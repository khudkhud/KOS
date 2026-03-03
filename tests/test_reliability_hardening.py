import os

from fastapi import HTTPException

from robotos.app import build_system
from robotos.control.api.http import _authorize
from robotos.control.message.agents import build_default_agent_registry
from robotos.control.message.stream import MessageStream
from robotos.kernel.lease.manager import LeaseManager
from robotos.kernel.osm.store import OSMStore
from robotos.models import Message


def test_lease_guardian_sweep_and_restart_recovery():
    osm = OSMStore()
    mgr = LeaseManager(osm)
    [lease] = mgr.acquire(["hpu"], "S-1", ttl_ms=10)

    # simulate restart: new manager rebuilds in-memory index from projection
    mgr2 = LeaseManager(osm)
    assert mgr2.by_resource["hpu"] == lease.lease_id

    expired = mgr2.sweep_expired(now=lease.expires_at + 1)
    assert expired == 1
    assert "hpu" not in mgr2.by_resource


def test_control_plane_audit_events_record_actor():
    sys = build_system()
    api = sys["api"]
    osm = sys["osm"]

    sid = api.post_sessions({"owner": "voice", "capabilities": ["NAV"]}, actor="alice")["session_id"]
    api.post_cancel(sid, actor="alice")

    audit = [e for e in osm.event_log if e.type == "CONTROL_AUDIT" and e.payload.get("actor") == "alice"]
    assert any(e.payload.get("action") == "cancel" for e in audit)


def test_http_api_authn_authz_baseline():
    try:
        _authorize(None, "get_session")
        raise AssertionError("expected unauthorized")
    except HTTPException as exc:
        assert exc.status_code == 401

    os.environ["ROBOTOS_API_KEYS"] = '{"viewer-key":{"actor":"bob","role":"viewer"}}'
    try:
        actor = _authorize("viewer-key", "get_session")
        assert actor == "bob"
        try:
            _authorize("viewer-key", "cancel")
            raise AssertionError("expected forbidden")
        except HTTPException as exc:
            assert exc.status_code == 403
    finally:
        os.environ.pop("ROBOTOS_API_KEYS", None)


def test_message_stream_idempotent_consumer_contract(tmp_path):
    persist = tmp_path / "messages.jsonl"
    producer = MessageStream(registry=build_default_agent_registry(), persist_path=str(persist))
    consumer = MessageStream(registry=build_default_agent_registry(), persist_path=str(persist))
    producer.publish(
        Message(type="Event", topic="SUGGEST_REPLAN", session_id="S-1", payload={"x": 1}, correlation_id="idem-1"),
        sender="monitor_agent",
    )
    # duplicate write with same idempotency key is allowed in log, but consumer remains idempotent
    producer.publish(
        Message(type="Event", topic="SUGGEST_REPLAN", session_id="S-1", payload={"x": 1}, correlation_id="idem-1"),
        sender="monitor_agent",
    )

    assert consumer.poll_new(consumer_id="planner") == 1
    assert consumer.poll_new(consumer_id="planner") == 0


def test_resource_policy_camera_is_subscription_not_lease():
    osm = OSMStore()
    mgr = LeaseManager(osm)
    leases = mgr.acquire(["camera"], "S-1")
    assert leases == []
    assert "camera" not in mgr.by_resource
    assert any(e.type == "LEASE_BYPASSED" and e.payload.get("resource") == "camera" for e in osm.event_log)


def test_resource_policy_defaults_for_hpu_and_base():
    osm = OSMStore()
    mgr = LeaseManager(osm)
    hpu = mgr.policy_for("hpu")
    base = mgr.policy_for("base")
    assert hpu.exclusive is True and hpu.timeout_mode == "hard" and hpu.on_expire == "fail"
    assert hpu.wait_soft_ms == 1000 and hpu.wait_hard_ms == 1000
    assert base.exclusive is True and base.timeout_mode == "hard" and base.on_expire == "fail"
    mic = mgr.policy_for("mic")
    speaker = mgr.policy_for("speaker")
    assert mic.lease_required is False and mic.exclusive is False
    assert speaker.lease_required is True and speaker.exclusive is True and speaker.timeout_mode == "hard"


def test_resource_policy_microphone_is_subscription_not_lease():
    osm = OSMStore()
    mgr = LeaseManager(osm)
    leases = mgr.acquire(["mic"], "S-1")
    assert leases == []
    assert "mic" not in mgr.by_resource
    assert any(e.type == "LEASE_BYPASSED" and e.payload.get("resource") == "mic" for e in osm.event_log)


def test_resource_policy_speaker_is_exclusive_and_fails_fast():
    osm = OSMStore()
    mgr = LeaseManager(osm)
    mgr.acquire(["speaker"], "S-owner")
    try:
        mgr.acquire(["speaker"], "S-other")
        raise AssertionError("expected speaker busy")
    except RuntimeError as exc:
        assert str(exc).startswith("SPEAKER_BUSY")



def test_resource_policy_npu_cpu_defaults():
    osm = OSMStore()
    mgr = LeaseManager(osm)
    npu = mgr.policy_for("npu")
    cpu = mgr.policy_for("cpu")
    assert npu.lease_required is True and npu.exclusive is True
    assert cpu.lease_required is False and cpu.exclusive is False


def test_unknown_resource_policy_fails_fast():
    osm = OSMStore()
    mgr = LeaseManager(osm)
    try:
        mgr.policy_for("custom_accelerator")
        raise AssertionError("expected unknown resource policy error")
    except KeyError as exc:
        assert "unknown resource policy" in str(exc)


def test_admission_allows_intent_without_planned_tools():
    sys = build_system()
    api = sys["api"]
    osm = sys["osm"]

    sid = api.post_sessions({"owner": "voice", "capabilities": ["NAV"]})["session_id"]
    api.post_submit_intent(sid, {"goal": "go_home"})

    assert any(item.get("session_id") == sid for item in osm.intent_queue)
    assert not any(e.type == "ADMISSION_REJECTED" and e.session_id == sid for e in osm.event_log)


def test_admission_rejects_only_unknown_tools():
    sys = build_system()
    api = sys["api"]
    osm = sys["osm"]

    sid = api.post_sessions({"owner": "voice", "capabilities": ["NAV"]})["session_id"]
    api.post_submit_intent(
        sid,
        {
            "goal": "do_task",
            "planned_tools": ["unknown.tool"],
            "latency_budget_ms": 1,
        },
    )

    assert not any(item.get("session_id") == sid for item in osm.intent_queue)
    rej = [e for e in osm.event_log if e.type == "ADMISSION_REJECTED" and e.session_id == sid]
    assert len(rej) == 1
    assert rej[0].payload.get("reason") == "unknown_tools"


def test_admission_no_longer_rejects_based_on_latency_budget():
    sys = build_system()
    api = sys["api"]
    osm = sys["osm"]

    sid = api.post_sessions({"owner": "voice", "capabilities": ["NAV"]})["session_id"]
    api.post_submit_intent(
        sid,
        {
            "goal": "tight_budget",
            "planned_tools": ["planning.robobrain.plan"],
            "latency_budget_ms": 1,
        },
    )

    assert any(item.get("session_id") == sid for item in osm.intent_queue)
    assert not any(e.type == "ADMISSION_REJECTED" and e.session_id == sid for e in osm.event_log)
