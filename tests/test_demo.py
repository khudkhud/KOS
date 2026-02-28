import os
from pathlib import Path

from robotos.app import run_demo
from robotos.control.message.agents import build_default_agent_registry
from robotos.control.message.stream import MessageStream
from robotos.demo_agent_comm import run_agent_comm_demo
from robotos.kernel.osm.store import OSMStore
from robotos.kernel.policy.gate import ToolRegistry
from robotos.models import Message
from robotos.schema_validate import SchemaValidationError


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
