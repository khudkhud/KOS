"""Session governance service.

Owns session lifecycle mutations and emits corresponding OSM events and
message-stream requests/events.
"""

from __future__ import annotations

from typing import Any, Dict

from robotos.control.message.stream import MessageStream
from robotos.kernel.osm.store import OSMStore
from robotos.models import Message, OSMEvent, Session, SessionState, new_id


class SessionService:
    """Owns CRUD-style session governance and event emission."""

    def __init__(self, osm: OSMStore, stream: MessageStream) -> None:
        self.osm = osm
        self.stream = stream

    def create(self, owner: str, capabilities: list[str], risk_class: str = "SAFE", priority: int = 0, preemption_policy: str = "ALLOW") -> Session:
        """Create a new session and publish SESSION_CREATED."""
        s = Session(session_id=new_id("S"), owner=owner, capabilities=capabilities, risk_class=risk_class, priority=priority, preemption_policy=preemption_policy)
        self.osm.apply_patch({"type": "session_upsert", "session": s})
        self.osm.append_event(OSMEvent(type="SESSION_CREATED", session_id=s.session_id, payload={"owner": owner, "capabilities": capabilities, "risk_class": risk_class, "priority": priority, "preemption_policy": preemption_policy}))
        self.stream.publish(Message(type="Event", topic="SESSION_CREATED", session_id=s.session_id, payload={"owner": owner}), sender="control")
        return s

    def submit_intent(self, session_id: str, intent: Dict[str, Any]) -> None:
        """Attach user intent to session and enqueue planning request."""
        self.osm.apply_patch({"type": "intent_enqueue", "intent": {"session_id": session_id, "intent": intent}})
        self.osm.append_event(OSMEvent(type="INTENT_SUBMITTED", session_id=session_id, payload=intent))
        self.stream.publish(Message(type="Request", topic="REQ_PLAN", session_id=session_id, payload=intent), sender="control")

    def cancel(self, session_id: str) -> None:
        """Move session to CANCELING; kernel completes convergence."""
        self.osm.apply_patch({"type": "session_state", "session_id": session_id, "state": SessionState.CANCELING.value})
        self.osm.append_event(OSMEvent(type="SESSION_STATE_CHANGED", session_id=session_id, payload={"state": "CANCELING"}))
        self.stream.publish(Message(type="Request", topic="REQ_CANCEL", session_id=session_id, payload={}), sender="control")

    def pause(self, session_id: str) -> None:
        self.osm.apply_patch({"type": "session_state", "session_id": session_id, "state": SessionState.PAUSED.value})

    def resume(self, session_id: str) -> None:
        self.osm.apply_patch({"type": "session_state", "session_id": session_id, "state": SessionState.EXECUTING.value})


    def preempt(self, low_session_id: str, high_session_id: str, mode: str = "PAUSE") -> None:
        """Control-plane preempt marker path (state + event updates)."""
        low_state = "PAUSED" if mode.upper() == "PAUSE" else "CANCELING"
        self.osm.apply_patch({"type": "session_state", "session_id": low_session_id, "state": low_state})
        self.osm.apply_patch({"type": "session_state", "session_id": high_session_id, "state": SessionState.EXECUTING.value})
        self.osm.append_event(OSMEvent(type="SESSION_STATE_CHANGED", session_id=low_session_id, payload={"state": low_state, "reason": "preempt"}))
        self.osm.append_event(OSMEvent(type="SESSION_STATE_CHANGED", session_id=high_session_id, payload={"state": "EXECUTING", "reason": "preempt"}))

    def get(self, session_id: str) -> Dict[str, Any]:
        return self.osm.get()["session_projection"][session_id]
