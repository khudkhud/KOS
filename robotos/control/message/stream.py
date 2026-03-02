"""Message stream with optional durable JSONL backing.

Default mode is in-memory pub/sub. When ``persist_path`` is provided, messages
are also appended to a local JSONL log so multiple processes on the same robot
can recover history and consume newly published events.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
import json
from pathlib import Path
from typing import Callable, DefaultDict, List, Optional

from robotos.control.message.agents import AgentRegistry
from robotos.models import Message
from robotos.schema_validate import validate


Subscriber = Callable[[Message], None]


class MessageStream:
    """In-memory pub/sub with optional local durable log for IPC recovery."""

    def __init__(self, registry: Optional[AgentRegistry] = None, persist_path: Optional[str] = None) -> None:
        self._subs: DefaultDict[str, List[Subscriber]] = defaultdict(list)
        self._all: List[Subscriber] = []
        self.registry = registry
        self.history: List[dict] = []
        self.governance_log: List[dict] = []
        self.persist_path = persist_path
        self._cursor_line = 0
        if self.persist_path:
            self._load_persisted()

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
            "correlation_id": msg.correlation_id,
            "trace_id": msg.trace_id,
            "severity": msg.severity,
        }
        self._record_entry(entry)
        self._dispatch(entry)

    def poll_new(self) -> int:
        """Load and dispatch newly appended messages from persistent log.

        Returns the number of newly consumed entries. No-op for pure in-memory mode.
        """
        if not self.persist_path:
            return 0
        path = Path(self.persist_path)
        if not path.exists():
            return 0
        consumed = 0
        with path.open("r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, start=1):
                if lineno <= self._cursor_line:
                    continue
                entry = json.loads(line)
                self.history.append(entry)
                if entry.get("type") in {"Proposal", "Decision", "Override", "Escalation"}:
                    self.governance_log.append(entry)
                self._dispatch(entry)
                self._cursor_line = lineno
                consumed += 1
        return consumed

    def _record_entry(self, entry: dict) -> None:
        self.history.append(entry)
        if entry["type"] in {"Proposal", "Decision", "Override", "Escalation"}:
            self.governance_log.append(entry)
        if self.persist_path:
            path = Path(self.persist_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._cursor_line += 1

    def _dispatch(self, entry: dict) -> None:
        msg = Message(
            type=str(entry.get("type", "")),
            topic=str(entry.get("topic", "")),
            severity=entry.get("severity"),
            session_id=entry.get("session_id"),
            correlation_id=str(entry.get("correlation_id") or ""),
            trace_id=entry.get("trace_id"),
            payload=entry.get("payload") or {},
            ts=int(entry.get("ts") or 0),
        )
        for cb in self._all:
            cb(msg)
        for cb in self._subs.get(msg.topic, []):
            cb(msg)

    def _load_persisted(self) -> None:
        path = Path(self.persist_path or "")
        if not path.exists():
            return
        with path.open("r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, start=1):
                entry = json.loads(line)
                self.history.append(entry)
                if entry.get("type") in {"Proposal", "Decision", "Override", "Escalation"}:
                    self.governance_log.append(entry)
                self._cursor_line = lineno
