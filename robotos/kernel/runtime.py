from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any, Callable, Dict, Optional

from robotos.kernel.action.supervisor import ActionSupervisor
from robotos.kernel.executor.engine import Executor, FAILURE, RuntimeState, SUCCESS
from robotos.kernel.lease.manager import LeaseManager
from robotos.kernel.osm.store import OSMStore
from robotos.kernel.policy.gate import PolicyGate
from robotos.models import OSMEvent, SessionState, now_ms


@dataclass
class Kernel:
    osm: OSMStore
    executor: Executor
    actions: ActionSupervisor
    leases: LeaseManager
    policy: PolicyGate
    spin_io: Optional[Callable[[], None]] = None

    def run_tick(self, session_id: str, exec_graph: Dict[str, Any], rt: RuntimeState) -> str:
        if self.spin_io:
            self.spin_io()
        session = self.osm.session_projection[session_id]
        if session.state == SessionState.PAUSED:
            return "PAUSED"
        if session.state == SessionState.CANCELING:
            self.executor.halt_subtree(exec_graph["root"], rt)
            self._release_all(session_id)
            self.osm.apply_patch({"type": "session_state", "session_id": session_id, "state": SessionState.CANCELED.value})
            self.osm.append_event(OSMEvent(type="SESSION_STATE_CHANGED", session_id=session_id, payload={"state": "CANCELED"}))
            return FAILURE

        st = self.executor.tick(exec_graph["root"], session, rt, now_ms())
        session.bt_checkpoint = self.snapshot_checkpoint(rt)
        self.osm.append_event(OSMEvent(type="KERNEL_TICK", session_id=session_id, payload={"status": st}))
        if st == SUCCESS:
            self._release_all(session_id)
            self.osm.apply_patch({"type": "session_state", "session_id": session_id, "state": SessionState.SUCCEEDED.value})
        elif st == FAILURE:
            self._release_all(session_id)
            self.osm.apply_patch({"type": "session_state", "session_id": session_id, "state": SessionState.FAILED.value, "last_error": {"code": "EXEC_FAIL", "msg": "graph failed"}})
        return st

    def preempt(self, low_session_id: str, high_session_id: str, mode: str = "PAUSE") -> None:
        low = self.osm.session_projection[low_session_id]
        if mode.upper() == "PAUSE":
            low.state = SessionState.PAUSED
            self.osm.append_event(OSMEvent(type="SESSION_STATE_CHANGED", session_id=low_session_id, payload={"state": "PAUSED", "reason": "preempted"}))
        else:
            low.state = SessionState.CANCELING
            self.osm.append_event(OSMEvent(type="SESSION_STATE_CHANGED", session_id=low_session_id, payload={"state": "CANCELING", "reason": "preempted"}))
        high = self.osm.session_projection[high_session_id]
        high.state = SessionState.EXECUTING
        self.osm.append_event(OSMEvent(type="SESSION_STATE_CHANGED", session_id=high_session_id, payload={"state": "EXECUTING", "reason": "preempt_win"}))

    def snapshot_checkpoint(self, rt: RuntimeState) -> str:
        return json.dumps(asdict(rt), ensure_ascii=False)

    def restore_checkpoint(self, checkpoint: str | None) -> RuntimeState:
        if not checkpoint:
            return RuntimeState()
        data = json.loads(checkpoint)
        return RuntimeState(
            cursor=data.get("cursor", {}),
            active_action=data.get("active_action", {}),
            retries=data.get("retries", {}),
            leases_by_node=data.get("leases_by_node", {}),
            time_start=data.get("time_start", {}),
        )

    def _release_all(self, session_id: str) -> None:
        for lid, lease in list(self.osm.lease_projection.items()):
            if lease.owner_session == session_id and lease.state == "HELD":
                self.leases.release(lid)
