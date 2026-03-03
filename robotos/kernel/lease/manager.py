"""Lease manager for shared robot resources.

Implements acquire/release/preempt and mirrors changes into OSM events.
Adds guardian helpers for TTL sweep/renew and restart recovery.
"""

from __future__ import annotations

from typing import Dict, Iterable, List

from robotos.kernel.osm.store import OSMStore
from robotos.models import Lease, OSMEvent, new_id, now_ms


class LeaseManager:
    def __init__(self, osm: OSMStore) -> None:
        self.osm = osm
        self.by_resource: Dict[str, str] = {}
        self.rebuild_index_from_projection()

    def rebuild_index_from_projection(self) -> int:
        """Rebuild in-memory index from persisted projection (restart safety)."""
        rebuilt = 0
        self.by_resource.clear()
        for lid, lease in self.osm.lease_projection.items():
            if lease.state == "HELD" and lease.expires_at > now_ms():
                self.by_resource[lease.resource] = lid
                rebuilt += 1
        return rebuilt

    def acquire(self, resources: Iterable[str], session_id: str, ttl_ms: int) -> List[Lease]:
        leases: List[Lease] = []
        for res in resources:
            current = self.by_resource.get(res)
            if current:
                owner = self.osm.lease_projection[current].owner_session
                if owner != session_id:
                    raise RuntimeError(f"resource busy: {res} owned by {owner}")
            lease = Lease(
                lease_id=new_id("L"),
                resource=res,
                owner_session=session_id,
                ttl_ms=ttl_ms,
                expires_at=now_ms() + ttl_ms,
            )
            self.by_resource[res] = lease.lease_id
            self.osm.apply_patch({"type": "lease_upsert", "lease": lease})
            self.osm.append_event(
                OSMEvent(
                    type="LEASE_ACQUIRED",
                    session_id=session_id,
                    payload={"resource": res, "lease_id": lease.lease_id, "ttl_ms": ttl_ms, "expires_at": lease.expires_at},
                )
            )
            leases.append(lease)
        return leases

    def renew(self, lease_id: str, ttl_ms: int | None = None) -> Lease:
        lease = self.osm.lease_projection.get(lease_id)
        if not lease or lease.state != "HELD":
            raise KeyError(f"cannot renew unknown lease: {lease_id}")
        ttl = ttl_ms if ttl_ms is not None else lease.ttl_ms
        lease.ttl_ms = ttl
        lease.expires_at = now_ms() + ttl
        self.osm.apply_patch({"type": "lease_upsert", "lease": lease})
        self.osm.append_event(
            OSMEvent(
                type="LEASE_RENEWED",
                session_id=lease.owner_session,
                payload={"resource": lease.resource, "lease_id": lease_id, "ttl_ms": ttl, "expires_at": lease.expires_at},
            )
        )
        return lease

    def sweep_expired(self, now: int | None = None) -> int:
        """Guardian sweep to release expired leases and avoid dead resources."""
        ts = now if now is not None else now_ms()
        expired_ids = [lid for lid, lease in self.osm.lease_projection.items() if lease.state == "HELD" and lease.expires_at <= ts]
        for lid in expired_ids:
            lease = self.osm.lease_projection.get(lid)
            if lease:
                self.osm.append_event(
                    OSMEvent(
                        type="LEASE_EXPIRED",
                        session_id=lease.owner_session,
                        payload={"resource": lease.resource, "lease_id": lid, "expired_at": ts},
                    )
                )
            self.release(lid)
        return len(expired_ids)

    def release(self, lease_id: str) -> None:
        lease = self.osm.lease_projection.get(lease_id)
        if not lease:
            return
        self.osm.apply_patch({"type": "lease_release", "lease_id": lease_id})
        if self.by_resource.get(lease.resource) == lease_id:
            del self.by_resource[lease.resource]
        self.osm.append_event(OSMEvent(type="LEASE_RELEASED", session_id=lease.owner_session, payload={"resource": lease.resource, "lease_id": lease_id}))

    def preempt(self, resource: str, new_session_id: str) -> None:
        old = self.by_resource.get(resource)
        if not old:
            return
        self.release(old)
        self.osm.append_event(OSMEvent(type="LEASE_PREEMPTED", session_id=new_session_id, payload={"resource": resource}))
