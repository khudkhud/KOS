"""Action supervision bridge between executor and DDS action transport.

Provides goal dispatch, feedback/result polling, cancel, epoch fencing,
and HPU coordination (queue + priority preempt) for model actions.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Any, Dict, Optional

from robotos.kernel.action.dds import ActionHeader, Cancel, DDSActionClient, Goal, result_to_action_result
from robotos.kernel.lease.manager import LeaseManager
from robotos.kernel.osm.store import OSMStore
from robotos.kernel.policy.gate import ToolRegistry
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
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self.osm = osm
        self.dds_client = dds_client
        self.max_concurrent_actions = max_concurrent_actions
        self.scheduler = scheduler
        self.leases = leases
        self.active: Dict[str, ActionHandle] = {}
        self.session_epoch: Dict[str, int] = {}
        self._model_jobs: Dict[str, TaskSpec] = {}
        self._pending_hpu: list[dict[str, Any]] = []
        self.tool_registry = tool_registry

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

    def _req_token(self, session_id: str, tool: str, args: Dict[str, Any]) -> str:
        return f"{session_id}:{tool}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"

    def _priority(self, session_id: str) -> int:
        session = self.osm.session_projection.get(session_id)
        return session.priority if session else 0

    def _enqueue_hpu(self, session_id: str, tool: str, args: Dict[str, Any]) -> str:
        token = self._req_token(session_id, tool, args)
        if not any(x["token"] == token for x in self._pending_hpu):
            self._pending_hpu.append(
                {
                    "token": token,
                    "session_id": session_id,
                    "tool": tool,
                    "args": args,
                    "priority": self._priority(session_id),
                    "ts": now_ms(),
                }
            )
            self.osm.append_event(
                OSMEvent(
                    type="MODEL_QUEUE_ENQUEUED",
                    session_id=session_id,
                    payload={"tool": tool, "token": token, "reason": "hpu_busy"},
                )
            )
        return token

    def _queue_head_token(self) -> str | None:
        if not self._pending_hpu:
            return None
        item = sorted(self._pending_hpu, key=lambda x: (-int(x["priority"]), int(x["ts"])))[0]
        return str(item["token"])

    def _pop_queue_token(self, token: str) -> None:
        self._pending_hpu = [x for x in self._pending_hpu if x["token"] != token]

    def _check_hpu_physical_gate(self, session_id: str) -> bool:
        if not self.leases:
            return True
        hpu_lease_id = self.leases.by_resource.get("hpu")
        if not hpu_lease_id:
            return False
        lease = self.osm.lease_projection.get(hpu_lease_id)
        return bool(lease and lease.owner_session == session_id)


    def _hpu_owner(self) -> str | None:
        if not self.leases:
            return None
        hpu_lease_id = self.leases.by_resource.get("hpu")
        if not hpu_lease_id:
            return None
        lease = self.osm.lease_projection.get(hpu_lease_id)
        if not lease or lease.state != "HELD":
            return None
        return lease.owner_session

    def _ensure_hpu_gate(self, session_id: str) -> bool:
        if not self.leases:
            return True
        owner = self._hpu_owner()
        if owner == session_id:
            return True
        if owner is not None:
            return False
        try:
            self.leases.acquire(["hpu"], session_id)
        except RuntimeError:
            return False
        return self._check_hpu_physical_gate(session_id)

    def _wait_for_hpu(self, session_id: str, tool: str) -> bool:
        if not self.leases:
            return True
        policy = self.leases.policy_for("hpu")
        soft = max(int(policy.wait_soft_ms), 0)
        hard = max(int(policy.wait_hard_ms), soft, 1)
        start = now_ms()
        soft_emitted = False
        self.osm.append_event(OSMEvent(type="HPU_WAIT_STARTED", session_id=session_id, payload={"tool": tool, "soft_ms": soft, "hard_ms": hard}))
        while now_ms() - start <= hard:
            if self._ensure_hpu_gate(session_id):
                waited = now_ms() - start
                self.osm.append_event(OSMEvent(type="HPU_WAIT_ACQUIRED", session_id=session_id, payload={"tool": tool, "waited_ms": waited}))
                return True
            waited = now_ms() - start
            if soft and not soft_emitted and waited >= soft:
                soft_emitted = True
                self.osm.append_event(OSMEvent(type="HPU_WAIT_SOFT_LIMIT", session_id=session_id, payload={"tool": tool, "waited_ms": waited, "soft_ms": soft}))
            time.sleep(0.05)
        self.osm.append_event(OSMEvent(type="HPU_WAIT_TIMEOUT", session_id=session_id, payload={"tool": tool, "waited_ms": now_ms() - start, "hard_ms": hard}))
        return False

    def _maybe_preempt_hpu_owner(self, incoming_session: str) -> None:
        if not self.leases:
            return
        hpu_lease_id = self.leases.by_resource.get("hpu")
        if not hpu_lease_id:
            return
        lease = self.osm.lease_projection.get(hpu_lease_id)
        if not lease:
            return
        owner = lease.owner_session
        if owner == incoming_session:
            return
        if self._priority(incoming_session) <= self._priority(owner):
            return

        # cancel owner model actions and preempt hpu lease to high-priority session
        for aid, handle in list(self.active.items()):
            if handle.session_id == owner and self._is_hpu_model_tool(handle.tool):
                self.cancel(aid, reason="hpu_preempted_by_high_priority")
                self.active.pop(aid, None)
        self.leases.preempt("hpu", incoming_session)
        # Acquire HPU for incoming high-priority session immediately.
        self.leases.acquire(["hpu"], incoming_session, ttl_ms=5_000)
        self.osm.append_event(
            OSMEvent(
                type="MODEL_QUEUE_PREEMPTED",
                session_id=incoming_session,
                payload={"resource": "hpu", "from_session": owner, "to_session": incoming_session},
            )
        )


    def _resolve_degrade_fallback(self, tool: str) -> str | None:
        if not self.tool_registry:
            return None
        try:
            spec = self.tool_registry.get(tool)
        except KeyError:
            return None
        fallback = (spec.degrade_fallback_tool or "").strip()
        if not fallback or fallback == tool:
            return None
        return fallback

    def _dispatch_with_degrade(self, *, session_id: str, tool: str, args: Dict[str, Any], reason: str, plan_id: str, trace_id: str) -> ActionHandle:
        fallback = self._resolve_degrade_fallback(tool)
        if not fallback:
            raise RuntimeError(reason)
        self.osm.append_event(
            OSMEvent(
                type="MODEL_DEGRADE_FALLBACK",
                session_id=session_id,
                payload={"from_tool": tool, "to_tool": fallback, "reason": reason},
            )
        )
        return self.send_goal(session_id, fallback, args, plan_id=plan_id, trace_id=trace_id)

    def can_dispatch(self, session_id: str) -> bool:
        active_for_session = sum(1 for h in self.active.values() if h.session_id == session_id)
        return active_for_session < self.max_concurrent_actions

    def stale_actions(self, session_id: str, now: int, timeout_ms: int) -> list[str]:
        out: list[str] = []
        for aid, handle in self.active.items():
            if handle.session_id == session_id and now - handle.started_at > timeout_ms:
                out.append(aid)
        return out

    def send_goal(self, session_id: str, tool: str, args: Dict[str, Any], plan_id: str = "", trace_id: str = "") -> ActionHandle:
        """Dispatch a new action goal over DDS action transport.

        HPU model tools use dual gate with graceful contention handling:
        1) scheduler logical quota
        2) lease physical ownership

        On contention, request is queued and caller gets `HPU_QUEUED` so executor
        can retry without hard-fail.
        """
        if self._is_hpu_model_tool(tool):
            token = self._enqueue_hpu(session_id, tool, args)
            self._maybe_preempt_hpu_owner(session_id)
            if token != self._queue_head_token():
                self._pop_queue_token(token)
                return self._dispatch_with_degrade(session_id=session_id, tool=tool, args=args, reason="HPU_QUEUED: waiting higher-priority jobs", plan_id=plan_id, trace_id=trace_id)
            if not self._ensure_hpu_gate(session_id):
                if not self._wait_for_hpu(session_id, tool):
                    self._pop_queue_token(token)
                    raise RuntimeError("HPU_TIMEOUT: waited 1000ms for hpu lease")
            if self.scheduler:
                job = TaskSpec(name=tool, task_type="model", priority=5, est_latency_ms=300)
                if not self.scheduler.start(job):
                    self._pop_queue_token(token)
                    return self._dispatch_with_degrade(session_id=session_id, tool=tool, args=args, reason="HPU_QUEUED: scheduler quota exceeded", plan_id=plan_id, trace_id=trace_id)
            else:
                job = None
            self._pop_queue_token(token)
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
