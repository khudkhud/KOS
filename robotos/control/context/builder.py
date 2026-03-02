from __future__ import annotations

from typing import Any, Dict

from robotos.kernel.osm.store import OSMStore


class ContextBuilder:
    def __init__(self, osm: OSMStore) -> None:
        self.osm = osm

    def build(self, session_id: str, intent: Dict[str, Any], constraints: Dict[str, Any]) -> Dict[str, Any]:
        snap = self.osm.get()
        return {
            "session_id": session_id,
            "intent": intent,
            "osm_snapshot_version": snap["version"],
            "world_summary": {
                "active_session": session_id,
                "leases": list(snap["lease_projection"].values()),
                "last_failures": [e.payload for e in self.osm.event_log if e.type == "ACTION_RESULT" and e.payload.get("status") == "FAILED"][-3:],
            },
            "constraints": constraints,
        }
