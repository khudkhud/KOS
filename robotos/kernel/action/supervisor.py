"""Action supervision bridge between executor and DDS action transport.

Provides goal dispatch, feedback/result polling, cancel, and epoch fencing to
avoid stale-result contamination after preempt/cancel.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from robotos.kernel.action.dds import ActionHeader, Cancel, DDSActionClient, Goal, result_to_action_result
from robotos.kernel.lease.manager import LeaseManager
from robotos.kernel.osm.store import OSMStore
from robotos.kernel.scheduler.mixed import MixedWorkloadScheduler, TaskSpec
from robotos.models import ActionResult, OSMEvent, new_id, now_ms


@dataclass
class ActionHandle:
    action_id: str
    session_id: str
    tool: str
    args: Dict[str, Any]
    action_epoch: int
    state: str = "GOAL_SENT"
    started_at: int = 0


class ActionSupervisor:
    """Supervise action lifecycle and protect against stale epochs."""

    def __init__(
        self,
        osm: OSMStore,
        dds_client: DDSActionClient,
        max_concurrent_actions: int = 2,
        scheduler: MixedWorkloadScheduler | None = None,
        leases: LeaseManager | None = None,
    ) -> None:
        self.osm = osm
        self.dds_client = dds_client
        self.max_concurrent_actions = max_concurrent_actions
        self.scheduler = scheduler
        self.leases = leases
        self.active: Dict[str, ActionHandle] = {}
        self.session_epoch: Dict[str, int] = {}
        self._model_jobs: Dict[str, TaskSpec] = {}

    def next_epoch(self, session_id: str) -> int:
        self.session_epoch[session_id] = self.session_epoch.get(session_id, 0) + 1
        return self.session_epoch[session_id]

    def current_epoch(self, session_id: str) -> int:
        return self.session_epoch.get(session_id, self.osm.session_projection.get(session_id).action_epoch if session_id in self.osm.session_projection else 0)

    def _is_hpu_model_tool(self, tool: str) -> bool:
        return tool in {
            "perception.depth_anything.estimate",
            "navigation.navdp.predict_waypoint",
            "planning.robobrain.plan",
        }

    def can_dispatch(self, session_id: str) -> bool:
        active_for_session = sum(1 for h in self.active.values() if h.session_id == session_id)
        return active_for_session < self.max_concurrent_actions

    def stale_actions(self, session_id: str, now: int, timeout_ms: int) -> list[str]:
        out: list[str] = []
        for aid, handle in self.active.items():
            if handle.session_id == session_id and now - handle.started_at > timeout_ms:
                out.append(aid)
        return out

    def _check_hpu_physical_gate(self, session_id: str) -> bool:
        if not self.leases:
            return True
        hpu_lease_id = self.leases.by_resource.get("hpu")
        if not hpu_lease_id:
            return False
        lease = self.osm.lease_projection.get(hpu_lease_id)
        return bool(lease and lease.owner_session == session_id)

    def send_goal(self, session_id: str, tool: str, args: Dict[str, Any], plan_id: str = "", trace_id: str = "") -> ActionHandle:
        """Dispatch a new action goal over DDS action transport.

        Includes dual gates for HPU model tools:
        1) scheduler logical quota
        2) lease physical ownership
        """
        if self._is_hpu_model_tool(tool):
            if self.scheduler:
                job = TaskSpec(name=tool, task_type="model", priority=5, est_latency_ms=300)
                if not self.scheduler.start(job):
                    raise RuntimeError("HPU_BUSY: scheduler quota exceeded")
            else:
                job = None
            if not self._check_hpu_physical_gate(session_id):
                if job and self.scheduler:
                    self.scheduler.finish(job)
                raise RuntimeError("HPU_BUSY: hpu lease not held")
        else:
            job = None

        epoch = self.current_epoch(session_id)
        action_id = new_id("A")
        h = ActionHandle(action_id=action_id, session_id=session_id, tool=tool, args=args, action_epoch=epoch, started_at=now_ms())
        self.active[action_id] = h
        if job:
            self._model_jobs[action_id] = job
        goal = Goal(
            hdr=ActionHeader(session_id=session_id, plan_id=plan_id, action_id=action_id, trace_id=trace_id, action_epoch=epoch, osm_version_hint=self.osm.version),
            tool=tool,
            json_args=args,
        )
        self.dds_client.send_goal(goal)
        self.osm.append_event(OSMEvent(type="ACTION_GOAL_SENT", session_id=session_id, action_id=action_id, payload={"tool": tool, "args": args, "action_epoch": epoch}))
        self.osm.apply_patch({"type": "action_update", "action_id": action_id, "data": {"state": "RUNNING", "tool": tool, "session_id": session_id, "action_epoch": epoch}})
        return h

    def _finish_model_job(self, action_id: str) -> None:
        job = self._model_jobs.pop(action_id, None)
        if job and self.scheduler:
            self.scheduler.finish(job)

    def poll(self, action_id: str) -> Optional[ActionResult]:
        """Poll feedback/result channels and translate to ActionResult."""
        handle = self.active.get(action_id)
        if not handle:
            return None
        fb = self.dds_client.poll_feedback(action_id)
        if fb:
            if fb.hdr.action_epoch == self.current_epoch(handle.session_id):
                self.osm.append_event(OSMEvent(type="ACTION_FEEDBACK", session_id=handle.session_id, action_id=action_id, payload={"progress": fb.progress, "status": fb.status_msg, "action_epoch": fb.hdr.action_epoch}))

        result = self.dds_client.poll_result(action_id)
        if result:
            if result.hdr.action_epoch != self.current_epoch(handle.session_id):
                self.active.pop(action_id, None)
                self._finish_model_job(action_id)
                self.osm.append_event(OSMEvent(type="ACTION_RESULT_IGNORED", session_id=handle.session_id, action_id=action_id, payload={"reason": "stale_epoch", "action_epoch": result.hdr.action_epoch}))
                return None
            ar = result_to_action_result(result)
            self.osm.append_event(OSMEvent(type="ACTION_RESULT", session_id=handle.session_id, action_id=action_id, payload={"status": ar.status, "error_code": ar.error_code, "action_epoch": result.hdr.action_epoch}))
            self.osm.apply_patch({"type": "action_update", "action_id": action_id, "data": {"state": ar.status, "tool": handle.tool, "session_id": handle.session_id, "action_epoch": result.hdr.action_epoch}})
            self.active.pop(action_id, None)
            self._finish_model_job(action_id)
            return ar
        return None

    def cancel(self, action_id: str, reason: str = "cancel") -> Optional[ActionResult]:
        handle = self.active.get(action_id)
        if not handle:
            return None
        cancel = Cancel(
            hdr=ActionHeader(session_id=handle.session_id, plan_id="", action_id=action_id, trace_id="", action_epoch=handle.action_epoch, osm_version_hint=self.osm.version),
            reason=reason,
        )
        self.dds_client.cancel(handle.tool, cancel)
        self.osm.append_event(OSMEvent(type="ACTION_CANCELED", session_id=handle.session_id, action_id=action_id, payload={"reason": reason, "action_epoch": handle.action_epoch}))
        self._finish_model_job(action_id)
        return None

    def cancel_session(self, session_id: str, reason: str = "session_cancel") -> None:
        """Cancel all active actions associated with a session."""
        for aid, handle in list(self.active.items()):
            if handle.session_id == session_id:
                self.cancel(aid, reason=reason)
                self.active.pop(aid, None)
