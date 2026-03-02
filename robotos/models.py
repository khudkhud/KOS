from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
import time
import uuid


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


class SessionState(str, Enum):
    CREATED = "CREATED"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    PAUSED = "PAUSED"
    CANCELING = "CANCELING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


@dataclass
class Session:
    session_id: str
    owner: str
    priority: int = 0
    preemption_policy: str = "ALLOW"
    capabilities: List[str] = field(default_factory=list)
    risk_class: str = "SAFE"
    plan_id: Optional[str] = None
    exec_graph_id: Optional[str] = None
    bt_checkpoint: Optional[str] = None
    trace_root: Optional[str] = None
    state: SessionState = SessionState.CREATED
    last_error: Dict[str, str] = field(default_factory=lambda: {"code": "", "msg": ""})
    action_epoch: int = 0


@dataclass
class Message:
    type: str
    topic: str
    severity: Optional[str] = None
    session_id: Optional[str] = None
    correlation_id: str = field(default_factory=lambda: new_id("C"))
    trace_id: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    ts: int = field(default_factory=now_ms)


@dataclass
class OSMEvent:
    type: str
    payload: Dict[str, Any]
    session_id: Optional[str] = None
    plan_id: Optional[str] = None
    action_id: Optional[str] = None
    event_id: str = field(default_factory=lambda: new_id("E"))
    ts: int = field(default_factory=now_ms)


@dataclass
class Lease:
    lease_id: str
    resource: str
    owner_session: str
    ttl_ms: int
    expires_at: int
    state: str = "HELD"


@dataclass
class ActionResult:
    status: str
    error_code: str = ""
    error_msg: str = ""
    artifacts: Dict[str, Any] = field(default_factory=dict)
