"""Kernel runtime loop and preemption orchestration.

This module is the main execution governor that performs deterministic ticks,
handles cancel/preempt convergence, and snapshots checkpoints.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
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
    """Execution kernel coordinating executor, actions, leases and OSM writes."""

    osm: OSMStore
    executor: Executor
    actions: ActionSupervisor
    leases: LeaseManager
    policy: PolicyGate
    spin_io: Optional[Callable[[], None]] = None
    max_session_runtime_ms: int = 30 * 60 * 1000
    stale_action_timeout_ms: int = 90 * 1000
    session_started_at: Dict[str, int] = field(default_factory=dict)

    def run_tick(self, session_id: str, exec_graph: Dict[str, Any], rt: RuntimeState) -> str:
        """Run one deterministic tick for a session execution graph."""
        if self.spin_io:
            self.spin_io()
        session = self.osm.session_projection[session_id]
        now = now_ms()
        self.session_started_at.setdefault(session_id, now)
        if now - self.session_started_at[session_id] > self.max_session_runtime_ms:
            self.actions.cancel_session(session_id, reason="max_session_runtime_exceeded")
            self._release_all(session_id)
            self.osm.apply_patch({"type": "session_state", "session_id": session_id, "state": SessionState.FAILED.value, "last_error": {"code": "MAX_RUNTIME", "msg": "session runtime exceeded budget"}})
            self.osm.append_event(OSMEvent(type="SESSION_STATE_CHANGED", session_id=session_id, payload={"state": "FAILED", "reason": "max_runtime"}))
            return FAILURE

        stale_actions = self.actions.stale_actions(session_id, now, self.stale_action_timeout_ms)
        if stale_actions:
            for aid in stale_actions:
                self.actions.cancel(aid, reason="stale_action_timeout")
            self._release_all(session_id)
            self.osm.apply_patch({"type": "session_state", "session_id": session_id, "state": SessionState.FAILED.value, "last_error": {"code": "ACTION_STALE", "msg": "action heartbeat/result timeout"}})
            self.osm.append_event(OSMEvent(type="SESSION_STATE_CHANGED", session_id=session_id, payload={"state": "FAILED", "reason": "action_stale"}))
            return FAILURE

        if session.state == SessionState.PAUSED:
            return "PAUSED"
        if session.state == SessionState.CANCELING:
            self.executor.halt_subtree(exec_graph["root"], rt)
            self.actions.cancel_session(session_id, reason="session_cancel")
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

    def preempt(
        self,
        low_session_id: str,
        high_session_id: str,
        mode: str = "PAUSE",
        low_exec_graph: Optional[Dict[str, Any]] = None,
        low_rt: Optional[RuntimeState] = None,
    ) -> None:
        """Perform two-phase preemption with fencing and resource handover."""
        low = self.osm.session_projection[low_session_id]
        high = self.osm.session_projection[high_session_id]

        self.osm.append_event(OSMEvent(type="PREEMPT_PHASE1_START", session_id=low_session_id, payload={"mode": mode, "high_session": high_session_id}))

        # epoch fence: all in-flight results from old epoch become stale
        low.action_epoch += 1
        self.actions.session_epoch[low_session_id] = low.action_epoch

        if low_exec_graph and low_rt:
            self.executor.halt_subtree(low_exec_graph["root"], low_rt)
        self.actions.cancel_session(low_session_id, reason="preempt")

        # after quiesce, resume from a clean deterministic checkpoint to avoid stale node cursors
        low.bt_checkpoint = self.snapshot_checkpoint(RuntimeState())

        # phase2 handover: release low-session resources; high session re-acquires explicitly
        for lid, lease in list(self.osm.lease_projection.items()):
            if lease.owner_session == low_session_id and lease.state == "HELD":
                self.leases.release(lid)


        if mode.upper() == "PAUSE":
            low.state = SessionState.PAUSED
            self.osm.append_event(OSMEvent(type="SESSION_STATE_CHANGED", session_id=low_session_id, payload={"state": "PAUSED", "reason": "preempted"}))
        else:
            low.state = SessionState.CANCELING
            self.osm.append_event(OSMEvent(type="SESSION_STATE_CHANGED", session_id=low_session_id, payload={"state": "CANCELING", "reason": "preempted"}))

        self.osm.append_event(OSMEvent(type="PREEMPT_PHASE2_COMPLETE", session_id=high_session_id, payload={"low_session": low_session_id}))
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
