"""Compiler from Plan JSON to executable graph IR.

Injects runtime-stability decorators (timeout/retry) and lease effects.
"""

from __future__ import annotations

from typing import Any, Dict, List

from robotos.kernel.policy.gate import ToolRegistry
from robotos.models import new_id
from robotos.schema_validate import validate


class PlanCompiler:
    def __init__(self, tool_registry: ToolRegistry) -> None:
        self.tools = tool_registry

    def compile(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        validate(plan, "plan_json.schema.json")
        children: List[Dict[str, Any]] = []
        held: List[str] = []
        for node in plan["root"]["children"]:
            tool = node["name"]
            spec = self.tools.get(tool)
            add = [r for r in spec.required_resources if r not in held]
            if add:
                children.append({"type": "AcquireLease", "resources": add, "ttl_ms": 5000})
                held.extend(add)
            action = {"type": "Action", "tool": tool, "args": node.get("args", {})}
            wrapped = {
                "type": "Timeout",
                "timeout_ms": spec.timeout_default_ms,
                "child": {"type": "Retry", "max": 2, "backoff_ms": 1000, "child": action},
            }
            children.append(wrapped)
        if held:
            children.append({"type": "ReleaseLease", "resources": held})
        graph = {
            "exec_graph_id": new_id("G"),
            "session_id": plan["session_id"],
            "plan_id": plan["plan_id"],
            "root": {"type": "Seq", "children": children},
        }
        validate(graph, "exec_graph.schema.json")
        return graph
