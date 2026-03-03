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
