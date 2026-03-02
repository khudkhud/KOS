"""Memory store with short-term/context/long-term layers for embodied agents."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

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
        ts = now_ms()
        default_nodes = {
            "entrance": {"waypoints": ["hallway", "entrance"], "confidence": 0.95, "updated_at": ts},
            "child_room": {"waypoints": ["hallway", "child_room"], "confidence": 0.92, "updated_at": ts},
            "kitchen": {"waypoints": ["hallway", "kitchen"], "confidence": 0.94, "updated_at": ts},
        }
        self.world_memory: Dict[str, Any] = {
            "semantic_topologies": {
                "home_core": {
                    "map_id": "home_core",
                    "nodes": default_nodes,
                    "created_at": ts,
                    "updated_at": ts,
                    "parents": [],
                }
            },
            "active_topology_ids": ["home_core"],
            # backward compatibility key
            "semantic_topology": {k: {"waypoints": v["waypoints"]} for k, v in default_nodes.items()},
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

    def upsert_semantic_node(
        self,
        *,
        map_id: str,
        node: str,
        waypoints: List[str],
        confidence: float,
        updated_at: Optional[int] = None,
        activate: bool = True,
    ) -> None:
        ts = updated_at or now_ms()
        tops = self.world_memory.setdefault("semantic_topologies", {})
        topo = tops.setdefault(
            map_id,
            {"map_id": map_id, "nodes": {}, "created_at": ts, "updated_at": ts, "parents": []},
        )
        topo["nodes"][node] = {
            "waypoints": list(waypoints),
            "confidence": float(confidence),
            "updated_at": ts,
        }
        topo["updated_at"] = ts
        if activate:
            active = self.world_memory.setdefault("active_topology_ids", [])
            if map_id not in active:
                active.append(map_id)
        self._refresh_flat_topology()

    def set_active_topologies(self, map_ids: List[str]) -> None:
        self.world_memory["active_topology_ids"] = list(map_ids)
        self._refresh_flat_topology()

    def merge_topologies(
        self,
        *,
        new_map_id: str,
        source_map_ids: List[str],
        bridge_nodes: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        tops = self.world_memory.setdefault("semantic_topologies", {})
        merged_nodes: Dict[str, Dict[str, Any]] = {}
        for sid in source_map_ids:
            topo = tops.get(sid, {})
            for name, data in topo.get("nodes", {}).items():
                prev = merged_nodes.get(name)
                score = (float(data.get("confidence", 0.0)), int(data.get("updated_at", 0)))
                if not prev or score > (float(prev.get("confidence", 0.0)), int(prev.get("updated_at", 0))):
                    merged_nodes[name] = {
                        "waypoints": list(data.get("waypoints", [name])),
                        "confidence": float(data.get("confidence", 0.0)),
                        "updated_at": int(data.get("updated_at", now_ms())),
                    }
        for bn in bridge_nodes or []:
            name = str(bn.get("node", ""))
            if not name:
                continue
            merged_nodes[name] = {
                "waypoints": list(bn.get("waypoints", [name])),
                "confidence": float(bn.get("confidence", 0.8)),
                "updated_at": int(bn.get("updated_at", now_ms())),
            }

        ts = now_ms()
        merged = {
            "map_id": new_map_id,
            "nodes": merged_nodes,
            "created_at": ts,
            "updated_at": ts,
            "parents": list(source_map_ids),
        }
        tops[new_map_id] = merged
        self.world_memory["active_topology_ids"] = [new_map_id]
        self._refresh_flat_topology()
        return merged

    def resolve_semantic_target(
        self,
        target: str,
        *,
        topology_ids: Optional[List[str]] = None,
        min_confidence: float = 0.0,
    ) -> Optional[Dict[str, Any]]:
        tops = self.world_memory.get("semantic_topologies", {})
        ids = topology_ids or self.world_memory.get("active_topology_ids", list(tops.keys()))
        best: Optional[Dict[str, Any]] = None
        for tid in ids:
            topo = tops.get(tid, {})
            node = topo.get("nodes", {}).get(target)
            if not node:
                continue
            conf = float(node.get("confidence", 0.0))
            if conf < min_confidence:
                continue
            cand = {
                "map_id": tid,
                "target": target,
                "waypoints": list(node.get("waypoints", [target])),
                "confidence": conf,
                "updated_at": int(node.get("updated_at", 0)),
            }
            if not best or (cand["confidence"], cand["updated_at"]) > (best["confidence"], best["updated_at"]):
                best = cand
        return best

    def _refresh_flat_topology(self) -> None:
        flat: Dict[str, Dict[str, Any]] = {}
        tops = self.world_memory.get("semantic_topologies", {})
        ids = self.world_memory.get("active_topology_ids", list(tops.keys()))
        for tid in ids:
            topo = tops.get(tid, {})
            for name, data in topo.get("nodes", {}).items():
                prev = flat.get(name)
                score = (float(data.get("confidence", 0.0)), int(data.get("updated_at", 0)))
                if not prev or score > (float(prev.get("confidence", 0.0)), int(prev.get("updated_at", 0))):
                    flat[name] = {"waypoints": list(data.get("waypoints", [name]))}
        self.world_memory["semantic_topology"] = flat

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
