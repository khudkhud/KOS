from __future__ import annotations

from typing import Callable, Optional

from robotos.control.message.stream import MessageStream
from robotos.models import Message


class StrategyPlugin:
    def __init__(
        self,
        stream: MessageStream,
        on_replan: Callable[[str], None],
        on_cancel: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self.stream = stream
        self.on_replan = on_replan
        self.on_cancel = on_cancel
        stream.subscribe("*", self.on_message)

    def on_message(self, msg: Message) -> None:
        if msg.type == "Event" and msg.topic in {"LOW_BATTERY", "NAV_STUCK"} and msg.session_id:
            self.stream.publish(
                Message(
                    type="Proposal",
                    topic="SUGGEST_REPLAN",
                    session_id=msg.session_id,
                    trace_id=msg.trace_id,
                    payload={"reason": msg.topic},
                )
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
                    )
                )
                if self.on_cancel:
                    self.on_cancel(msg.session_id, "TARGET_GONE")
