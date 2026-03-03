"""Session governance service.

Owns session lifecycle mutations and emits corresponding OSM events and
message-stream requests/events.
"""

from __future__ import annotations

from typing import Any, Dict

from robotos.control.admission import AdmissionBudget
from robotos.control.message.stream import MessageStream
from robotos.kernel.osm.store import OSMStore
from robotos.kernel.error_codes import ERR_ADMISSION_REJECTED
from robotos.models import Message, OSMEvent, Session, SessionState, new_id, now_ms


class SessionService:
    """Owns CRUD-style session governance and event emission."""

    def __init__(self, osm: OSMStore, stream: MessageStream, admission: AdmissionBudget | None = None) -> None:
        self.osm = osm
        self.stream = stream
        self.admission = admission

    def _audit(self, action: str, actor: str, details: Dict[str, Any]) -> None:
        """Emit an auditable governance decision record."""
        self.stream.publish_governance(
            decision_type="Decision",
            topic=f"AUDIT_{action.upper()}",
            session_id=str(details.get("session_id") or details.get("low_session_id") or "GLOBAL"),
            proposer=actor,
            approver=actor,
            executor="control_api",
            rollback_owner="control_api",
            reason=f"{action} requested by {actor}",
            sender="control",
        )
        self.osm.append_event(
            OSMEvent(
                type="CONTROL_AUDIT",
                session_id=str(details.get("session_id") or details.get("low_session_id") or "GLOBAL"),
                payload={"action": action, "actor": actor, "details": details, "ts": now_ms()},
            )
        )

    def create(
        self,
        owner: str,
        capabilities: list[str],
        risk_class: str = "SAFE",
        priority: int = 0,
        preemption_policy: str = "ALLOW",
        actor: str = "system",
    ) -> Session:
        """Create a new session and publish SESSION_CREATED."""
        s = Session(
            session_id=new_id("S"),
            owner=owner,
            capabilities=capabilities,
            risk_class=risk_class,
            priority=priority,
            preemption_policy=preemption_policy,
        )
        self.osm.apply_patch({"type": "session_upsert", "session": s})
        self.osm.append_event(
            OSMEvent(
                type="SESSION_CREATED",
                session_id=s.session_id,
                payload={
                    "owner": owner,
                    "capabilities": capabilities,
                    "risk_class": risk_class,
                    "priority": priority,
                    "preemption_policy": preemption_policy,
                },
            )
        )
        self.stream.publish(
            Message(type="Event", topic="SESSION_CREATED", session_id=s.session_id, payload={"owner": owner}),
            sender="control",
        )
        self._audit("create_session", actor, {"session_id": s.session_id, "owner": owner})
        return s

    def submit_intent(self, session_id: str, intent: Dict[str, Any], actor: str = "system") -> None:
        """Attach user intent to session and enqueue planning request."""
        if self.admission is not None:
            decision = self.admission.evaluate(intent)
            if not decision.accepted:
                detail = decision.detail or {}
                self.osm.append_event(
                    OSMEvent(
                        type="ADMISSION_REJECTED",
                        session_id=session_id,
                        payload={"code": decision.code or ERR_ADMISSION_REJECTED, "reason": decision.reason, **detail},
                    )
                )
                self.osm.apply_patch(
                    {
                        "type": "request_enqueue",
                        "request": {
                            "topic": "REQ_REPLAN",
                            "session_id": session_id,
                            "code": decision.code or ERR_ADMISSION_REJECTED,
                            "reason": decision.reason,
                            **detail,
                        },
                    }
                )
                self.stream.publish(
                    Message(
                        type="Decision",
                        topic="SUGGEST_REPLAN",
                        session_id=session_id,
                        payload={"code": decision.code or ERR_ADMISSION_REJECTED, "reason": decision.reason, **detail},
                    ),
                    sender="control",
                )
                self._audit("submit_intent_rejected", actor, {"session_id": session_id, "reason": decision.reason})
                return

        self.osm.apply_patch({"type": "intent_enqueue", "intent": {"session_id": session_id, "intent": intent}})
        self.osm.append_event(OSMEvent(type="INTENT_SUBMITTED", session_id=session_id, payload=intent))
        self.stream.publish(Message(type="Request", topic="REQ_PLAN", session_id=session_id, payload=intent), sender="control")
        self._audit("submit_intent", actor, {"session_id": session_id})

    def cancel(self, session_id: str, actor: str = "system") -> None:
        """Move session to CANCELING; kernel completes convergence."""
        session = self.osm.session_projection.get(session_id)
        if not session:
            raise KeyError(f"unknown session: {session_id}")
        if session.state in {SessionState.CANCELING, SessionState.CANCELED, SessionState.SUCCEEDED, SessionState.FAILED}:
            self._audit("cancel_ignored", actor, {"session_id": session_id, "state": session.state.value})
            return
        self.osm.apply_patch({"type": "session_state", "session_id": session_id, "state": SessionState.CANCELING.value})
        self.osm.append_event(OSMEvent(type="SESSION_STATE_CHANGED", session_id=session_id, payload={"state": "CANCELING"}))
        self.stream.publish(Message(type="Request", topic="REQ_CANCEL", session_id=session_id, payload={}), sender="control")
        self._audit("cancel", actor, {"session_id": session_id})

    def pause(self, session_id: str, actor: str = "system") -> None:
        self.osm.apply_patch({"type": "session_state", "session_id": session_id, "state": SessionState.PAUSED.value})
        self._audit("pause", actor, {"session_id": session_id})

    def resume(self, session_id: str, actor: str = "system") -> None:
        self.osm.apply_patch({"type": "session_state", "session_id": session_id, "state": SessionState.EXECUTING.value})
        self._audit("resume", actor, {"session_id": session_id})

    def preempt(self, low_session_id: str, high_session_id: str, mode: str = "PAUSE", actor: str = "system") -> None:
        """Control-plane preempt state machine with compensation on failure."""
        low = self.osm.session_projection.get(low_session_id)
        high = self.osm.session_projection.get(high_session_id)
        if not low or not high:
            raise KeyError("preempt requires both low_session and high_session")

        low_target = SessionState.PAUSED if mode.upper() == "PAUSE" else SessionState.CANCELING
        prev_low, prev_high = low.state, high.state
        self.osm.append_event(
            OSMEvent(
                type="PREEMPT_TXN_STARTED",
                session_id=low_session_id,
                payload={"high_session_id": high_session_id, "mode": mode, "actor": actor},
            )
        )
        try:
            self.osm.apply_patch({"type": "session_state", "session_id": low_session_id, "state": low_target.value})
            self.osm.apply_patch({"type": "session_state", "session_id": high_session_id, "state": SessionState.EXECUTING.value})
            self.osm.append_event(
                OSMEvent(
                    type="PREEMPT_TXN_COMMITTED",
                    session_id=high_session_id,
                    payload={"low_session_id": low_session_id, "low_state": low_target.value},
                )
            )
            self._audit(
                "preempt",
                actor,
                {"low_session_id": low_session_id, "high_session_id": high_session_id, "mode": mode},
            )
        except Exception as exc:
            self.osm.apply_patch({"type": "session_state", "session_id": low_session_id, "state": prev_low.value})
            self.osm.apply_patch({"type": "session_state", "session_id": high_session_id, "state": prev_high.value})
            self.osm.append_event(
                OSMEvent(
                    type="PREEMPT_TXN_ROLLED_BACK",
                    session_id=low_session_id,
                    payload={"high_session_id": high_session_id, "error": str(exc)},
                )
            )
            raise

    def get(self, session_id: str) -> Dict[str, Any]:
        return self.osm.get()["session_projection"][session_id]
