"""Scenario-level execution flow visualization for RobotOS.

Generates a step-by-step timeline and Mermaid diagram from a demo run so users
can inspect how control/planner/compiler/kernel/actions collaborate.
"""

from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List

from robotos.app import run_demo


def _event_step(evt: Dict[str, Any]) -> str:
    et = evt.get("type", "")
    payload = evt.get("payload", {}) or {}
    if et == "SESSION_CREATED":
        return "Control: create session"
    if et == "INTENT_SUBMITTED":
        return "Control: submit intent"
    if et == "REQUEST_ENQUEUED" and payload.get("topic") == "REQ_PLAN":
        return "Strategy: enqueue replan request"
    if et == "LEASE_ACQUIRED":
        return f"Kernel: acquire lease({payload.get('resource')})"
    if et == "ACTION_GOAL_SENT":
        return f"Kernel: send action goal({payload.get('tool')})"
    if et == "ACTION_FEEDBACK":
        return "Skill->Kernel: action feedback"
    if et == "ACTION_RESULT":
        return f"Skill->Kernel: action result({payload.get('status')})"
    if et == "SESSION_STATE_CHANGED":
        return f"Session state -> {payload.get('state')}"
    if et == "PREEMPT_PHASE1_START":
        return "Kernel: preempt phase1(quiesce)"
    if et == "PREEMPT_PHASE2_COMPLETE":
        return "Kernel: preempt phase2(handover complete)"
    if et == "KERNEL_TICK":
        return f"Kernel tick: {payload.get('status')}"
    if et == "LEASE_RELEASED":
        return f"Kernel: release lease({payload.get('resource')})"
    return f"Event: {et}"


def build_timeline(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert OSM event stream into readable ordered timeline steps."""
    return [
        {
            "index": i + 1,
            "ts": evt.get("ts"),
            "event": evt.get("type"),
            "step": _event_step(evt),
            "session_id": evt.get("session_id"),
        }
        for i, evt in enumerate(events)
    ]


def build_mermaid_flow(timeline: List[Dict[str, Any]]) -> str:
    """Render timeline into Mermaid flowchart."""
    lines = ["flowchart TD"]
    for item in timeline:
        nid = f"N{item['index']}"
        label = item["step"].replace('"', "'")
        lines.append(f'    {nid}["{item["index"]}. {label}"]')
    for idx in range(1, len(timeline)):
        lines.append(f"    N{idx} --> N{idx + 1}")
    return "\n".join(lines)


def run_flow_demo(scenario: str) -> Dict[str, Any]:
    """Run a selected scenario and return timeline + visualization artifacts."""
    if scenario == "normal":
        out = run_demo()
    elif scenario == "target_gone":
        out = run_demo(emit_target_gone=True)
    elif scenario == "preempt":
        out = run_demo(do_preempt=True)
    elif scenario == "cancel":
        out = run_demo(cancel_midway=True)
    else:
        raise ValueError(f"unknown scenario: {scenario}")

    timeline = build_timeline(out.get("events", []))
    return {
        "scenario": scenario,
        "final_session": out.get("session"),
        "timeline": timeline,
        "mermaid_flowchart": build_mermaid_flow(timeline),
        "message_count": len(out.get("messages", [])),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="RobotOS scenario flow visualization")
    parser.add_argument("--scenario", choices=["normal", "target_gone", "preempt", "cancel"], default="normal")
    args = parser.parse_args()
    result = run_flow_demo(args.scenario)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
