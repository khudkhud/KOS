"""Task execution agent for long-horizon tasks with recovery policy table."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from robotos.control.agents.base import AgentProfile, BaseAgent
from robotos.control.message.stream import MessageStream
from robotos.embodied import NavigationGoal, PathNavigationAgent
from robotos.models import Message


@dataclass(frozen=True)
class RecoveryPolicy:
    max_retries: int
    action: str


class TaskExecutionAgent(BaseAgent):
    def __init__(self, stream: MessageStream, pna: PathNavigationAgent, agent_id: str = "task_execution_agent") -> None:
        super().__init__(
            AgentProfile(
                agent_id=agent_id,
                role="task_agent",
                responsibilities=["task_lifecycle", "agent_orchestration", "result_reporting", "failure_recovery"],
            )
        )
        self.stream = stream
        self.pna = pna
        self.task_reports: List[Dict[str, object]] = []
        self.recovery_policy_table: Dict[str, RecoveryPolicy] = {
            "unknown_target": RecoveryPolicy(max_retries=1, action="fallback_target"),
            "blocked_path": RecoveryPolicy(max_retries=2, action="reroute"),
            "timeout": RecoveryPolicy(max_retries=1, action="retry"),
        }

    def submit_long_nav_task(self, session_id: str, semantic_target: str) -> None:
        target = semantic_target
        constraints: Dict[str, object] = {"avoid_private_rooms": False}
        attempts = 0
        recovery_action = "none"
        escalated = False

        while True:
            attempts += 1
            result = self.pna.plan_and_execute(NavigationGoal(semantic_target=target, constraints=constraints))
            if result.success:
                report = {
                    "session_id": session_id,
                    "target": target,
                    "success": True,
                    "global_path": result.global_path,
                    "waypoints": result.waypoints,
                    "reason": result.reason,
                    "attempts": attempts,
                    "recovery_action": recovery_action,
                    "escalated": escalated,
                }
                self._report(report, "Decision", "NAV_EXEC_DONE")
                return

            reason = result.reason or "timeout"
            policy = self.recovery_policy_table.get(reason)
            if not policy or attempts > (policy.max_retries + 1):
                escalated = True
                fail_report = {
                    "session_id": session_id,
                    "target": target,
                    "success": False,
                    "global_path": result.global_path,
                    "waypoints": result.waypoints,
                    "reason": reason,
                    "attempts": attempts,
                    "recovery_action": recovery_action,
                    "escalated": escalated,
                }
                self._report(fail_report, "Decision", "NAV_EXEC_DONE")
                self._report(
                    {
                        "session_id": session_id,
                        "reason": reason,
                        "target": target,
                        "attempts": attempts,
                        "owner": "human_operator",
                    },
                    "Escalation",
                    "TASK_NEEDS_HUMAN_HANDOVER",
                )
                return

            recovery_action = policy.action
            target, constraints = self._apply_recovery(policy.action, target, constraints)

    def _apply_recovery(self, action: str, target: str, constraints: Dict[str, object]) -> tuple[str, Dict[str, object]]:
        if action == "fallback_target":
            fallback = {"child_room": "entrance", "bedroom": "entrance"}
            return fallback.get(target, "entrance"), constraints
        if action == "reroute":
            next_constraints = dict(constraints)
            next_constraints["avoid_private_rooms"] = True
            return target, next_constraints
        if action == "retry":
            return target, constraints
        return target, constraints

    def _report(self, payload: Dict[str, object], msg_type: str, topic: str) -> None:
        if topic == "NAV_EXEC_DONE":
            self.task_reports.append(payload)
        self.stream.publish(
            Message(
                type=msg_type,
                topic=topic,
                session_id=str(payload.get("session_id", "")),
                payload=payload,
            ),
            sender=self.agent_id,
        )
