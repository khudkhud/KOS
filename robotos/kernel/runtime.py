from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from robotos.kernel.action.supervisor import ActionSupervisor
from robotos.kernel.executor.engine import Executor, FAILURE, RUNNING, SUCCESS, RuntimeState
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

    def run_tick(self, session_id: str, exec_graph: Dict[str, Any], rt: RuntimeState) -> str:
        session = self.osm.session_projection[session_id]
        if session.state == SessionState.CANCELING:
            self.executor.halt_subtree(exec_graph["root"], rt)
            self._release_all(session_id)
            self.osm.apply_patch({"type": "session_state", "session_id": session_id, "state": SessionState.CANCELED.value})
            self.osm.append_event(OSMEvent(type="SESSION_STATE_CHANGED", session_id=session_id, payload={"state": "CANCELED"}))
            return FAILURE

        st = self.executor.tick(exec_graph["root"], session, rt, now_ms())
        self.osm.append_event(OSMEvent(type="KERNEL_TICK", session_id=session_id, payload={"status": st}))
        if st == SUCCESS:
            self._release_all(session_id)
            self.osm.apply_patch({"type": "session_state", "session_id": session_id, "state": SessionState.SUCCEEDED.value})
        elif st == FAILURE:
            self._release_all(session_id)
            self.osm.apply_patch({"type": "session_state", "session_id": session_id, "state": SessionState.FAILED.value, "last_error": {"code": "EXEC_FAIL", "msg": "graph failed"}})
        return st

    def _release_all(self, session_id: str) -> None:
        for lid, lease in list(self.osm.lease_projection.items()):
            if lease.owner_session == session_id and lease.state == "HELD":
                self.leases.release(lid)
