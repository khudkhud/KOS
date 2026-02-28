"""OSM store: event log + projections + replay/rebuild.

Provides append-only event persistence, in-memory projections, and reducer-
based rebuild from persisted logs.
"""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Dict, List, Optional

from robotos.models import Lease, OSMEvent, Session, SessionState
from robotos.schema_validate import validate


Watcher = Callable[[Dict[str, Any]], None]


class OSMStore:
    """Thread-safe in-memory projections with append-only event persistence."""

    def __init__(self, persist_path: str | None = None) -> None:
        self.version = 0
        self.event_log: List[OSMEvent] = []
        self.session_projection: Dict[str, Session] = {}
        self.action_projection: Dict[str, Dict[str, Any]] = {}
        self.lease_projection: Dict[str, Lease] = {}
        self.intent_queue: List[Dict[str, Any]] = []
        self.request_queue: List[Dict[str, Any]] = []
        self._watchers: Dict[str, List[Watcher]] = {}
        self._lock = RLock()
        self.persist_path = Path(persist_path) if persist_path else None
        if self.persist_path and self.persist_path.exists():
            self.replay_from_file(str(self.persist_path), rebuild=True)

    def get(self, version: Optional[int] = None) -> Dict[str, Any]:
        _ = version
        with self._lock:
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
        payload = asdict(e)
        validate(payload, "osm_event.schema.json")
        with self._lock:
            self.event_log.append(e)
            self.version += 1
            if self.persist_path:
                with self.persist_path.open("a", encoding="utf-8") as fp:
                    fp.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return self.version

    def apply_patch(self, patch: Dict[str, Any]) -> int:
        with self._lock:
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

    def replay_from_file(self, path: str, rebuild: bool = False) -> None:
        """Load persisted JSONL events; optionally rebuild projections via reducer."""
        p = Path(path)
        if not p.exists():
            return
        with self._lock:
            self.event_log = []
            self.version = 0
            if rebuild:
                self._reset_projections()
            with p.open("r", encoding="utf-8") as fp:
                for line in fp:
                    line = line.strip()
                    if not line:
                        continue
                    raw = json.loads(line)
                    evt = OSMEvent(
                        type=raw["type"],
                        payload=raw["payload"],
                        session_id=raw.get("session_id"),
                        plan_id=raw.get("plan_id"),
                        action_id=raw.get("action_id"),
                        event_id=raw["event_id"],
                        ts=raw["ts"],
                    )
                    self.event_log.append(evt)
                    self.version += 1
                    if rebuild:
                        self._reduce_event(evt)

    def rebuild_projections_from_events(self) -> None:
        with self._lock:
            self._reset_projections()
            for evt in self.event_log:
                self._reduce_event(evt)

    def _reduce_event(self, evt: OSMEvent) -> None:
        """Projection reducer: apply one event to read models."""
        payload = evt.payload
        if evt.type == "SESSION_CREATED" and evt.session_id:
            self.session_projection[evt.session_id] = Session(
                session_id=evt.session_id,
                owner=payload.get("owner", "unknown"),
                priority=payload.get("priority", 0),
                capabilities=payload.get("capabilities", []),
                risk_class=payload.get("risk_class", "SAFE"),
                preemption_policy=payload.get("preemption_policy", "ALLOW"),
                state=SessionState.CREATED,
            )
        elif evt.type == "SESSION_STATE_CHANGED" and evt.session_id and evt.session_id in self.session_projection:
            state = payload.get("state")
            if state:
                self.session_projection[evt.session_id].state = SessionState(state)
        elif evt.type in {"ACTION_GOAL_SENT", "ACTION_FEEDBACK", "ACTION_RESULT", "ACTION_CANCELED"} and evt.action_id:
            data = self.action_projection.get(evt.action_id, {})
            if evt.type == "ACTION_GOAL_SENT":
                data.update({"state": "RUNNING", "tool": payload.get("tool"), "session_id": evt.session_id, "action_epoch": payload.get("action_epoch", 0)})
            elif evt.type == "ACTION_RESULT":
                data.update({"state": payload.get("status"), "error_code": payload.get("error_code", ""), "action_epoch": payload.get("action_epoch", 0)})
            elif evt.type == "ACTION_CANCELED":
                data.update({"state": "CANCELED", "action_epoch": payload.get("action_epoch", 0)})
            self.action_projection[evt.action_id] = data
        elif evt.type == "LEASE_ACQUIRED":
            lid = payload.get("lease_id")
            if lid:
                self.lease_projection[lid] = Lease(
                    lease_id=lid,
                    resource=payload.get("resource", ""),
                    owner_session=evt.session_id or "",
                    ttl_ms=payload.get("ttl_ms", 5000),
                    expires_at=payload.get("expires_at", 0),
                    state="HELD",
                )
        elif evt.type == "LEASE_RELEASED":
            lid = payload.get("lease_id")
            if lid and lid in self.lease_projection:
                self.lease_projection[lid].state = "RELEASING"

    def _reset_projections(self) -> None:
        self.session_projection = {}
        self.action_projection = {}
        self.lease_projection = {}
        self.intent_queue = []
        self.request_queue = []

    def _emit(self, query: str) -> None:
        payload = self.get()
        for cb in self._watchers.get(query, []):
            cb(payload)
