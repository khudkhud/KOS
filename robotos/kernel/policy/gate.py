from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from robotos.models import Session


@dataclass
class ToolSpec:
    tool: str
    required_resources: List[str]
    capability: str
    risk_class: str = "SAFE"
    cancel_grace_ms: int = 500
    timeout_default_ms: int = 600_000


class ToolRegistry:
    def __init__(self, tools: List[ToolSpec]) -> None:
        self._tools: Dict[str, ToolSpec] = {t.tool: t for t in tools}

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
