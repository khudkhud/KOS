from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from robotos.models import ActionResult, OSMEvent, new_id, now_ms
from robotos.kernel.osm.store import OSMStore


class Skill:
    def start(self, action_id: str, tool: str, args: Dict[str, Any]) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def step(self, action_id: str, now: int) -> Optional[ActionResult]:  # pragma: no cover - interface
        raise NotImplementedError

    def cancel(self, action_id: str) -> ActionResult:  # pragma: no cover - interface
        raise NotImplementedError


@dataclass
class ActionHandle:
    action_id: str
    session_id: str
    tool: str
    args: Dict[str, Any]
    state: str = "GOAL_SENT"
    started_at: int = 0


class ActionSupervisor:
    def __init__(self, osm: OSMStore, skill_router: Dict[str, Skill]) -> None:
        self.osm = osm
        self.skill_router = skill_router
        self.active: Dict[str, ActionHandle] = {}

    def send_goal(self, session_id: str, tool: str, args: Dict[str, Any]) -> ActionHandle:
        action_id = new_id("A")
        h = ActionHandle(action_id=action_id, session_id=session_id, tool=tool, args=args, started_at=now_ms())
        self.active[action_id] = h
        self.skill_router[tool].start(action_id, tool, args)
        self.osm.append_event(OSMEvent(type="ACTION_GOAL_SENT", session_id=session_id, action_id=action_id, payload={"tool": tool, "args": args}))
        self.osm.apply_patch({"type": "action_update", "action_id": action_id, "data": {"state": "RUNNING", "tool": tool, "session_id": session_id}})
        return h

    def poll(self, action_id: str) -> Optional[ActionResult]:
        handle = self.active.get(action_id)
        if not handle:
            return None
        result = self.skill_router[handle.tool].step(action_id, now_ms())
        if result:
            handle.state = "TERMINAL"
            self.osm.append_event(OSMEvent(type="ACTION_RESULT", session_id=handle.session_id, action_id=action_id, payload={"status": result.status, "error_code": result.error_code}))
            self.osm.apply_patch({"type": "action_update", "action_id": action_id, "data": {"state": result.status, "tool": handle.tool, "session_id": handle.session_id}})
            self.active.pop(action_id, None)
        return result

    def cancel(self, action_id: str, reason: str = "cancel") -> Optional[ActionResult]:
        handle = self.active.get(action_id)
        if not handle:
            return None
        res = self.skill_router[handle.tool].cancel(action_id)
        self.osm.append_event(OSMEvent(type="ACTION_CANCELED", session_id=handle.session_id, action_id=action_id, payload={"reason": reason}))
        self.active.pop(action_id, None)
        return res
