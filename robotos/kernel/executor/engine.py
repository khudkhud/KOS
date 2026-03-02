"""Deterministic executable-graph executor.

Executes IR nodes (Seq/Action/Timeout/Retry/Lease ops) against runtime state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from robotos.kernel.action.supervisor import ActionSupervisor
from robotos.kernel.lease.manager import LeaseManager
from robotos.kernel.policy.gate import PolicyGate
from robotos.models import Session


SUCCESS = "SUCCESS"
FAILURE = "FAILURE"
RUNNING = "RUNNING"


@dataclass
class RuntimeState:
    cursor: Dict[str, int] = field(default_factory=dict)
    active_action: Dict[str, str] = field(default_factory=dict)
    retries: Dict[str, int] = field(default_factory=dict)
    leases_by_node: Dict[str, List[str]] = field(default_factory=dict)
    time_start: Dict[str, int] = field(default_factory=dict)


class Executor:
    def __init__(self, policy: PolicyGate, leases: LeaseManager, actions: ActionSupervisor) -> None:
        self.policy = policy
        self.leases = leases
        self.actions = actions

    def tick(self, node: Dict[str, Any], session: Session, rt: RuntimeState, now_ms: int) -> str:
        ntype = node["type"]
        nid = node.setdefault("id", f"node_{id(node)}")
        if ntype == "Seq":
            idx = rt.cursor.get(nid, 0)
            children = node.get("children", [])
            while idx < len(children):
                st = self.tick(children[idx], session, rt, now_ms)
                if st == RUNNING:
                    rt.cursor[nid] = idx
                    return RUNNING
                if st == FAILURE:
                    rt.cursor.pop(nid, None)
                    return FAILURE
                idx += 1
            rt.cursor.pop(nid, None)
            return SUCCESS
        if ntype == "AcquireLease":
            if nid not in rt.leases_by_node:
                found = self.leases.acquire(node["resources"], session.session_id, node.get("ttl_ms", 5000))
                rt.leases_by_node[nid] = [l.lease_id for l in found]
            return SUCCESS
        if ntype == "ReleaseLease":
            for res in node["resources"]:
                lid = self.leases.by_resource.get(res)
                if lid:
                    self.leases.release(lid)
            return SUCCESS
        if ntype == "Action":
            aid = rt.active_action.get(nid)
            if not aid:
                spec = self.policy.check_tool(session, node["tool"])
                for r in spec.required_resources:
                    if r not in self.leases.by_resource:
                        return FAILURE
                if not self.actions.can_dispatch(session.session_id):
                    return RUNNING
                handle = self.actions.send_goal(session.session_id, node["tool"], node.get("args", {}))
                rt.active_action[nid] = handle.action_id
                return RUNNING
            result = self.actions.poll(aid)
            if not result:
                return RUNNING
            rt.active_action.pop(nid, None)
            return SUCCESS if result.status == "SUCCEEDED" else FAILURE
        if ntype == "Timeout":
            start = rt.time_start.setdefault(nid, now_ms)
            if now_ms - start > node["timeout_ms"]:
                self.halt_subtree(node["child"], rt)
                rt.time_start.pop(nid, None)
                return FAILURE
            st = self.tick(node["child"], session, rt, now_ms)
            if st != RUNNING:
                rt.time_start.pop(nid, None)
            return st
        if ntype == "Retry":
            tries = rt.retries.get(nid, 0)
            st = self.tick(node["child"], session, rt, now_ms)
            if st == SUCCESS:
                rt.retries.pop(nid, None)
                return SUCCESS
            if st == RUNNING:
                return RUNNING
            tries += 1
            if tries > node.get("max", 0):
                rt.retries.pop(nid, None)
                return FAILURE
            rt.retries[nid] = tries
            return RUNNING
        raise ValueError(f"unsupported node type {ntype}")

    def halt_subtree(self, node: Dict[str, Any], rt: RuntimeState) -> None:
        nid = node.get("id")
        if nid and nid in rt.active_action:
            self.actions.cancel(rt.active_action[nid], reason="halt_subtree")
            rt.active_action.pop(nid, None)
        for child_key in ("children", "child"):
            child = node.get(child_key)
            if isinstance(child, list):
                for c in child:
                    self.halt_subtree(c, rt)
            elif isinstance(child, dict):
                self.halt_subtree(child, rt)
