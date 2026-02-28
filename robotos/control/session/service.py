from __future__ import annotations

from typing import Any, Dict

from robotos.control.message.stream import MessageStream
from robotos.kernel.osm.store import OSMStore
from robotos.models import Message, OSMEvent, Session, SessionState, new_id


class SessionService:
    def __init__(self, osm: OSMStore, stream: MessageStream) -> None:
        self.osm = osm
        self.stream = stream

    def create(self, owner: str, capabilities: list[str], risk_class: str = "SAFE", priority: int = 0) -> Session:
        s = Session(session_id=new_id("S"), owner=owner, capabilities=capabilities, risk_class=risk_class, priority=priority)
        self.osm.apply_patch({"type": "session_upsert", "session": s})
        self.osm.append_event(OSMEvent(type="SESSION_CREATED", session_id=s.session_id, payload={"owner": owner}))
        self.stream.publish(Message(type="Event", topic="SESSION_CREATED", session_id=s.session_id, payload={"owner": owner}))
        return s

    def submit_intent(self, session_id: str, intent: Dict[str, Any]) -> None:
        self.osm.apply_patch({"type": "intent_enqueue", "intent": {"session_id": session_id, "intent": intent}})
        self.osm.append_event(OSMEvent(type="INTENT_SUBMITTED", session_id=session_id, payload=intent))
        self.stream.publish(Message(type="Request", topic="REQ_PLAN", session_id=session_id, payload=intent))

    def cancel(self, session_id: str) -> None:
        self.osm.apply_patch({"type": "session_state", "session_id": session_id, "state": SessionState.CANCELING.value})
        self.osm.append_event(OSMEvent(type="SESSION_STATE_CHANGED", session_id=session_id, payload={"state": "CANCELING"}))
        self.stream.publish(Message(type="Request", topic="REQ_CANCEL", session_id=session_id, payload={}))

    def pause(self, session_id: str) -> None:
        self.osm.apply_patch({"type": "session_state", "session_id": session_id, "state": SessionState.PAUSED.value})

    def resume(self, session_id: str) -> None:
        self.osm.apply_patch({"type": "session_state", "session_id": session_id, "state": SessionState.EXECUTING.value})

    def get(self, session_id: str) -> Dict[str, Any]:
        return self.osm.get()["session_projection"][session_id]
