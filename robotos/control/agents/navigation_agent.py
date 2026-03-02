"""Long-range navigation agent.

This agent encapsulates multi-step navigation decisions (plan + execute report)
instead of treating long-horizon navigation as a single low-level skill call.
"""

from __future__ import annotations

from robotos.control.agents.base import AgentProfile, BaseAgent
from robotos.control.message.stream import MessageStream
from robotos.embodied import NavigationGoal, PathNavigationAgent
from robotos.models import Message


class LongRangeNavigationAgent(BaseAgent):
    def __init__(self, stream: MessageStream, pna: PathNavigationAgent, agent_id: str = "nav_service_agent") -> None:
        super().__init__(
            AgentProfile(
                agent_id=agent_id,
                role="service_agent",
                responsibilities=["global_path_planning", "waypoint_execution", "navigation_reporting"],
            )
        )
        self.stream = stream
        self.pna = pna
        stream.subscribe("REQ_NAV_PLAN", self.on_request, agent_id=agent_id)

    def on_request(self, msg: Message) -> None:
        if msg.type != "Request" or not msg.session_id:
            return
        target = str((msg.payload or {}).get("semantic_target", ""))
        result = self.pna.plan_and_execute(NavigationGoal(semantic_target=target, constraints=msg.payload.get("constraints", {})))
        self.stream.publish(
            Message(
                type="Decision",
                topic="NAV_EXEC_DONE",
                session_id=msg.session_id,
                trace_id=msg.trace_id,
                payload={
                    "target": target,
                    "success": result.success,
                    "global_path": result.global_path,
                    "waypoints": result.waypoints,
                    "reason": result.reason,
                },
            ),
            sender=self.agent_id,
        )
