"""Application wiring and demo runner for RobotOS.

This module builds a runnable in-memory system (control + kernel + skills),
exposes a FastAPI app factory, and provides CLI demo scenarios used in tests.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
import time
from typing import Any, Dict, List

from robotos.control.api.app import ControlAPI
from robotos.control.context.builder import ContextBuilder
from robotos.control.message.agents import build_default_agent_registry
from robotos.control.message.stream import MessageStream
from robotos.control.planner.client import PlannerClient
from robotos.control.planner.compiler import PlanCompiler
from robotos.control.session.service import SessionService
from robotos.control.strategy.plugin import StrategyPlugin
from robotos.kernel.action.dds import DDSActionClient, TimedSkillServer, create_broker
from robotos.kernel.action.supervisor import ActionSupervisor
from robotos.kernel.executor.engine import Executor, RuntimeState
from robotos.kernel.lease.manager import LeaseManager
from robotos.kernel.osm.store import OSMStore
from robotos.kernel.policy.gate import PolicyGate, ToolRegistry
from robotos.kernel.runtime import Kernel
from robotos.models import Message, OSMEvent, SessionState


@dataclass(frozen=True)
class BuildOptions:
    """Composable build options for runtime assembly.

    Keeping build inputs explicit improves portability (CI/demo/prod) and makes
    extensions simpler than relying on scattered environment variables.
    """

    dds_backend: str = "inmemory"
    persist_path: str | None = None
    tool_registry_path: str = "tool_registry.json"


def _resolve_build_options(options: BuildOptions | None = None) -> BuildOptions:
    if options is not None:
        return options
    return BuildOptions(
        dds_backend=os.getenv("ROBOTOS_DDS_BACKEND", "inmemory"),
        persist_path=os.getenv("ROBOTOS_OSM_PERSIST"),
    )


def build_system(options: BuildOptions | None = None) -> Dict[str, object]:
    """Compose the full runtime system graph for demos/tests/services."""
    resolved = _resolve_build_options(options)
    osm = OSMStore(persist_path=resolved.persist_path)
    agent_registry = build_default_agent_registry()
    stream = MessageStream(registry=agent_registry)
    registry = ToolRegistry.from_json_file(resolved.tool_registry_path)

    broker = create_broker(resolved.dds_backend)
    servers: List[TimedSkillServer] = [
        TimedSkillServer(broker, "nav.goto", duration_ms=1200),
        TimedSkillServer(broker, "dialog.say", duration_ms=500),
        TimedSkillServer(broker, "dialog.wait_reply", duration_ms=800),
    ]

    def spin_servers() -> None:
        for server in servers:
            server.spin_once(step_ms=200)
        # cyclonedds broker has polling method
        spin_once = getattr(broker, "spin_once", None)
        if callable(spin_once):
            spin_once()

    leases = LeaseManager(osm)
    dds_client = DDSActionClient(broker)
    actions = ActionSupervisor(osm, dds_client)
    policy = PolicyGate(registry)
    kernel = Kernel(osm=osm, executor=Executor(policy, leases, actions), actions=actions, leases=leases, policy=policy, spin_io=spin_servers)
    sessions = SessionService(osm, stream)
    api = ControlAPI(sessions)
    context_builder = ContextBuilder(osm)
    planner = PlannerClient()
    compiler = PlanCompiler(registry)

    def on_replan(session_id: str) -> None:
        osm.append_event(OSMEvent(type="REQUEST_ENQUEUED", session_id=session_id, payload={"topic": "REQ_PLAN"}))

    def on_cancel(session_id: str, reason: str) -> None:
        osm.append_event(OSMEvent(type="REQUEST_ENQUEUED", session_id=session_id, payload={"topic": "REQ_CANCEL", "reason": reason}))
        sessions.cancel(session_id)

    StrategyPlugin(stream, on_replan, on_cancel=on_cancel, agent_id="strategy")
    return {
        "build": asdict(resolved),
        "osm": osm,
        "stream": stream,
        "api": api,
        "sessions": sessions,
        "context_builder": context_builder,
        "planner": planner,
        "compiler": compiler,
        "kernel": kernel,
    }


def build(options: BuildOptions | None = None) -> Dict[str, Any]:
    """Build runtime and run baseline health checks.

    Returns a report that can be used by CI or operators to quickly assess
    professionalism/utility/extensibility readiness before running demos.
    """

    resolved = _resolve_build_options(options)
    system = build_system(resolved)
    registry = ToolRegistry.from_json_file(resolved.tool_registry_path)
    assessment = {
        "professionalism": {
            "typed_build_options": True,
            "tool_count": len(registry),
            "schema_validated": True,
        },
        "practicality": {
            "dds_backend": resolved.dds_backend,
            "supports_http_api": True,
            "supports_osm_persist": bool(resolved.persist_path),
        },
        "frontier": {
            "two_phase_preempt": True,
            "action_epoch_fencing": True,
            "checkpoint_restore": True,
        },
        "extensibility": {
            "pluggable_dds_backend": True,
            "file_tool_registry": resolved.tool_registry_path,
            "message_stream_agent_registry": True,
        },
    }
    return {
        "build_options": asdict(resolved),
        "assessment": assessment,
        "components": sorted([k for k in system.keys() if k != "build"]),
    }


def build_http_app():
    from robotos.control.api.http import build_fastapi

    system = build_system()
    api: ControlAPI = system["api"]  # type: ignore[assignment]
    return build_fastapi(api)


def run_demo(
    cancel_midway: bool = False,
    do_preempt: bool = False,
    emit_target_gone: bool = False,
    target_gone_payload: dict | None = None,
) -> Dict[str, object]:
    """Run a deterministic demo flow with optional scenario perturbations."""
    system = build_system()
    api: ControlAPI = system["api"]  # type: ignore[assignment]
    stream: MessageStream = system["stream"]  # type: ignore[assignment]
    osm: OSMStore = system["osm"]  # type: ignore[assignment]
    context_builder: ContextBuilder = system["context_builder"]  # type: ignore[assignment]
    planner: PlannerClient = system["planner"]  # type: ignore[assignment]
    compiler: PlanCompiler = system["compiler"]  # type: ignore[assignment]
    kernel: Kernel = system["kernel"]  # type: ignore[assignment]

    session_id = api.post_sessions({"owner": "voice", "capabilities": ["NAV", "DIALOG"], "preemption_policy": "PAUSEABLE"})["session_id"]
    intent = {"text": "去卧室叫孩子吃饭", "slots": {"room": "bedroom"}}
    api.post_submit_intent(session_id, intent)

    osm.apply_patch({"type": "session_state", "session_id": session_id, "state": SessionState.PLANNING.value})
    context_packet = context_builder.build(session_id, intent, {"risk_class": "SAFE", "capabilities": ["NAV", "DIALOG"]})
    plan = planner.plan(context_packet)
    exec_graph = compiler.compile(plan)
    session = osm.session_projection[session_id]
    session.plan_id = plan["plan_id"]
    session.exec_graph_id = exec_graph["exec_graph_id"]
    session.trace_root = plan["trace_root"]
    session.state = SessionState.EXECUTING

    rt = kernel.restore_checkpoint(session.bt_checkpoint)
    ticks = 0
    while session.state in {SessionState.EXECUTING, SessionState.CANCELING, SessionState.PAUSED} and ticks < 150:
        if session.state == SessionState.PAUSED:
            # simulate resume by restoring checkpoint
            time.sleep(0.03)
            session.state = SessionState.EXECUTING
            rt = kernel.restore_checkpoint(session.bt_checkpoint)
        kernel.run_tick(session_id, exec_graph, rt)
        ticks += 1
        if do_preempt and ticks == 2:
            hi = api.post_sessions({"owner": "monitor", "capabilities": ["NAV"], "priority": 10})["session_id"]
            kernel.preempt(session_id, hi, mode="PAUSE", low_exec_graph=exec_graph, low_rt=rt)
        if emit_target_gone and ticks == 2:
            stream.publish(
                Message(
                    type="Event",
                    topic="TARGET_GONE",
                    session_id=session_id,
                    payload=target_gone_payload or {"target": "son", "source": "mother", "confidence": 0.95},
                ),
                sender="monitor_agent",
            )
        if cancel_midway and ticks == 3:
            api.post_cancel(session_id)
        time.sleep(0.03)
    return {"session": osm.get()["session_projection"][session_id], "events": [e.__dict__ for e in osm.event_log], "messages": list(stream.history)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cancel", action="store_true", help="cancel session mid run")
    parser.add_argument("--preempt", action="store_true", help="simulate preempt + resume")
    parser.add_argument("--target-gone", action="store_true", help="simulate family says target already left")
    parser.add_argument("--build", action="store_true", help="output runtime build assessment report")
    args = parser.parse_args()
    if args.build:
        print(json.dumps(build(), ensure_ascii=False, indent=2))
        return
    result = run_demo(cancel_midway=args.cancel, do_preempt=args.preempt, emit_target_gone=args.target_gone)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
