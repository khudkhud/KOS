"""Demo-specific wiring and scenario runner."""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List

from robotos.control.agents import TaskExecutionAgent
from robotos.control.api.app import ControlAPI
from robotos.control.context.builder import ContextBuilder
from robotos.control.contracts import BehaviorContract, MotionContract, TaskContract, validate_stack
from robotos.control.memory.store import MemoryStore
from robotos.control.message.stream import MessageStream
from robotos.control.planner.client import PlannerClient
from robotos.control.planner.compiler import PlanCompiler
from robotos.control.world_memory import OSMWorldProjector
from robotos.embodied import NavigationGoal, PathNavigationAgent, RobotStateEstimator, SafetySupervisor
from robotos.kernel.action.dds import DDSBroker, TimedSkillServer
from robotos.kernel.orchestrator import Kernel
from robotos.kernel.osm.store import OSMStore
from robotos.kernel.scheduler.mixed import MixedWorkloadScheduler, TaskSpec
from robotos.models import Message, OSMEvent, SessionState
from robotos.skills.ai_model_skills import YOLOPerceptionServiceSkill


def create_demo_spin_io(*, broker: DDSBroker, state_estimator: RobotStateEstimator) -> Callable[[], None]:
    """Create demo skill-server spin loop used by runtime bootstrap."""
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
        yolo_out = yolo_service.infer({"frame": "stream"})
        has_obstacle = bool(yolo_out.get("result", {}).get("detections"))
        state_estimator.update(obstacle_nearby=has_obstacle)
        for server in servers:
            server.spin_once(step_ms=200)
        spin_once = getattr(broker, "spin_once", None)
        if callable(spin_once):
            spin_once()

    return spin_servers


def _base_snapshot(osm: OSMStore, stream: MessageStream, memory: MemoryStore) -> Dict[str, object]:
    session_projection = osm.get()["session_projection"]
    session_id = next(iter(session_projection.keys()))
    return {
        "session": session_projection[session_id],
        "events": [e.__dict__ for e in osm.event_log],
        "messages": list(stream.history),
        "governance": list(stream.governance_log),
        "memory": {
            "short_term_sessions": len(memory.short_term),
            "long_term_users": len(memory.long_term_user),
            "context_locations": len(memory.contextual),
        },
    }


def _final_snapshot(
    *,
    osm: OSMStore,
    stream: MessageStream,
    memory: MemoryStore,
    task_agent: TaskExecutionAgent,
    projector: OSMWorldProjector,
) -> Dict[str, object]:
    base = _base_snapshot(osm, stream, memory)
    projection = projector.project([e.__dict__ for e in osm.event_log])
    memory.cleanup_expired()
    base["task_reports"] = list(task_agent.task_reports)
    base["memory"] = {
        **base["memory"],
        "world_keys": len(memory.world_memory),
    }
    base["world_projection"] = projection
    return base


def _bind_layer_contract(osm: OSMStore, session_id: str) -> None:
    contracts = validate_stack(
        TaskContract(
            task_type="multi_step_home_task",
            max_latency_ms=120_000,
            safety_level="HOUSEHOLD",
            degrade_policy="retry_then_escalate",
        ),
        BehaviorContract(
            behavior_id="navigate_scan_report",
            expected_error_codes=["NAV_TIMEOUT", "NAV_BLOCKED"],
            interruptible=True,
            timeout_ms=30_000,
        ),
        MotionContract(controller="local_planner_v1", max_velocity=0.4, obstacle_clearance_m=0.25),
    )
    osm.append_event(
        OSMEvent(
            type="LAYER_CONTRACT_BOUND",
            session_id=session_id,
            payload={
                "task_type": contracts["task"].task_type,
                "behavior_id": contracts["behavior"].behavior_id,
                "controller": contracts["motion"].controller,
            },
        )
    )


def _run_execution_loop(
    *,
    session,
    session_id: str,
    api: ControlAPI,
    kernel: Kernel,
    exec_graph: Dict[str, Any],
    rt,
    stream: MessageStream,
    do_preempt: bool,
    emit_target_gone: bool,
    target_gone_payload: dict | None,
    cancel_midway: bool,
) -> None:
    ticks = 0
    while session.state in {SessionState.EXECUTING, SessionState.CANCELING, SessionState.PAUSED} and ticks < 150:
        if session.state == SessionState.PAUSED:
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


def run_demo(
    cancel_midway: bool = False,
    do_preempt: bool = False,
    emit_target_gone: bool = False,
    target_gone_payload: dict | None = None,
) -> Dict[str, object]:
    """Run a deterministic demo flow with optional scenario perturbations."""
    from robotos.runtime.bootstrap import build_system

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

    _bind_layer_contract(osm, session_id)

    state_estimator.update(battery_percent=78, localization_confidence=0.93, obstacle_nearby=False)
    safety_decision = safety.evaluate(state_estimator.snapshot())
    if not safety_decision.allow_execute:
        osm.apply_patch(
            {
                "type": "session_state",
                "session_id": session_id,
                "state": SessionState.FAILED.value,
                "last_error": {"code": "SAFETY_BLOCK", "msg": safety_decision.reason},
            }
        )
        return _base_snapshot(osm, stream, memory)

    nav_result = pna.plan_and_execute(NavigationGoal(semantic_target="child_room", constraints={"avoid_private_rooms": False}))
    osm.append_event(
        OSMEvent(
            type="PNA_NAVIGATION_RESULT",
            session_id=session_id,
            payload={"success": nav_result.success, "path": nav_result.global_path, "reason": nav_result.reason},
        )
    )
    task_agent.submit_long_nav_task(session_id, "child_room")

    model_task = TaskSpec(name="intent-parse", task_type="model", priority=5)
    if scheduler.start(model_task):
        scheduler.finish(model_task)

    osm.append_event(OSMEvent(type="EXPLAIN_TRACE", session_id=session_id, payload={"stage": "intent_ingested", "evidence": intent["text"]}))

    osm.apply_patch({"type": "session_state", "session_id": session_id, "state": SessionState.PLANNING.value})
    context_packet = context_builder.build(session_id, intent, {"risk_class": "SAFE", "capabilities": ["NAV", "DIALOG"]})
    plan = planner.plan(context_packet)
    exec_graph = compiler.compile(plan)
    osm.append_event(
        OSMEvent(
            type="EXPLAIN_TRACE",
            session_id=session_id,
            payload={"stage": "plan_compiled", "plan_id": plan["plan_id"], "exec_graph_id": exec_graph["exec_graph_id"]},
        )
    )
    session = osm.session_projection[session_id]
    session.plan_id = plan["plan_id"]
    session.exec_graph_id = exec_graph["exec_graph_id"]
    session.trace_root = plan["trace_root"]
    session.state = SessionState.EXECUTING

    rt = kernel.restore_checkpoint(session.bt_checkpoint)
    _run_execution_loop(
        session=session,
        session_id=session_id,
        api=api,
        kernel=kernel,
        exec_graph=exec_graph,
        rt=rt,
        stream=stream,
        do_preempt=do_preempt,
        emit_target_gone=emit_target_gone,
        target_gone_payload=target_gone_payload,
        cancel_midway=cancel_midway,
    )
    return _final_snapshot(osm=osm, stream=stream, memory=memory, task_agent=task_agent, projector=projector)
