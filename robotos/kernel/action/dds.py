from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, List, Optional, Protocol
import json
import os

from robotos.models import ActionResult, now_ms


@dataclass
class ActionHeader:
    session_id: str
    plan_id: str
    action_id: str
    trace_id: str
    osm_version_hint: int = 0


@dataclass
class Goal:
    hdr: ActionHeader
    tool: str
    json_args: Dict[str, Any]


@dataclass
class Feedback:
    hdr: ActionHeader
    progress: float
    status_msg: str
    heartbeat_ts: int


@dataclass
class Result:
    hdr: ActionHeader
    status: str
    error_code: str = ""
    error_msg: str = ""
    json_artifacts: Dict[str, Any] | None = None


@dataclass
class Cancel:
    hdr: ActionHeader
    reason: str


Subscriber = Callable[[Dict[str, Any]], None]


class DDSBroker(Protocol):
    def subscribe(self, topic: str, cb: Subscriber) -> None: ...

    def publish(self, topic: str, payload: Dict[str, Any]) -> None: ...


class InMemoryDDSBroker:
    def __init__(self) -> None:
        self._subs: Dict[str, List[Subscriber]] = {}

    def subscribe(self, topic: str, cb: Subscriber) -> None:
        self._subs.setdefault(topic, []).append(cb)

    def publish(self, topic: str, payload: Dict[str, Any]) -> None:
        for cb in self._subs.get(topic, []):
            cb(payload)


class CycloneDDSBroker:
    """CycloneDDS-backed broker, enabled when cyclonedds Python package exists."""

    def __init__(self) -> None:
        try:
            from cyclonedds.domain import DomainParticipant
            from cyclonedds.topic import Topic
            from cyclonedds.pub import Publisher, DataWriter
            from cyclonedds.sub import Subscriber, DataReader
            from cyclonedds.idl import IdlStruct
            from cyclonedds.idl.types import string
        except Exception as exc:  # pragma: no cover - optional runtime dependency
            raise RuntimeError("cyclonedds package not available") from exc

        class Msg(IdlStruct, typename="robotos::dds::Msg"):
            topic: string
            payload: string

        self._participant = DomainParticipant()
        self._topic = Topic(self._participant, "robotos_action_bus", Msg)
        self._publisher = Publisher(self._participant)
        self._writer = DataWriter(self._publisher, self._topic)
        self._subscriber = Subscriber(self._participant)
        self._reader = DataReader(self._subscriber, self._topic)
        self._subs: Dict[str, List[Subscriber]] = {}

    def subscribe(self, topic: str, cb: Subscriber) -> None:
        self._subs.setdefault(topic, []).append(cb)

    def publish(self, topic: str, payload: Dict[str, Any]) -> None:
        msg = self._topic.data_type(topic=topic, payload=json.dumps(payload, ensure_ascii=False))
        self._writer.write(msg)
        self.spin_once()

    def spin_once(self) -> None:
        samples = self._reader.take(N=64)
        for sample in samples:
            for cb in self._subs.get(sample.topic, []):
                cb(json.loads(sample.payload))


class FastDDSBroker:
    """Hook for FastDDS integration; currently requires external bridge implementation."""

    def __init__(self) -> None:
        raise RuntimeError("FastDDS Python broker bridge is not bundled; implement org-specific adapter.")

    def subscribe(self, topic: str, cb: Subscriber) -> None:  # pragma: no cover
        _ = topic, cb

    def publish(self, topic: str, payload: Dict[str, Any]) -> None:  # pragma: no cover
        _ = topic, payload


def create_broker(backend: str | None = None) -> DDSBroker:
    selected = (backend or os.getenv("ROBOTOS_DDS_BACKEND") or "inmemory").lower()
    if selected == "inmemory":
        return InMemoryDDSBroker()
    if selected == "cyclonedds":
        return CycloneDDSBroker()
    if selected == "fastdds":
        return FastDDSBroker()
    raise ValueError(f"unsupported DDS backend: {selected}")


def _prefix(tool: str) -> str:
    return f"/{tool.replace('.', '_')}/action"


class DDSActionClient:
    def __init__(self, broker: DDSBroker) -> None:
        self.broker = broker
        self._results: Dict[str, Result] = {}
        self._feedback: Dict[str, Feedback] = {}

    def send_goal(self, goal: Goal) -> None:
        prefix = _prefix(goal.tool)
        self.broker.subscribe(f"{prefix}/feedback", self._on_feedback)
        self.broker.subscribe(f"{prefix}/result", self._on_result)
        self.broker.publish(f"{prefix}/goal", asdict(goal))

    def cancel(self, tool: str, cancel: Cancel) -> None:
        self.broker.publish(f"{_prefix(tool)}/cancel", asdict(cancel))

    def poll_result(self, action_id: str) -> Optional[Result]:
        return self._results.pop(action_id, None)

    def poll_feedback(self, action_id: str) -> Optional[Feedback]:
        return self._feedback.get(action_id)

    def _on_feedback(self, payload: Dict[str, Any]) -> None:
        hdr = ActionHeader(**payload["hdr"])
        self._feedback[hdr.action_id] = Feedback(hdr=hdr, progress=payload["progress"], status_msg=payload["status_msg"], heartbeat_ts=payload["heartbeat_ts"])

    def _on_result(self, payload: Dict[str, Any]) -> None:
        hdr = ActionHeader(**payload["hdr"])
        self._results[hdr.action_id] = Result(
            hdr=hdr,
            status=payload["status"],
            error_code=payload.get("error_code", ""),
            error_msg=payload.get("error_msg", ""),
            json_artifacts=payload.get("json_artifacts"),
        )


class TimedSkillServer:
    def __init__(self, broker: DDSBroker, tool: str, duration_ms: int, fail: bool = False) -> None:
        self.broker = broker
        self.tool = tool
        self.duration_ms = duration_ms
        self.fail = fail
        self.jobs: Dict[str, Dict[str, Any]] = {}
        self._register()

    def _register(self) -> None:
        prefix = _prefix(self.tool)
        self.broker.subscribe(f"{prefix}/goal", self._on_goal)
        self.broker.subscribe(f"{prefix}/cancel", self._on_cancel)

    def _on_goal(self, payload: Dict[str, Any]) -> None:
        hdr = payload["hdr"]
        aid = hdr["action_id"]
        self.jobs[aid] = {"elapsed": 0, "hdr": hdr}

    def _on_cancel(self, payload: Dict[str, Any]) -> None:
        aid = payload["hdr"]["action_id"]
        if aid in self.jobs:
            hdr = self.jobs[aid]["hdr"]
            del self.jobs[aid]
            self.broker.publish(f"{_prefix(self.tool)}/result", {"hdr": hdr, "status": "CANCELED", "error_code": "", "error_msg": ""})

    def spin_once(self, step_ms: int = 200) -> None:
        for aid, st in list(self.jobs.items()):
            st["elapsed"] += step_ms
            hdr = st["hdr"]
            self.broker.publish(
                f"{_prefix(self.tool)}/feedback",
                {"hdr": hdr, "progress": min(st["elapsed"] / self.duration_ms, 1.0), "status_msg": "RUNNING", "heartbeat_ts": now_ms()},
            )
            if st["elapsed"] >= self.duration_ms:
                del self.jobs[aid]
                status = "FAILED" if self.fail else "SUCCEEDED"
                self.broker.publish(f"{_prefix(self.tool)}/result", {"hdr": hdr, "status": status, "error_code": "SIM_FAIL" if self.fail else "", "error_msg": ""})


def result_to_action_result(result: Result) -> ActionResult:
    return ActionResult(status=result.status, error_code=result.error_code, error_msg=result.error_msg, artifacts=result.json_artifacts or {})
