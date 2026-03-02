"""AI model skill stubs for embodied perception/planning.

These are lightweight placeholders to standardize how model inference is exposed
through skill contracts. Real model runtime can later replace `infer` internals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class ModelSkillSpec:
    name: str
    mode: str  # periodic_service | on_demand_skill
    est_latency_ms: int
    output_schema: Dict[str, str]


class BaseModelSkill:
    def __init__(self, spec: ModelSkillSpec) -> None:
        self.spec = spec

    def infer(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        # Placeholder for real runtime inference call.
        return {
            "model": self.spec.name,
            "mode": self.spec.mode,
            "latency_ms": self.spec.est_latency_ms,
            "inputs": list(inputs.keys()),
            "result": {k: f"stub_{v}" for k, v in self.spec.output_schema.items()},
        }


class YOLOSkill(BaseModelSkill):
    def __init__(self) -> None:
        super().__init__(
            ModelSkillSpec(
                name="yolo_detector",
                mode="periodic_service",
                est_latency_ms=30,
                output_schema={"detections": "bbox_list", "confidence": "float"},
            )
        )


class DepthAnythingSkill(BaseModelSkill):
    def __init__(self) -> None:
        super().__init__(
            ModelSkillSpec(
                name="depth_anything",
                mode="on_demand_skill",
                est_latency_ms=220,
                output_schema={"depth_map": "tensor", "free_space": "polygon"},
            )
        )


class NavDPSkill(BaseModelSkill):
    def __init__(self) -> None:
        super().__init__(
            ModelSkillSpec(
                name="navdp_local_waypoint",
                mode="on_demand_skill",
                est_latency_ms=80,
                output_schema={"waypoint": "pose2d", "risk": "float"},
            )
        )


class RoboBrainSkill(BaseModelSkill):
    def __init__(self) -> None:
        super().__init__(
            ModelSkillSpec(
                name="robobrain_task_planner",
                mode="on_demand_skill",
                est_latency_ms=260,
                output_schema={"task_graph": "json", "explain": "text"},
            )
        )
