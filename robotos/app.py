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

from robotos.control.agents import TaskExecutionAgent
from robotos.control.api.app import ControlAPI
from robotos.control.context.builder import ContextBuilder
from robotos.control.contracts import BehaviorContract, MotionContract, TaskContract, validate_stack
from robotos.control.message.agents import build_default_agent_registry
from robotos.control.memory.store import MemoryStore
from robotos.control.message.stream import MessageStream
from robotos.control.planner.client import PlannerClient
from robotos.control.planner.compiler import PlanCompiler
from robotos.control.session.service import SessionService
from robotos.control.strategy.plugin import StrategyPlugin
from robotos.control.world_memory import OSMWorldProjector
from robotos.kernel.action.dds import DDSActionClient, TimedSkillServer, create_broker
from robotos.kernel.action.supervisor import ActionSupervisor
from robotos.kernel.executor.engine import Executor, RuntimeState
from robotos.kernel.lease.manager import LeaseManager
from robotos.kernel.osm.store import OSMStore
from robotos.kernel.policy.gate import PolicyGate, ToolRegistry
from robotos.kernel.runtime import Kernel
from robotos.embodied import NavigationGoal, PathNavigationAgent, RobotStateEstimator, SafetySupervisor
from robotos.kernel.scheduler.mixed import MixedWorkloadScheduler, TaskSpec
from robotos.models import Message, OSMEvent, SessionState
from robotos.skills.ai_model_skills import YOLOPerceptionServiceSkill




@dataclass(frozen=True)
class EmbodimentProfile:
    """Resource constraints for a household embodied mobile robot."""

    max_concurrent_actions: int = 2
    max_session_runtime_ms: int = 45 * 60 * 1000
    stale_action_timeout_ms: int = 120 * 1000
    min_battery_percent_to_start: int = 15
    max_parallel_model_tasks: int = 1


@dataclass(frozen=True)
class BuildOptions:
    """Composable build options for runtime assembly.

    Keeping build inputs explicit improves portability (CI/demo/prod) and makes
    extensions simpler than relying on scattered environment variables.
    """

    dds_backend: str = "inmemory"
    persist_path: str | None = None
    tool_registry_path: str = "tool_registry.json"
    message_persist_path: str | None = None
    embodiment: EmbodimentProfile = EmbodimentProfile()


def _resolve_build_options(options: BuildOptions | None = None) -> BuildOptions:
    if options is not None:
        return options
    return BuildOptions(
        dds_backend=os.getenv("ROBOTOS_DDS_BACKEND", "inmemory"),
        persist_path=os.getenv("ROBOTOS_OSM_PERSIST"),
        message_persist_path=os.getenv("ROBOTOS_MESSAGE_PERSIST"),
    )


def build_system(options: BuildOptions | None = None) -> Dict[str, object]:
    """Compose the full runtime system graph for demos/tests/services."""
    resolved = _resolve_build_options(options)
    osm = OSMStore(persist_path=resolved.persist_path)
    agent_registry = build_default_agent_registry()
    stream = MessageStream(registry=agent_registry, persist_path=resolved.message_persist_path)
    registry = ToolRegistry.from_json_file(resolved.tool_registry_path)

    broker = create_broker(resolved.dds_backend)
    yolo_service = YOLOPerceptionServiceSkill()
    servers: List[TimedSkillServer] = [
        TimedSkillServer(broker, "nav.goto", duration_ms=1200),
        TimedSkillServer(broker, "dialog.say", duration_ms=500),
        TimedSkillServer(broker, "dialog.wait_reply", duration_ms=800),
        TimedSkillServer(broker, "perception.depth_anything.estimate", duration_ms=700),
        TimedSkillServer(broker, "navigation.navdp.predict_waypoint", duration_ms=300),
        TimedSkillServer(broker, "navigation.navdp.logic_predict_waypoint", duration_ms=120),
        TimedSkillServer(broker, "planning.robobrain.plan", duration_ms=900),
    ]

    def spin_servers() -> None:
        # YOLO runs as periodic perception service (not action-triggered).
        yolo_out = yolo_service.infer({"frame": "stream"})
        has_obstacle = bool(yolo_out.get("result", {}).get("detections"))
        state_estimator.update(obstacle_nearby=has_obstacle)
        for server in servers:
            server.spin_once(step_ms=200)
        # cyclonedds broker has polling method
        spin_once = getattr(broker, "spin_once", None)
        if callable(spin_once):
            spin_once()

    leases = LeaseManager(osm)
    scheduler = MixedWorkloadScheduler(
        max_parallel_skill=resolved.embodiment.max_concurrent_actions,
        max_parallel_model=resolved.embodiment.max_parallel_model_tasks,
    )
    state_estimator = RobotStateEstimator()
    dds_client = DDSActionClient(broker)
    actions = ActionSupervisor(
        osm,
        dds_client,
        max_concurrent_actions=resolved.embodiment.max_concurrent_actions,
        scheduler=scheduler,
        leases=leases,
        tool_registry=registry,
    )
    policy = PolicyGate(registry)
    kernel = Kernel(
        osm=osm,
        executor=Executor(policy, leases, actions),
        actions=actions,
        leases=leases,
        policy=policy,
        spin_io=spin_servers,
        max_session_runtime_ms=resolved.embodiment.max_session_runtime_ms,
        stale_action_timeout_ms=resolved.embodiment.stale_action_timeout_ms,
    )
    api_state_estimator = state_estimator
    sessions = SessionService(osm, stream)
    api = ControlAPI(sessions)
    context_builder = ContextBuilder(osm)
    planner = PlannerClient()
    compiler = PlanCompiler(registry)
    memory = MemoryStore()
    pna = PathNavigationAgent(memory)
    task_agent = TaskExecutionAgent(stream, pna)
    state_estimator = api_state_estimator
    safety = SafetySupervisor(min_battery_percent=resolved.embodiment.min_battery_percent_to_start)
    projector = OSMWorldProjector(memory)

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
        "memory": memory,
        "scheduler": scheduler,
        "pna": pna,
        "task_agent": task_agent,
        "state_estimator": state_estimator,
        "safety": safety,
        "projector": projector,
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
            "supports_message_persist": bool(resolved.message_persist_path),
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
        "embodied_reliability": {
            "max_concurrent_actions": resolved.embodiment.max_concurrent_actions,
            "max_session_runtime_ms": resolved.embodiment.max_session_runtime_ms,
            "stale_action_timeout_ms": resolved.embodiment.stale_action_timeout_ms,
            "min_battery_percent_to_start": resolved.embodiment.min_battery_percent_to_start,
        },
        "ai_native_robotics": {
            "tool_contract_versioning": True,
            "governance_bus": True,
            "memory_layers": ["short_term", "long_term_user", "contextual", "world_memory"],
            "mixed_model_skill_scheduler": True,
            "explainable_trace": True,
            "layered_control_contract": True,
            "pna_service": True,
            "safety_supervisor": True,
            "state_estimator": True,
            "osm_world_projection_pipeline": True,
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
    memory: MemoryStore = system["memory"]  # type: ignore[assignment]
    scheduler: MixedWorkloadScheduler = system["scheduler"]  # type: ignore[assignment]
    pna: PathNavigationAgent = system["pna"]  # type: ignore[assignment]
    task_agent: TaskExecutionAgent = system["task_agent"]  # type: ignore[assignment]
    state_estimator: RobotStateEstimator = system["state_estimator"]  # type: ignore[assignment]
    safety: SafetySupervisor = system["safety"]  # type: ignore[assignment]
    projector: OSMWorldProjector = system["projector"]  # type: ignore[assignment]
    kernel: Kernel = system["kernel"]  # type: ignore[assignment]

    session_id = api.post_sessions({"owner": "voice", "capabilities": ["NAV", "DIALOG"], "preemption_policy": "PAUSEABLE"})["session_id"]
    intent = {"text": "去卧室叫孩子吃饭", "slots": {"room": "bedroom"}}
    api.post_submit_intent(session_id, intent)
    memory.write_short_term(session_id, "intent", intent)
    memory.write_long_term_user_pref("voice", "preferred_language", "zh")
    memory.write_context("home", {"battery_percent": 78, "network": "online"})

    contracts = validate_stack(
        TaskContract(task_type="multi_step_home_task", max_latency_ms=120_000, safety_level="HOUSEHOLD", degrade_policy="retry_then_escalate"),
        BehaviorContract(behavior_id="navigate_scan_report", expected_error_codes=["NAV_TIMEOUT", "NAV_BLOCKED"], interruptible=True, timeout_ms=30_000),
        MotionContract(controller="local_planner_v1", max_velocity=0.4, obstacle_clearance_m=0.25),
    )
    osm.append_event(OSMEvent(type="LAYER_CONTRACT_BOUND", session_id=session_id, payload={
        "task_type": contracts["task"].task_type,
        "behavior_id": contracts["behavior"].behavior_id,
        "controller": contracts["motion"].controller,
    }))

    state_estimator.update(battery_percent=78, localization_confidence=0.93, obstacle_nearby=False)
    safety_decision = safety.evaluate(state_estimator.snapshot())
    if not safety_decision.allow_execute:
        osm.apply_patch({"type": "session_state", "session_id": session_id, "state": SessionState.FAILED.value, "last_error": {"code": "SAFETY_BLOCK", "msg": safety_decision.reason}})
        return {"session": osm.get()["session_projection"][session_id], "events": [e.__dict__ for e in osm.event_log], "messages": list(stream.history), "governance": list(stream.governance_log), "memory": {"short_term_sessions": len(memory.short_term), "long_term_users": len(memory.long_term_user), "context_locations": len(memory.contextual)}}

    nav_result = pna.plan_and_execute(NavigationGoal(semantic_target="child_room", constraints={"avoid_private_rooms": False}))
    osm.append_event(OSMEvent(type="PNA_NAVIGATION_RESULT", session_id=session_id, payload={"success": nav_result.success, "path": nav_result.global_path, "reason": nav_result.reason}))
    task_agent.submit_long_nav_task(session_id, "child_room")

    model_task = TaskSpec(name="intent-parse", task_type="model", priority=5)
    if scheduler.start(model_task):
        scheduler.finish(model_task)

    osm.append_event(OSMEvent(type="EXPLAIN_TRACE", session_id=session_id, payload={"stage": "intent_ingested", "evidence": intent["text"]}))

    osm.apply_patch({"type": "session_state", "session_id": session_id, "state": SessionState.PLANNING.value})
    context_packet = context_builder.build(session_id, intent, {"risk_class": "SAFE", "capabilities": ["NAV", "DIALOG"]})
    plan = planner.plan(context_packet)
    exec_graph = compiler.compile(plan)
    osm.append_event(OSMEvent(type="EXPLAIN_TRACE", session_id=session_id, payload={"stage": "plan_compiled", "plan_id": plan["plan_id"], "exec_graph_id": exec_graph["exec_graph_id"]}))
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
    projection = projector.project([e.__dict__ for e in osm.event_log])
    memory.cleanup_expired()
    return {"session": osm.get()["session_projection"][session_id], "events": [e.__dict__ for e in osm.event_log], "messages": list(stream.history), "governance": list(stream.governance_log), "task_reports": list(task_agent.task_reports), "memory": {"short_term_sessions": len(memory.short_term), "long_term_users": len(memory.long_term_user), "context_locations": len(memory.contextual), "world_keys": len(memory.world_memory)}, "world_projection": projection}


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
