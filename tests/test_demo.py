import os
from pathlib import Path

from robotos.app import BuildOptions, EmbodimentProfile, build, run_demo
from robotos.control.contracts import BehaviorContract, MotionContract, TaskContract, validate_stack
from robotos.control.message.agents import build_default_agent_registry
from robotos.control.message.stream import MessageStream
from robotos.control.memory.store import MemoryStore
from robotos.control.strategy.plugin import StrategyPlugin
from robotos.control.world_memory import OSMWorldProjector
from robotos.demo_agent_comm import run_agent_comm_demo
from robotos.embodied import NavigationGoal, PathNavigationAgent, RobotStateEstimator, SafetySupervisor
from robotos.kernel.osm.store import OSMStore
from robotos.kernel.policy.gate import ToolRegistry
from robotos.models import Message
from robotos.schema_validate import SchemaValidationError
from robotos.skills.ai_model_skills import DepthAnythingSkill, NavDPSkill, RoboBrainSkill, YOLOPerceptionServiceSkill


def test_demo_success():
    out = run_demo(cancel_midway=False)
    assert out["session"]["state"] == "SUCCEEDED"
    assert any(e["type"] == "ACTION_RESULT" for e in out["events"])
    assert any(e["type"] == "ACTION_FEEDBACK" for e in out["events"])


def test_demo_cancel():
    out = run_demo(cancel_midway=True)
    assert out["session"]["state"] == "CANCELED"


def test_preempt_pause_resume_two_phase():
    out = run_demo(do_preempt=True)
    assert out["session"]["state"] == "SUCCEEDED"
    assert any(e["type"] == "PREEMPT_PHASE1_START" for e in out["events"])
    assert any(e["type"] == "PREEMPT_PHASE2_COMPLETE" for e in out["events"])
    assert any(e["type"] == "SESSION_STATE_CHANGED" and e["payload"].get("state") == "PAUSED" for e in out["events"])


def test_target_gone_from_mother_auto_cancel():
    out = run_demo(emit_target_gone=True)
    assert out["session"]["state"] == "CANCELED"
    assert any(e["type"] == "REQUEST_ENQUEUED" and e["payload"].get("topic") == "REQ_CANCEL" for e in out["events"])


def test_target_gone_low_confidence_not_cancel():
    out = run_demo(emit_target_gone=True, target_gone_payload={"target": "son", "source": "mother", "confidence": 0.4})
    assert out["session"]["state"] == "SUCCEEDED"


def test_target_gone_wrong_target_not_cancel():
    out = run_demo(emit_target_gone=True, target_gone_payload={"target": "daughter", "source": "mother", "confidence": 0.95})
    assert out["session"]["state"] == "SUCCEEDED"


def test_message_schema_validation():
    stream = MessageStream()
    bad = Message(type="NotAllowed", topic="X")
    try:
        stream.publish(bad)
        raise AssertionError("expected schema validation failure")
    except SchemaValidationError:
        pass


def test_tool_registry_file_load():
    reg = ToolRegistry.from_json_file("tool_registry.json")
    assert reg.get("nav.goto").capability == "NAV"


def test_osm_persist_and_rebuild(tmp_path: Path):
    persist = tmp_path / "events.jsonl"
    os.environ["ROBOTOS_OSM_PERSIST"] = str(persist)
    try:
        out = run_demo(cancel_midway=False)
        assert out["session"]["state"] == "SUCCEEDED"
        assert persist.exists()

        rebuilt = OSMStore(persist_path=str(persist))
        snap = rebuilt.get()
        assert len(snap["session_projection"]) >= 1
        assert len(snap["action_projection"]) >= 1
    finally:
        os.environ.pop("ROBOTOS_OSM_PERSIST", None)


def test_agent_publish_permission_guard():
    stream = MessageStream(registry=build_default_agent_registry())
    try:
        stream.publish(Message(type="Request", topic="REQ_CANCEL", session_id="S-1", payload={}), sender="monitor_agent")
        raise AssertionError("expected permission error")
    except PermissionError:
        pass


def test_agent_comm_demo_visualization():
    out = run_agent_comm_demo()
    assert out["session"]["state"] == "CANCELED"
    assert "sequenceDiagram" in out["mermaid"]
    assert any(m["topic"] == "TARGET_GONE" for m in out["messages"])


def test_build_assessment_report():
    out = build(BuildOptions(dds_backend="inmemory"))
    assert out["assessment"]["professionalism"]["typed_build_options"] is True
    assert out["assessment"]["practicality"]["dds_backend"] == "inmemory"
    assert "kernel" in out["components"]
    assert out["assessment"]["extensibility"]["pluggable_dds_backend"] is True


def test_build_embodiment_profile():
    out = build(BuildOptions(embodiment=EmbodimentProfile(max_concurrent_actions=1, max_session_runtime_ms=123000, stale_action_timeout_ms=45000, min_battery_percent_to_start=25)))
    rel = out["assessment"]["embodied_reliability"]
    assert rel["max_concurrent_actions"] == 1
    assert rel["max_session_runtime_ms"] == 123000
    assert rel["stale_action_timeout_ms"] == 45000
    assert rel["min_battery_percent_to_start"] == 25


def test_tool_registry_discovery_and_negotiate():
    reg = ToolRegistry.from_json_file("tool_registry.json")
    dialog_tools = reg.discover(capability="DIALOG")
    assert any(t.tool == "dialog.say" for t in dialog_tools)
    spec = reg.negotiate("nav.goto", accepted_contracts=["1.1", "1.0"])
    assert spec.contract_version == "1.1"


def test_governance_bus_records_responsibility_chain():
    stream = MessageStream(registry=build_default_agent_registry())

    called = {"cancel": False}

    def on_replan(_: str) -> None:
        return

    def on_cancel(_: str, __: str) -> None:
        called["cancel"] = True

    StrategyPlugin(stream, on_replan=on_replan, on_cancel=on_cancel, agent_id="strategy")
    stream.publish(Message(type="Event", topic="TARGET_GONE", session_id="S-1", payload={"target": "son", "source": "mother", "confidence": 0.95}), sender="monitor_agent")

    assert called["cancel"] is True
    assert any(x["type"] == "Decision" and x["topic"] == "SUGGEST_CANCEL" for x in stream.governance_log)
    chain = [x for x in stream.governance_log if x["topic"] == "SUGGEST_CANCEL"][0]["payload"]["responsibility_chain"]
    assert chain["proposer"] == "strategy"


def test_run_demo_has_explain_trace_and_memory_snapshot():
    out = run_demo()
    assert any(e["type"] == "EXPLAIN_TRACE" for e in out["events"])
    assert out["memory"]["long_term_users"] >= 1
    assert isinstance(out["governance"], list)
    assert out["world_projection"]["session_count"] >= 1


def test_layer_contract_validation():
    stack = validate_stack(
        TaskContract(task_type="safety_patrol", max_latency_ms=120000, safety_level="HOUSEHOLD", degrade_policy="retry"),
        BehaviorContract(behavior_id="navigate_scan", expected_error_codes=["NAV_TIMEOUT"], timeout_ms=30000),
        MotionContract(controller="local_planner", max_velocity=0.4, obstacle_clearance_m=0.2),
    )
    assert stack["task"].task_type == "safety_patrol"


def test_pna_and_safety_supervisor_components():
    memory = MemoryStore()
    pna = PathNavigationAgent(memory)
    nav = pna.plan_and_execute(NavigationGoal(semantic_target="entrance"))
    assert nav.success is True

    est = RobotStateEstimator()
    est.update(battery_percent=10)
    safety = SafetySupervisor(min_battery_percent=15)
    decision = safety.evaluate(est.snapshot())
    assert decision.allow_execute is False


def test_osm_world_projection_pipeline():
    memory = MemoryStore()
    projector = OSMWorldProjector(memory)
    report = projector.project([
        {"type": "SESSION_STATE_CHANGED", "session_id": "S-1", "payload": {"state": "EXECUTING"}},
        {"type": "ACTION_RESULT", "session_id": "S-1", "payload": {"status": "SUCCEEDED", "error_code": ""}},
    ])
    assert report["session_count"] == 1
    assert memory.read_world_fact("session_summary")["S-1"]["state"] == "EXECUTING"


def test_ai_model_skill_stubs():
    yolo = YOLOPerceptionServiceSkill().infer({"frame": "mock"})
    depth = DepthAnythingSkill().infer({"frame": "mock"})
    navdp = NavDPSkill().infer({"rgb": "mock", "goal": "kitchen"})
    brain = RoboBrainSkill().infer({"intent": "去厨房看看"})

    assert yolo["mode"] == "periodic_service"
    assert depth["mode"] == "on_demand_skill"
    assert navdp["result"]["waypoint"].startswith("stub_")
    assert brain["result"]["task_graph"].startswith("stub_")


def test_registry_contains_ai_network_tools():
    reg = ToolRegistry.from_json_file("tool_registry.json")
    assert reg.get("perception.yolo.detect").capability == "PERCEPTION"
    assert reg.get("perception.depth_anything.estimate").capability == "PERCEPTION"
    assert reg.get("navigation.navdp.predict_waypoint").capability == "NAV"
    assert reg.get("planning.robobrain.plan").capability == "PLANNING"


def test_hpu_resources_unified_in_registry():
    reg = ToolRegistry.from_json_file("tool_registry.json")
    assert reg.get("perception.depth_anything.estimate").required_resources == ["hpu"]
    assert reg.get("navigation.navdp.predict_waypoint").required_resources == ["hpu"]
    assert reg.get("planning.robobrain.plan").required_resources == ["hpu"]
