from __future__ import annotations

import argparse
import json
import os
import time
from typing import Dict, List

from robotos.control.api.app import ControlAPI
from robotos.control.context.builder import ContextBuilder
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
from robotos.models import OSMEvent, SessionState


def build_system() -> Dict[str, object]:
    osm_persist = os.getenv("ROBOTOS_OSM_PERSIST")
    osm = OSMStore(persist_path=osm_persist)
    stream = MessageStream()
    registry = ToolRegistry.from_json_file("tool_registry.json")

    broker = create_broker(os.getenv("ROBOTOS_DDS_BACKEND", "inmemory"))
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

    StrategyPlugin(stream, on_replan)
    return {
        "osm": osm,
        "stream": stream,
        "api": api,
        "sessions": sessions,
        "context_builder": context_builder,
        "planner": planner,
        "compiler": compiler,
        "kernel": kernel,
    }


def build_http_app():
    from robotos.control.api.http import build_fastapi

    system = build_system()
    api: ControlAPI = system["api"]  # type: ignore[assignment]
    return build_fastapi(api)


def run_demo(cancel_midway: bool = False, do_preempt: bool = False) -> Dict[str, object]:
    system = build_system()
    api: ControlAPI = system["api"]  # type: ignore[assignment]
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
            kernel.preempt(session_id, hi, mode="PAUSE")
        if cancel_midway and ticks == 3:
            api.post_cancel(session_id)
        time.sleep(0.03)
    return {"session": osm.get()["session_projection"][session_id], "events": [e.__dict__ for e in osm.event_log]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cancel", action="store_true", help="cancel session mid run")
    parser.add_argument("--preempt", action="store_true", help="simulate preempt + resume")
    args = parser.parse_args()
    result = run_demo(cancel_midway=args.cancel, do_preempt=args.preempt)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
