"""Strategy plugin for system-level supervision decisions.

Consumes message stream events and emits governance proposals such as
replan/cancel suggestions. This keeps policy triggers outside executor logic.
"""

from __future__ import annotations

from typing import Callable, Optional

from robotos.control.message.stream import MessageStream
from robotos.models import Message


class StrategyPlugin:
    """Translate observed events into governance actions (replan/cancel)."""

    def __init__(
        self,
        stream: MessageStream,
        on_replan: Callable[[str], None],
        on_cancel: Optional[Callable[[str, str], None]] = None,
        agent_id: str = "strategy",
    ) -> None:
        self.stream = stream
        self.on_replan = on_replan
        self.on_cancel = on_cancel
        self.agent_id = agent_id
        stream.subscribe("*", self.on_message, agent_id=agent_id)

    def on_message(self, msg: Message) -> None:
        """Handle stream messages and trigger strategy callbacks when matched."""
        if msg.type == "Event" and msg.topic in {"LOW_BATTERY", "NAV_STUCK"} and msg.session_id:
            self.stream.publish(
                Message(
                    type="Proposal",
                    topic="SUGGEST_REPLAN",
                    session_id=msg.session_id,
                    trace_id=msg.trace_id,
                    payload={"reason": msg.topic},
                ),
                sender=self.agent_id,
            )
            self.on_replan(msg.session_id)
            return

        if msg.type == "Event" and msg.topic == "TARGET_GONE" and msg.session_id:
            payload = msg.payload or {}
            target = str(payload.get("target", "")).lower()
            confidence = float(payload.get("confidence", 0.0))
            source = str(payload.get("source", "")).lower()

            trusted_sources = {"mother", "father", "guardian", "family_member"}
            should_cancel = target in {"son", "child"} and confidence >= 0.7 and source in trusted_sources
            if should_cancel:
                self.stream.publish(
                    Message(
                        type="Proposal",
                        topic="SUGGEST_CANCEL",
                        session_id=msg.session_id,
                        trace_id=msg.trace_id,
                        payload={"reason": "TARGET_GONE", "source": source, "confidence": confidence},
                    ),
                    sender=self.agent_id,
                )
                if self.on_cancel:
                    self.on_cancel(msg.session_id, "TARGET_GONE")
