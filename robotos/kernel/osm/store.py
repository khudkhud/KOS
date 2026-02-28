from __future__ import annotations

from dataclasses import asdict
from typing import Any, Callable, Dict, List, Optional

from robotos.models import Lease, OSMEvent, Session, SessionState


Watcher = Callable[[Dict[str, Any]], None]


class OSMStore:
    def __init__(self) -> None:
        self.version = 0
        self.event_log: List[OSMEvent] = []
        self.session_projection: Dict[str, Session] = {}
        self.action_projection: Dict[str, Dict[str, Any]] = {}
        self.lease_projection: Dict[str, Lease] = {}
        self.intent_queue: List[Dict[str, Any]] = []
        self.request_queue: List[Dict[str, Any]] = []
        self._watchers: Dict[str, List[Watcher]] = {}

    def get(self, version: Optional[int] = None) -> Dict[str, Any]:
        _ = version
        return {
            "version": self.version,
            "session_projection": {k: asdict(v) for k, v in self.session_projection.items()},
            "action_projection": self.action_projection.copy(),
            "lease_projection": {k: asdict(v) for k, v in self.lease_projection.items()},
            "intent_queue": list(self.intent_queue),
            "request_queue": list(self.request_queue),
        }

    def watch(self, query: str, cb: Watcher) -> None:
        self._watchers.setdefault(query, []).append(cb)

    def append_event(self, e: OSMEvent) -> int:
        self.event_log.append(e)
        self.version += 1
        return self.version

    def apply_patch(self, patch: Dict[str, Any]) -> int:
        self.version += 1
        ptype = patch.get("type")
        if ptype == "session_upsert":
            self.session_projection[patch["session"].session_id] = patch["session"]
            self._emit("session_projection")
        elif ptype == "session_state":
            sid = patch["session_id"]
            self.session_projection[sid].state = SessionState(patch["state"])
            if "last_error" in patch:
                self.session_projection[sid].last_error = patch["last_error"]
            self._emit("session_projection")
        elif ptype == "action_update":
            self.action_projection[patch["action_id"]] = patch["data"]
            self._emit("action_projection")
        elif ptype == "lease_upsert":
            self.lease_projection[patch["lease"].lease_id] = patch["lease"]
            self._emit("lease_projection")
        elif ptype == "lease_release":
            lid = patch["lease_id"]
            if lid in self.lease_projection:
                self.lease_projection[lid].state = "RELEASING"
            self._emit("lease_projection")
        elif ptype == "intent_enqueue":
            self.intent_queue.append(patch["intent"])
        elif ptype == "request_enqueue":
            self.request_queue.append(patch["request"])
        return self.version

    def _emit(self, query: str) -> None:
        payload = self.get()
        for cb in self._watchers.get(query, []):
            cb(payload)
