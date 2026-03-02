"""In-memory causal message stream.

Carries Event/Request/Proposal envelopes for inter-agent coordination.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Callable, DefaultDict, List, Optional

from dataclasses import asdict

from robotos.control.message.agents import AgentRegistry
from robotos.models import Message
from robotos.schema_validate import validate


Subscriber = Callable[[Message], None]


class MessageStream:
    """In-memory pub/sub for Event/Request/Proposal causal traffic."""

    def __init__(self, registry: Optional[AgentRegistry] = None) -> None:
        self._subs: DefaultDict[str, List[Subscriber]] = defaultdict(list)
        self._all: List[Subscriber] = []
        self.registry = registry
        self.history: List[dict] = []
        self.governance_log: List[dict] = []

    def subscribe(self, topic: str, cb: Subscriber, agent_id: Optional[str] = None) -> None:
        if agent_id and self.registry and not self.registry.can_subscribe(agent_id, topic):
            raise PermissionError(f"agent {agent_id} is not allowed to subscribe topic {topic}")
        if topic == "*":
            self._all.append(cb)
            return
        self._subs[topic].append(cb)


    def publish_governance(
        self,
        *,
        decision_type: str,
        topic: str,
        session_id: str,
        proposer: str,
        approver: str,
        executor: str,
        rollback_owner: str,
        reason: str,
        sender: Optional[str] = None,
    ) -> None:
        msg = Message(
            type=decision_type,
            topic=topic,
            session_id=session_id,
            payload={
                "reason": reason,
                "responsibility_chain": {
                    "proposer": proposer,
                    "approver": approver,
                    "executor": executor,
                    "rollback_owner": rollback_owner,
                },
            },
        )
        self.publish(msg, sender=sender)

    def publish(self, msg: Message, sender: Optional[str] = None) -> None:
        if sender and self.registry and not self.registry.can_publish(sender, msg):
            raise PermissionError(f"agent {sender} cannot publish message type {msg.type}")
        validate(asdict(msg), "message.schema.json")
        entry = {
            "sender": sender or "unknown",
            "type": msg.type,
            "topic": msg.topic,
            "session_id": msg.session_id,
            "payload": msg.payload,
            "ts": msg.ts,
        }
        self.history.append(entry)
        if msg.type in {"Proposal", "Decision", "Override", "Escalation"}:
            self.governance_log.append(entry)
        for cb in self._all:
            cb(msg)
        for cb in self._subs.get(msg.topic, []):
            cb(msg)
