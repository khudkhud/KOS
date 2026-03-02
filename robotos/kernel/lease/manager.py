"""Lease manager for shared robot resources.

Implements acquire/release/preempt and mirrors changes into OSM events.
"""

from __future__ import annotations

from typing import Dict, Iterable, List

from robotos.kernel.osm.store import OSMStore
from robotos.models import Lease, OSMEvent, new_id, now_ms


class LeaseManager:
    def __init__(self, osm: OSMStore) -> None:
        self.osm = osm
        self.by_resource: Dict[str, str] = {}

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
            self.osm.append_event(OSMEvent(type="LEASE_ACQUIRED", session_id=session_id, payload={"resource": res, "lease_id": lease.lease_id, "ttl_ms": ttl_ms, "expires_at": lease.expires_at}))
            leases.append(lease)
        return leases

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
