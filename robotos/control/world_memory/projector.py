"""Projection pipeline from OSM event log into world memory views."""

from __future__ import annotations

from typing import Dict, List

from robotos.control.memory.store import MemoryStore


class OSMWorldProjector:
    def __init__(self, memory: MemoryStore) -> None:
        self.memory = memory

    def project(self, events: List[Dict[str, object]]) -> Dict[str, object]:
        session_summary: Dict[str, object] = {}
        latest_risks: Dict[str, object] = {}
        for e in events:
            et = str(e.get("type", ""))
            sid = str(e.get("session_id", ""))
            payload = e.get("payload", {})
            if sid and sid not in session_summary:
                session_summary[sid] = {"last_event": et}
            if et == "SESSION_STATE_CHANGED" and sid:
                session_summary[sid] = payload
            if et == "ACTION_RESULT" and sid:
                latest_risks[sid] = {
                    "status": getattr(payload, "get", lambda *_: "")("status"),
                    "error_code": getattr(payload, "get", lambda *_: "")("error_code"),
                }
            if et == "EXPLAIN_TRACE":
                self.memory.write_world_fact("last_explain_trace", payload if isinstance(payload, dict) else {})
        self.memory.write_world_fact("session_summary", session_summary)
        self.memory.write_world_fact("latest_risks", latest_risks)
        return {
            "session_count": len(session_summary),
            "risk_count": len(latest_risks),
        }
