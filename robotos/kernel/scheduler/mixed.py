"""Lightweight scheduler for model+skill mixed workload routing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass
class TaskSpec:
    name: str
    task_type: str  # model | skill
    priority: int = 0
    est_cost: float = 1.0
    est_latency_ms: int = 100


class MixedWorkloadScheduler:
    def __init__(self, max_parallel_skill: int = 2, max_parallel_model: int = 1) -> None:
        self.max_parallel_skill = max_parallel_skill
        self.max_parallel_model = max_parallel_model
        self.running: Dict[str, int] = {"skill": 0, "model": 0}

    def can_run(self, task: TaskSpec) -> bool:
        kind = task.task_type
        if kind == "skill":
            return self.running["skill"] < self.max_parallel_skill
        if kind == "model":
            return self.running["model"] < self.max_parallel_model
        return False

    def start(self, task: TaskSpec) -> bool:
        if not self.can_run(task):
            return False
        self.running[task.task_type] += 1
        return True

    def finish(self, task: TaskSpec) -> None:
        if task.task_type in self.running and self.running[task.task_type] > 0:
            self.running[task.task_type] -= 1
