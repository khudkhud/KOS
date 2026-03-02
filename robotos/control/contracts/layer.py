"""Interface contracts for Task↔Behavior↔Motion layering."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass(frozen=True)
class TaskContract:
    task_type: str
    max_latency_ms: int
    safety_level: str
    degrade_policy: str


@dataclass(frozen=True)
class BehaviorContract:
    behavior_id: str
    expected_error_codes: List[str] = field(default_factory=list)
    interruptible: bool = True
    timeout_ms: int = 30_000


@dataclass(frozen=True)
class MotionContract:
    controller: str
    max_velocity: float
    obstacle_clearance_m: float
    stop_on_estimation_loss: bool = True


DEFAULT_ERROR_TAXONOMY = {"NAV_TIMEOUT", "NAV_BLOCKED", "SENSOR_LOSS", "EXEC_FAIL"}


def validate_stack(task: TaskContract, behavior: BehaviorContract, motion: MotionContract) -> Dict[str, object]:
    unknown = [x for x in behavior.expected_error_codes if x not in DEFAULT_ERROR_TAXONOMY]
    if unknown:
        raise ValueError(f"unknown behavior error codes: {unknown}")
    if task.max_latency_ms < behavior.timeout_ms:
        raise ValueError("task latency budget must be >= behavior timeout")
    if motion.max_velocity <= 0:
        raise ValueError("motion max_velocity must be positive")
    if motion.obstacle_clearance_m <= 0:
        raise ValueError("motion obstacle_clearance_m must be positive")
    return {
        "task": task,
        "behavior": behavior,
        "motion": motion,
    }
