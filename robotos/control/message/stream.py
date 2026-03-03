"""Message stream with durable event log, outbox flush, replay and idempotent consume.

Reliability contract:
- delivery: at-least-once for persisted records (consumer may observe duplicates)
- side-effects: callers must use ``idempotency_key`` to deduplicate state writes

Default mode is in-memory pub/sub. When ``persist_path`` is provided, entries are
written to JSONL event log and can be replayed by other local processes.
"""

from __future__ import annotations

from collections import defaultdict
from collections import deque
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from typing import Callable, DefaultDict, List, Optional

from robotos.control.message.agents import AgentRegistry
from robotos.models import Message
from robotos.schema_validate import validate


Subscriber = Callable[[Message], None]
GOVERNANCE_TYPES = frozenset({"Proposal", "Decision", "Override", "Escalation"})


class MessageStream:
    """Pub/sub stream with optional durable local event-log semantics."""

    def __init__(self, registry: Optional[AgentRegistry] = None, persist_path: Optional[str] = None) -> None:
        self._subs: DefaultDict[str, List[Subscriber]] = defaultdict(list)
        self._all: List[Subscriber] = []
        self.registry = registry
        self.history: List[dict] = []
        self.governance_log: List[dict] = []
        self.outbox: deque[dict] = deque()
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
            "idempotency_key": self._build_idempotency_key(msg),
        }
        self.outbox.append(entry)
        self.flush_outbox()

    def flush_outbox(self) -> int:
        """Flush pending outbox entries to history/event-log and dispatch."""
        flushed = 0
        while self.outbox:
            entry = self.outbox.popleft()
            self._record_entry(entry)
            self._dispatch(entry)
            flushed += 1
        return flushed

    def replay(self, from_line: int = 0, dispatch: bool = False) -> List[dict]:
        """Replay persisted event log from a line offset (1-indexed)."""
        if not self.persist_path:
            return []
        path = Path(self.persist_path)
        if not path.exists():
            return []
        replayed: List[dict] = []
        with path.open("r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, start=1):
                if lineno <= from_line:
                    continue
                entry = json.loads(line)
                replayed.append(entry)
                if dispatch:
                    self._dispatch(entry)
        return replayed

    def poll_new(self, consumer_id: Optional[str] = None) -> int:
        """Consume newly appended entries from log.

        If ``consumer_id`` is provided, cursor and consumed idempotency keys are
        persisted, enabling idempotent recovery across process restarts.
        """
        if not self.persist_path:
            return 0
        path = Path(self.persist_path)
        if not path.exists():
            return 0

        cursor = self._cursor_line
        consumed_keys: set[str] = set()
        if consumer_id:
            state = self._load_consumer_state(consumer_id)
            cursor = max(cursor, int(state.get("cursor_line", 0)))
            consumed_keys = set(state.get("consumed_keys", []))

        consumed = 0
        with path.open("r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, start=1):
                if lineno <= cursor:
                    continue
                entry = json.loads(line)
                key = str(entry.get("idempotency_key") or "")
                if key and key in consumed_keys:
                    cursor = lineno
                    continue
                self._append_history(entry)
                self._dispatch(entry)
                cursor = lineno
                consumed += 1
                if key:
                    consumed_keys.add(key)

        self._cursor_line = cursor
        if consumer_id:
            self._save_consumer_state(consumer_id, cursor, consumed_keys)
        return consumed

    def _record_entry(self, entry: dict) -> None:
        self._append_history(entry)
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
                self._append_history(entry)
                self._cursor_line = lineno

    def _append_history(self, entry: dict) -> None:
        self.history.append(entry)
        if entry.get("type") in GOVERNANCE_TYPES:
            self.governance_log.append(entry)

    def _build_idempotency_key(self, msg: Message) -> str:
        if msg.correlation_id:
            return msg.correlation_id
        raw = f"{msg.type}|{msg.topic}|{msg.session_id}|{json.dumps(msg.payload, sort_keys=True, ensure_ascii=False)}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def _consumer_state_path(self, consumer_id: str) -> Path:
        base = Path(self.persist_path or "")
        return base.with_suffix(base.suffix + f".{consumer_id}.state.json")

    def _load_consumer_state(self, consumer_id: str) -> dict:
        path = self._consumer_state_path(consumer_id)
        if not path.exists():
            return {"cursor_line": 0, "consumed_keys": []}
        return json.loads(path.read_text(encoding="utf-8"))

    def _save_consumer_state(self, consumer_id: str, cursor_line: int, consumed_keys: set[str]) -> None:
        path = self._consumer_state_path(consumer_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        keys = sorted(consumed_keys)
        if len(keys) > 2000:
            keys = keys[-2000:]
        payload = {"cursor_line": cursor_line, "consumed_keys": keys}
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
