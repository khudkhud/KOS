import os
from pathlib import Path

from robotos.app import BuildOptions, EmbodimentProfile, build, build_system, run_demo
from robotos.control.contracts import BehaviorContract, MotionContract, TaskContract, validate_stack
from robotos.demo_agent_comm import run_agent_comm_demo
from robotos.kernel.osm.store import OSMStore


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

def test_layer_contract_validation():
    stack = validate_stack(
        TaskContract(task_type="safety_patrol", max_latency_ms=120000, safety_level="HOUSEHOLD", degrade_policy="retry"),
        BehaviorContract(behavior_id="navigate_scan", expected_error_codes=["NAV_TIMEOUT"], timeout_ms=30000),
        MotionContract(controller="local_planner", max_velocity=0.4, obstacle_clearance_m=0.2),
    )
    assert stack["task"].task_type == "safety_patrol"

def test_run_demo_has_explain_trace_and_memory_snapshot():
    out = run_demo()
    assert any(e["type"] == "EXPLAIN_TRACE" for e in out["events"])
    assert out["memory"]["long_term_users"] >= 1
    assert isinstance(out["governance"], list)
    assert out["world_projection"]["session_count"] >= 1
    assert isinstance(out["task_reports"], list)

def test_long_range_navigation_agent_orchestration():
    sys = build_system()
    api = sys["api"]
    task_agent = sys["task_agent"]
    stream = sys["stream"]

    session_id = api.post_sessions({"owner": "voice", "capabilities": ["NAV", "DIALOG"], "preemption_policy": "PAUSEABLE"})["session_id"]
    task_agent.submit_long_nav_task(session_id, "child_room")

    assert any(m["topic"] == "NAV_EXEC_DONE" and m["type"] == "Decision" for m in stream.history)
    assert len(task_agent.task_reports) >= 1

