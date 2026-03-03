from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from robotos.kernel.error_codes import ERR_ADMISSION_REJECTED
from robotos.kernel.policy.gate import ToolRegistry


@dataclass(frozen=True)
class AdmissionDecision:
    accepted: bool
    code: str = ""
    reason: str = ""
    detail: Dict[str, Any] | None = None


class AdmissionBudget:
    """Lightweight intent validator for deterministic pre-planning rejects.

    Scope is intentionally minimal: reject only when the request declares
    unknown tools. Dynamic runtime concerns (resource contention/latency)
    are handled by planner/executor paths where richer state is available.
    """

    def __init__(self, registry: ToolRegistry, *, max_parallel_model: int = 1, default_latency_budget_ms: int = 15_000) -> None:
        self.registry = registry
        self.max_parallel_model = max_parallel_model
        self.default_latency_budget_ms = default_latency_budget_ms

    def evaluate(self, intent: Dict[str, Any]) -> AdmissionDecision:
        planned_tools: List[str] = list(intent.get("planned_tools") or [])
        if not planned_tools:
            return AdmissionDecision(accepted=True)

        unknown_tools = []
        for tool in planned_tools:
            try:
                self.registry.get(tool)
            except KeyError:
                unknown_tools.append(tool)

        if unknown_tools:
            return AdmissionDecision(
                accepted=False,
                code=ERR_ADMISSION_REJECTED,
                reason="unknown_tools",
                detail={"unknown_tools": unknown_tools},
            )

        return AdmissionDecision(accepted=True)
