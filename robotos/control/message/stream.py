"""In-memory causal message stream.

Carries Event/Request/Proposal envelopes for inter-agent coordination.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Callable, DefaultDict, List

from dataclasses import asdict

from robotos.models import Message
from robotos.schema_validate import validate


Subscriber = Callable[[Message], None]


class MessageStream:
    """In-memory pub/sub for Event/Request/Proposal causal traffic."""

    def __init__(self) -> None:
        self._subs: DefaultDict[str, List[Subscriber]] = defaultdict(list)
        self._all: List[Subscriber] = []

    def subscribe(self, topic: str, cb: Subscriber) -> None:
        if topic == "*":
            self._all.append(cb)
            return
        self._subs[topic].append(cb)

    def publish(self, msg: Message) -> None:
        validate(asdict(msg), "message.schema.json")
        for cb in self._all:
            cb(msg)
        for cb in self._subs.get(msg.topic, []):
            cb(msg)
