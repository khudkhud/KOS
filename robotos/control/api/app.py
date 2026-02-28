from __future__ import annotations

from typing import Any, Dict

from robotos.control.session.service import SessionService


class ControlAPI:
    """Minimal API facade mirroring HTTP endpoints."""

    def __init__(self, sessions: SessionService) -> None:
        self.sessions = sessions

    def post_sessions(self, body: Dict[str, Any]) -> Dict[str, Any]:
        s = self.sessions.create(
            owner=body.get("owner", "app"),
            capabilities=body.get("capabilities", []),
            risk_class=body.get("risk_class", "SAFE"),
            priority=body.get("priority", 0),
        )
        return {"session_id": s.session_id}

    def post_submit_intent(self, session_id: str, body: Dict[str, Any]) -> Dict[str, str]:
        self.sessions.submit_intent(session_id, body)
        return {"status": "ok"}

    def post_cancel(self, session_id: str) -> Dict[str, str]:
        self.sessions.cancel(session_id)
        return {"status": "ok"}

    def post_pause(self, session_id: str) -> Dict[str, str]:
        self.sessions.pause(session_id)
        return {"status": "ok"}

    def post_resume(self, session_id: str) -> Dict[str, str]:
        self.sessions.resume(session_id)
        return {"status": "ok"}

    def post_preempt(self, body: Dict[str, Any]) -> Dict[str, str]:
        self.sessions.preempt(
            low_session_id=body["low_session_id"],
            high_session_id=body["high_session_id"],
            mode=body.get("mode", "PAUSE"),
        )
        return {"status": "ok"}

    def get_session(self, session_id: str) -> Dict[str, Any]:
        return self.sessions.get(session_id)
