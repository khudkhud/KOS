"""Path & Navigation Agent (PNA) service abstraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from robotos.control.memory.store import MemoryStore


@dataclass
class NavigationGoal:
    semantic_target: str
    constraints: Dict[str, object] = field(default_factory=dict)


@dataclass
class NavigationResult:
    success: bool
    global_path: List[str]
    waypoints: List[str]
    reason: str = ""


class PathNavigationAgent:
    """Resolve semantic targets using dynamic world-model topologies."""

    def __init__(self, memory: MemoryStore) -> None:
        self.memory = memory

    def plan_and_execute(self, goal: NavigationGoal) -> NavigationResult:
        topo_ids = goal.constraints.get("topology_ids")
        min_conf = float(goal.constraints.get("min_confidence", 0.0))
        resolved = self.memory.resolve_semantic_target(
            goal.semantic_target,
            topology_ids=topo_ids if isinstance(topo_ids, list) else None,
            min_confidence=min_conf,
        )
        if not resolved:
            return NavigationResult(success=False, global_path=[], waypoints=[], reason="unknown_target")

        path = ["home_base", goal.semantic_target]
        waypoints = list(resolved.get("waypoints", [goal.semantic_target]))
        self.memory.write_world_fact(
            "navigation_last_result",
            {
                "target": goal.semantic_target,
                "path": path,
                "waypoints": waypoints,
                "constraints": goal.constraints,
                "map_id": resolved.get("map_id"),
                "confidence": resolved.get("confidence", 0.0),
                "updated_at": resolved.get("updated_at", 0),
            },
        )
        return NavigationResult(success=True, global_path=path, waypoints=waypoints)
