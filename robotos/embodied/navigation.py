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
    """Resolve semantic targets using world memory and execute waypoint plan."""

    def __init__(self, memory: MemoryStore) -> None:
        self.memory = memory

    def plan_and_execute(self, goal: NavigationGoal) -> NavigationResult:
        topo = self.memory.world_memory.get("semantic_topology", {})
        node = topo.get(goal.semantic_target)
        if not node:
            return NavigationResult(success=False, global_path=[], waypoints=[], reason="unknown_target")
        path = ["home_base", goal.semantic_target]
        waypoints = node.get("waypoints", [goal.semantic_target])
        self.memory.write_world_fact(
            "navigation_last_result",
            {
                "target": goal.semantic_target,
                "path": path,
                "waypoints": waypoints,
                "constraints": goal.constraints,
            },
        )
        return NavigationResult(success=True, global_path=path, waypoints=waypoints)
