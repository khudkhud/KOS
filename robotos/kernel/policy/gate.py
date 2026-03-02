"""Policy gate and file-backed tool registry.

Applies capability/risk checks before action dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from typing import Any, Dict, List, Optional
import json

from robotos.models import Session
from robotos.schema_validate import validate


@dataclass
class ToolSpec:
    tool: str
    required_resources: List[str]
    capability: str
    risk_class: str = "SAFE"
    cancel_grace_ms: int = 500
    timeout_default_ms: int = 600_000
    dds_action: str = ""
    contract_version: str = "1.0"
    idempotent: bool = False
    compensation_tool: str = ""
    rollout_stage: str = "ga"
    degrade_fallback_tool: str = ""


class ToolRegistry:
    def __init__(self, tools: List[ToolSpec]) -> None:
        self._tools: Dict[str, ToolSpec] = {t.tool: t for t in tools}

    @classmethod
    def from_json_file(cls, package_rel_path: str = "tool_registry.json") -> "ToolRegistry":
        text = resources.files("robotos.config").joinpath(package_rel_path).read_text(encoding="utf-8")
        raw: List[Dict[str, Any]] = json.loads(text)
        specs: List[ToolSpec] = []
        for entry in raw:
            validate(entry, "tool_registry.schema.json")
            specs.append(
                ToolSpec(
                    tool=entry["tool"],
                    dds_action=entry.get("dds_action", ""),
                    required_resources=entry["required_resources"],
                    capability=entry["capability"],
                    risk_class=entry.get("risk_class", "SAFE"),
                    cancel_grace_ms=entry.get("cancel_grace_ms", 500),
                    timeout_default_ms=entry.get("timeout_default_ms", 600_000),
                    contract_version=str(entry.get("contract_version", "1.0")),
                    idempotent=bool(entry.get("idempotent", False)),
                    compensation_tool=str(entry.get("compensation_tool", "")),
                    rollout_stage=str(entry.get("rollout_stage", "ga")),
                    degrade_fallback_tool=str(entry.get("degrade_fallback_tool", "")),
                )
            )
        return cls(specs)


    def __len__(self) -> int:
        return len(self._tools)


    def discover(self, capability: Optional[str] = None, rollout_stage: Optional[str] = None) -> List[ToolSpec]:
        out: List[ToolSpec] = []
        for spec in self._tools.values():
            if capability and spec.capability != capability:
                continue
            if rollout_stage and spec.rollout_stage != rollout_stage:
                continue
            out.append(spec)
        return sorted(out, key=lambda x: x.tool)

    @staticmethod
    def _contract_compatible(available: str, accepted: str) -> bool:
        if accepted == available:
            return True
        if accepted.endswith('.x'):
            return available.split('.', 1)[0] == accepted.split('.', 1)[0]
        return False

    def negotiate(self, name: str, accepted_contracts: List[str]) -> ToolSpec:
        spec = self.get(name)
        if not any(self._contract_compatible(spec.contract_version, a) for a in accepted_contracts):
            raise ValueError(f"tool {name} contract {spec.contract_version} is not compatible")
        return spec

    def get(self, name: str) -> ToolSpec:
        if name not in self._tools:
            raise KeyError(f"unknown tool: {name}")
        return self._tools[name]


class PolicyGate:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def check_tool(self, session: Session, tool: str) -> ToolSpec:
        spec = self.registry.get(tool)
        if spec.capability not in session.capabilities:
            raise PermissionError(f"session lacks capability {spec.capability}")
        if spec.risk_class == "FORBIDDEN":
            raise PermissionError(f"tool forbidden: {tool}")
        if spec.risk_class == "CONFIRM" and session.risk_class != "CONFIRM":
            raise PermissionError("tool requires explicit confirm risk class")
        return spec
