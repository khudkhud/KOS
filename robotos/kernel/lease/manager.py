"""Lease manager for shared robot resources.

Implements acquire/release/preempt and mirrors changes into OSM events.
Adds guardian helpers for TTL sweep/renew and restart recovery.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, Iterable, List

from robotos.kernel.osm.store import OSMStore
from robotos.models import Lease, OSMEvent, new_id, now_ms


@dataclass(frozen=True)
class ResourceLeasePolicy:
    """Policy profile per resource class."""

    ttl_ms: int
    renew_interval_ms: int
    timeout_mode: str  # hard | soft
    on_expire: str  # fail | retry | degrade
    lease_required: bool = True
    exclusive: bool = True
    wait_soft_ms: int = 0
    wait_hard_ms: int = 0


DEFAULT_RESOURCE_POLICIES: Dict[str, ResourceLeasePolicy] = {
    # single HPU, strict exclusive ownership and hard timeout
    "hpu": ResourceLeasePolicy(ttl_ms=5_000, renew_interval_ms=1_500, timeout_mode="hard", on_expire="fail", lease_required=True, exclusive=True, wait_soft_ms=1_000, wait_hard_ms=1_000),
    # single mobile base(chassis), strict exclusive ownership and hard timeout
    "base": ResourceLeasePolicy(ttl_ms=2_000, renew_interval_ms=600, timeout_mode="hard", on_expire="fail", lease_required=True, exclusive=True, wait_soft_ms=0, wait_hard_ms=0),
    # camera is continuous 30fps service stream; tasks subscribe instead of owning it
    "camera": ResourceLeasePolicy(ttl_ms=0, renew_interval_ms=0, timeout_mode="soft", on_expire="retry", lease_required=False, exclusive=False, wait_soft_ms=0, wait_hard_ms=0),
    # microphone is typically an always-on capture stream consumed via subscriptions
    "mic": ResourceLeasePolicy(ttl_ms=0, renew_interval_ms=0, timeout_mode="soft", on_expire="retry", lease_required=False, exclusive=False, wait_soft_ms=0, wait_hard_ms=0),
    # speaker output is serialized to avoid overlapping TTS/playback
    "speaker": ResourceLeasePolicy(ttl_ms=1_500, renew_interval_ms=400, timeout_mode="hard", on_expire="fail", lease_required=True, exclusive=True, wait_soft_ms=0, wait_hard_ms=0),
}


class LeaseManager:
    def __init__(self, osm: OSMStore, resource_policies: Dict[str, ResourceLeasePolicy] | None = None) -> None:
        self.osm = osm
        self.by_resource: Dict[str, str] = {}
        self.resource_policies = {**DEFAULT_RESOURCE_POLICIES, **(resource_policies or {})}
        self.rebuild_index_from_projection()

    def policy_for(self, resource: str) -> ResourceLeasePolicy:
        return self.resource_policies.get(
            resource,
            ResourceLeasePolicy(ttl_ms=5_000, renew_interval_ms=1_500, timeout_mode="hard", on_expire="fail", lease_required=True, exclusive=True, wait_soft_ms=0, wait_hard_ms=0),
        )

    def rebuild_index_from_projection(self) -> int:
        """Rebuild in-memory index from persisted projection (restart safety)."""
        rebuilt = 0
        self.by_resource.clear()
        for lid, lease in self.osm.lease_projection.items():
            if lease.state == "HELD" and lease.expires_at > now_ms():
                policy = self.policy_for(lease.resource)
                if policy.lease_required:
                    self.by_resource[lease.resource] = lid
                    rebuilt += 1
        return rebuilt

    def acquire(self, resources: Iterable[str], session_id: str, ttl_ms: int | None = None) -> List[Lease]:
        leases: List[Lease] = []
        for res in resources:
            policy = self.policy_for(res)
            if not policy.lease_required:
                self.osm.append_event(
                    OSMEvent(
                        type="LEASE_BYPASSED",
                        session_id=session_id,
                        payload={"resource": res, "reason": "subscription_resource", "policy": asdict(policy)},
                    )
                )
                continue

            current = self.by_resource.get(res)
            if current and policy.exclusive:
                owner = self.osm.lease_projection[current].owner_session
                if owner != session_id:
                    if res == "base":
                        raise RuntimeError(f"BASE_BUSY: resource busy: {res} owned by {owner}")
                    if res == "speaker":
                        raise RuntimeError(f"SPEAKER_BUSY: resource busy: {res} owned by {owner}")
                    raise RuntimeError(f"resource busy: {res} owned by {owner}")

            final_ttl = int(ttl_ms if ttl_ms is not None else policy.ttl_ms)
            lease = Lease(
                lease_id=new_id("L"),
                resource=res,
                owner_session=session_id,
                ttl_ms=final_ttl,
                expires_at=now_ms() + final_ttl,
            )
            self.by_resource[res] = lease.lease_id
            self.osm.apply_patch({"type": "lease_upsert", "lease": lease})
            self.osm.append_event(
                OSMEvent(
                    type="LEASE_ACQUIRED",
                    session_id=session_id,
                    payload={
                        "resource": res,
                        "lease_id": lease.lease_id,
                        "ttl_ms": final_ttl,
                        "expires_at": lease.expires_at,
                        "policy": asdict(policy),
                    },
                )
            )
            leases.append(lease)
        return leases

    def renew(self, lease_id: str, ttl_ms: int | None = None) -> Lease:
        lease = self.osm.lease_projection.get(lease_id)
        if not lease or lease.state != "HELD":
            raise KeyError(f"cannot renew unknown lease: {lease_id}")

        policy = self.policy_for(lease.resource)
        ts = now_ms()
        if policy.timeout_mode == "hard" and ts > lease.expires_at:
            raise RuntimeError(f"lease {lease_id} already expired under hard timeout policy")

        ttl = int(ttl_ms if ttl_ms is not None else lease.ttl_ms)
        lease.ttl_ms = ttl
        lease.expires_at = ts + ttl
        self.osm.apply_patch({"type": "lease_upsert", "lease": lease})
        self.osm.append_event(
            OSMEvent(
                type="LEASE_RENEWED",
                session_id=lease.owner_session,
                payload={
                    "resource": lease.resource,
                    "lease_id": lease_id,
                    "ttl_ms": ttl,
                    "expires_at": lease.expires_at,
                    "renew_interval_ms": policy.renew_interval_ms,
                },
            )
        )
        return lease

    def sweep_expired(self, now: int | None = None) -> int:
        """Guardian sweep to release expired leases and emit expire action hints."""
        ts = now if now is not None else now_ms()
        expired_ids = [lid for lid, lease in self.osm.lease_projection.items() if lease.state == "HELD" and lease.expires_at <= ts]
        for lid in expired_ids:
            lease = self.osm.lease_projection.get(lid)
            if lease:
                policy = self.policy_for(lease.resource)
                self.osm.append_event(
                    OSMEvent(
                        type="LEASE_EXPIRED",
                        session_id=lease.owner_session,
                        payload={
                            "resource": lease.resource,
                            "lease_id": lid,
                            "expired_at": ts,
                            "timeout_mode": policy.timeout_mode,
                            "on_expire": policy.on_expire,
                        },
                    )
                )
                self.osm.apply_patch(
                    {
                        "type": "request_enqueue",
                        "request": {
                            "topic": f"LEASE_EXPIRE_{policy.on_expire.upper()}",
                            "session_id": lease.owner_session,
                            "resource": lease.resource,
                            "lease_id": lid,
                        },
                    }
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
