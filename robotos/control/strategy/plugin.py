from __future__ import annotations

from typing import Callable

from robotos.control.message.stream import MessageStream
from robotos.models import Message


class StrategyPlugin:
    def __init__(self, stream: MessageStream, on_replan: Callable[[str], None]) -> None:
        self.stream = stream
        self.on_replan = on_replan
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
