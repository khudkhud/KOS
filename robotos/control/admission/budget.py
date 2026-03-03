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
    """Simple admission controller to reject obviously over-budget intents."""

    def __init__(self, registry: ToolRegistry, *, max_parallel_model: int = 1, default_latency_budget_ms: int = 15_000) -> None:
        self.registry = registry
        self.max_parallel_model = max_parallel_model
        self.default_latency_budget_ms = default_latency_budget_ms

    def evaluate(self, intent: Dict[str, Any]) -> AdmissionDecision:
        planned_tools: List[str] = list(intent.get("planned_tools") or [])
        if not planned_tools:
            return AdmissionDecision(accepted=True)

        model_tools = []
        est_total_ms = 0
        unknown_tools = []
        for tool in planned_tools:
            try:
                spec = self.registry.get(tool)
            except KeyError:
                unknown_tools.append(tool)
                continue
            est_total_ms += int(spec.timeout_default_ms)
            if any(r in {"hpu", "npu"} for r in spec.required_resources):
                model_tools.append(tool)

        if unknown_tools:
            return AdmissionDecision(
                accepted=False,
                code=ERR_ADMISSION_REJECTED,
                reason="unknown_tools",
                detail={"unknown_tools": unknown_tools},
            )

        latency_budget_ms = int(intent.get("latency_budget_ms") or self.default_latency_budget_ms)
        if est_total_ms > latency_budget_ms:
            return AdmissionDecision(
                accepted=False,
                code=ERR_ADMISSION_REJECTED,
                reason="latency_budget_exceeded",
                detail={"estimated_ms": est_total_ms, "latency_budget_ms": latency_budget_ms},
            )

        if len(model_tools) > max(1, self.max_parallel_model) * 3:
            return AdmissionDecision(
                accepted=False,
                code=ERR_ADMISSION_REJECTED,
                reason="model_contention_risk",
                detail={"model_tools": model_tools, "max_parallel_model": self.max_parallel_model},
            )

        return AdmissionDecision(accepted=True)
