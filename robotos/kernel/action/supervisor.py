from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from robotos.kernel.action.dds import ActionHeader, Cancel, DDSActionClient, Goal, result_to_action_result
from robotos.kernel.osm.store import OSMStore
from robotos.models import ActionResult, OSMEvent, new_id, now_ms


@dataclass
class ActionHandle:
    action_id: str
    session_id: str
    tool: str
    args: Dict[str, Any]
    action_epoch: int
    state: str = "GOAL_SENT"
    started_at: int = 0


class ActionSupervisor:
    def __init__(self, osm: OSMStore, dds_client: DDSActionClient) -> None:
        self.osm = osm
        self.dds_client = dds_client
        self.active: Dict[str, ActionHandle] = {}
        self.session_epoch: Dict[str, int] = {}

    def next_epoch(self, session_id: str) -> int:
        self.session_epoch[session_id] = self.session_epoch.get(session_id, 0) + 1
        return self.session_epoch[session_id]

    def current_epoch(self, session_id: str) -> int:
        return self.session_epoch.get(session_id, self.osm.session_projection.get(session_id).action_epoch if session_id in self.osm.session_projection else 0)

    def send_goal(self, session_id: str, tool: str, args: Dict[str, Any], plan_id: str = "", trace_id: str = "") -> ActionHandle:
        epoch = self.current_epoch(session_id)
        action_id = new_id("A")
        h = ActionHandle(action_id=action_id, session_id=session_id, tool=tool, args=args, action_epoch=epoch, started_at=now_ms())
        self.active[action_id] = h
        goal = Goal(
            hdr=ActionHeader(session_id=session_id, plan_id=plan_id, action_id=action_id, trace_id=trace_id, action_epoch=epoch, osm_version_hint=self.osm.version),
            tool=tool,
            json_args=args,
        )
        self.dds_client.send_goal(goal)
        self.osm.append_event(OSMEvent(type="ACTION_GOAL_SENT", session_id=session_id, action_id=action_id, payload={"tool": tool, "args": args, "action_epoch": epoch}))
        self.osm.apply_patch({"type": "action_update", "action_id": action_id, "data": {"state": "RUNNING", "tool": tool, "session_id": session_id, "action_epoch": epoch}})
        return h

    def poll(self, action_id: str) -> Optional[ActionResult]:
        handle = self.active.get(action_id)
        if not handle:
            return None
        fb = self.dds_client.poll_feedback(action_id)
        if fb:
            if fb.hdr.action_epoch == self.current_epoch(handle.session_id):
                self.osm.append_event(OSMEvent(type="ACTION_FEEDBACK", session_id=handle.session_id, action_id=action_id, payload={"progress": fb.progress, "status": fb.status_msg, "action_epoch": fb.hdr.action_epoch}))

        result = self.dds_client.poll_result(action_id)
        if result:
            if result.hdr.action_epoch != self.current_epoch(handle.session_id):
                self.active.pop(action_id, None)
                self.osm.append_event(OSMEvent(type="ACTION_RESULT_IGNORED", session_id=handle.session_id, action_id=action_id, payload={"reason": "stale_epoch", "action_epoch": result.hdr.action_epoch}))
                return None
            ar = result_to_action_result(result)
            self.osm.append_event(OSMEvent(type="ACTION_RESULT", session_id=handle.session_id, action_id=action_id, payload={"status": ar.status, "error_code": ar.error_code, "action_epoch": result.hdr.action_epoch}))
            self.osm.apply_patch({"type": "action_update", "action_id": action_id, "data": {"state": ar.status, "tool": handle.tool, "session_id": handle.session_id, "action_epoch": result.hdr.action_epoch}})
            self.active.pop(action_id, None)
            return ar
        return None

    def cancel(self, action_id: str, reason: str = "cancel") -> Optional[ActionResult]:
        handle = self.active.get(action_id)
        if not handle:
            return None
        cancel = Cancel(
            hdr=ActionHeader(session_id=handle.session_id, plan_id="", action_id=action_id, trace_id="", action_epoch=handle.action_epoch, osm_version_hint=self.osm.version),
            reason=reason,
        )
        self.dds_client.cancel(handle.tool, cancel)
        self.osm.append_event(OSMEvent(type="ACTION_CANCELED", session_id=handle.session_id, action_id=action_id, payload={"reason": reason, "action_epoch": handle.action_epoch}))
        return None

    def cancel_session(self, session_id: str, reason: str = "session_cancel") -> None:
        for aid, handle in list(self.active.items()):
            if handle.session_id == session_id:
                self.cancel(aid, reason=reason)
                self.active.pop(aid, None)
