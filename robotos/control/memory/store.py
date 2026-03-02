"""Memory store with short-term/context/long-term layers for embodied agents."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from robotos.models import now_ms


@dataclass
class MemoryItem:
    key: str
    value: Dict[str, Any]
    ts: int = field(default_factory=now_ms)
    ttl_ms: int = 0


class MemoryStore:
    def __init__(self) -> None:
        self.short_term: Dict[str, List[MemoryItem]] = {}
        self.long_term_user: Dict[str, Dict[str, Any]] = {}
        self.contextual: Dict[str, Dict[str, Any]] = {}
        self.world_memory: Dict[str, Any] = {
            "semantic_topology": {
                "entrance": {"waypoints": ["hallway", "entrance"]},
                "child_room": {"waypoints": ["hallway", "child_room"]},
                "kitchen": {"waypoints": ["hallway", "kitchen"]},
            }
        }

    def write_short_term(self, session_id: str, key: str, value: Dict[str, Any], ttl_ms: int = 30 * 60 * 1000) -> None:
        self.short_term.setdefault(session_id, []).append(MemoryItem(key=key, value=value, ttl_ms=ttl_ms))

    def write_long_term_user_pref(self, user_id: str, pref_key: str, pref_value: Any) -> None:
        bucket = self.long_term_user.setdefault(user_id, {})
        bucket[pref_key] = pref_value

    def write_context(self, location: str, payload: Dict[str, Any]) -> None:
        self.contextual[location] = payload


    def write_world_fact(self, key: str, value: Any) -> None:
        self.world_memory[key] = value

    def read_world_fact(self, key: str, default: Any = None) -> Any:
        return self.world_memory.get(key, default)

    def cleanup_expired(self, now: int | None = None) -> None:
        ts = now or now_ms()
        for sid, items in list(self.short_term.items()):
            kept = [x for x in items if x.ttl_ms <= 0 or ts - x.ts <= x.ttl_ms]
            if kept:
                self.short_term[sid] = kept
            else:
                self.short_term.pop(sid, None)

    def erase_user(self, user_id: str) -> None:
        self.long_term_user.pop(user_id, None)
