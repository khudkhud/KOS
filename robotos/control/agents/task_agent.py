"""Task execution agent for long-horizon tasks."""

from __future__ import annotations

from typing import Dict, List

from robotos.control.agents.base import AgentProfile, BaseAgent
from robotos.control.message.stream import MessageStream
from robotos.models import Message


class TaskExecutionAgent(BaseAgent):
    def __init__(self, stream: MessageStream, agent_id: str = "task_execution_agent") -> None:
        super().__init__(
            AgentProfile(
                agent_id=agent_id,
                role="task_agent",
                responsibilities=["task_lifecycle", "agent_orchestration", "result_reporting"],
            )
        )
        self.stream = stream
        self.task_reports: List[Dict[str, object]] = []
        stream.subscribe("NAV_EXEC_DONE", self.on_nav_done, agent_id=agent_id)

    def submit_long_nav_task(self, session_id: str, semantic_target: str) -> None:
        self.stream.publish(
            Message(
                type="Request",
                topic="REQ_NAV_PLAN",
                session_id=session_id,
                payload={"semantic_target": semantic_target, "constraints": {"avoid_private_rooms": False}},
            ),
            sender=self.agent_id,
        )

    def on_nav_done(self, msg: Message) -> None:
        if msg.type != "Decision" or not msg.session_id:
            return
        self.task_reports.append({"session_id": msg.session_id, **(msg.payload or {})})
