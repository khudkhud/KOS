"""Task execution agent for long-horizon tasks.

Design note:
- Long-horizon navigation is orchestrated directly by this task agent.
- We intentionally avoid an extra service-agent wrapper layer to keep the
  architecture simple and avoid over-segmentation.
"""

from __future__ import annotations

from typing import Dict, List

from robotos.control.agents.base import AgentProfile, BaseAgent
from robotos.control.message.stream import MessageStream
from robotos.embodied import NavigationGoal, PathNavigationAgent
from robotos.models import Message


class TaskExecutionAgent(BaseAgent):
    def __init__(self, stream: MessageStream, pna: PathNavigationAgent, agent_id: str = "task_execution_agent") -> None:
        super().__init__(
            AgentProfile(
                agent_id=agent_id,
                role="task_agent",
                responsibilities=["task_lifecycle", "agent_orchestration", "result_reporting"],
            )
        )
        self.stream = stream
        self.pna = pna
        self.task_reports: List[Dict[str, object]] = []

    def submit_long_nav_task(self, session_id: str, semantic_target: str) -> None:
        result = self.pna.plan_and_execute(
            NavigationGoal(
                semantic_target=semantic_target,
                constraints={"avoid_private_rooms": False},
            )
        )
        report = {
            "session_id": session_id,
            "target": semantic_target,
            "success": result.success,
            "global_path": result.global_path,
            "waypoints": result.waypoints,
            "reason": result.reason,
        }
        self.task_reports.append(report)
        self.stream.publish(
            Message(
                type="Decision",
                topic="NAV_EXEC_DONE",
                session_id=session_id,
                payload=report,
            ),
            sender=self.agent_id,
        )
