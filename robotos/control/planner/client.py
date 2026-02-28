from __future__ import annotations

from typing import Any, Dict

from robotos.models import new_id


class PlannerClient:
    """On-demand planner adapter (stubbed deterministic implementation)."""

    def plan(self, context_packet: Dict[str, Any]) -> Dict[str, Any]:
        intent_text = context_packet["intent"].get("text", "")
        room = context_packet["intent"].get("slots", {}).get("room", "bedroom")
        say_text = "吃饭啦" if "吃饭" in intent_text else "收到"
        return {
            "session_id": context_packet["session_id"],
            "plan_id": new_id("P"),
            "trace_root": context_packet["session_id"],
            "required_resources_hint": ["base", "mic", "speaker"],
            "root": {
                "type": "seq",
                "children": [
                    {"type": "tool", "name": "nav.goto", "args": {"place": room}},
                    {"type": "tool", "name": "dialog.say", "args": {"text": say_text}},
                    {"type": "tool", "name": "dialog.wait_reply", "args": {"timeout_s": 30}},
                ],
            },
        }
