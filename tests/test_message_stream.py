import os
from pathlib import Path

from robotos.control.message.agents import build_default_agent_registry
from robotos.control.message.stream import MessageStream
from robotos.control.strategy.plugin import StrategyPlugin
from robotos.models import Message
from robotos.schema_validate import SchemaValidationError, validate


def test_message_schema_validation():
    stream = MessageStream()
    bad = Message(type="NotAllowed", topic="X")
    try:
        stream.publish(bad)
        raise AssertionError("expected schema validation failure")
    except SchemaValidationError:
        pass

def test_message_stream_persist_and_recover(tmp_path: Path):
    persist = tmp_path / "messages.jsonl"
    stream1 = MessageStream(registry=build_default_agent_registry(), persist_path=str(persist))
    stream1.publish(Message(type="Event", topic="TARGET_GONE", session_id="S-1", payload={"target": "son"}), sender="monitor_agent")

    # new process instance can recover history from disk
    stream2 = MessageStream(registry=build_default_agent_registry(), persist_path=str(persist))
    assert len(stream2.history) == 1
    assert stream2.history[0]["topic"] == "TARGET_GONE"

def test_message_stream_poll_new_for_cross_process_ipc(tmp_path: Path):
    persist = tmp_path / "messages.jsonl"
    producer = MessageStream(registry=build_default_agent_registry(), persist_path=str(persist))
    consumer = MessageStream(registry=build_default_agent_registry(), persist_path=str(persist))

    seen: list[str] = []
    consumer.subscribe("SUGGEST_REPLAN", lambda msg: seen.append(msg.topic), agent_id="planner")

    producer.publish(Message(type="Event", topic="SUGGEST_REPLAN", session_id="S-1", payload={"reason": "new_obstacle"}), sender="monitor_agent")
    consumed = consumer.poll_new()

    assert consumed == 1
    assert seen == ["SUGGEST_REPLAN"]

def test_message_stream_outbox_replay_and_idempotent_consume(tmp_path: Path):
    persist = tmp_path / "messages.jsonl"
    producer = MessageStream(registry=build_default_agent_registry(), persist_path=str(persist))
    consumer = MessageStream(registry=build_default_agent_registry(), persist_path=str(persist))

    seen: list[str] = []
    consumer.subscribe("SUGGEST_REPLAN", lambda msg: seen.append(msg.correlation_id), agent_id="planner")

    msg = Message(type="Event", topic="SUGGEST_REPLAN", session_id="S-1", payload={"reason": "new_obstacle"}, correlation_id="C-fixed")
    producer.publish(msg, sender="monitor_agent")

    # poll as durable consumer, then repoll should be idempotent due to key state
    assert consumer.poll_new(consumer_id="planner_proc") == 1
    assert consumer.poll_new(consumer_id="planner_proc") == 0
    assert seen == ["C-fixed"]

    # replay API provides event-log readback
    replayed = consumer.replay(from_line=0, dispatch=False)
    assert len(replayed) >= 1
    assert replayed[0]["idempotency_key"] == "C-fixed"

def test_schema_validation_engine_rejects_nested_type_violation():
    bad_message = {
        "type": "Event",
        "topic": "TARGET_GONE",
        "correlation_id": "C-1",
        "payload": [],  # must be object
        "ts": 1,
    }
    try:
        validate(bad_message, "message.schema.json")
        raise AssertionError("expected schema validation failure")
    except SchemaValidationError:
        pass

def test_agent_publish_permission_guard():
    stream = MessageStream(registry=build_default_agent_registry())
    try:
        stream.publish(Message(type="Request", topic="REQ_CANCEL", session_id="S-1", payload={}), sender="monitor_agent")
        raise AssertionError("expected permission error")
    except PermissionError:
        pass

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

