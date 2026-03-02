"""Agent registry and communication policy checks for Message Stream."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from robotos.models import Message


@dataclass
class AgentDescriptor:
    agent_id: str
    role: str
    subscriptions: List[str]
    publish_allow: List[str]


class AgentRegistry:
    """Holds agent comm policy and validates publish/subscribe permissions."""

    def __init__(self) -> None:
        self._agents: Dict[str, AgentDescriptor] = {}

    def register(self, agent: AgentDescriptor) -> None:
        self._agents[agent.agent_id] = agent

    def get(self, agent_id: str) -> AgentDescriptor:
        if agent_id not in self._agents:
            raise PermissionError(f"agent not registered: {agent_id}")
        return self._agents[agent_id]

    def can_publish(self, agent_id: str, msg: Message) -> bool:
        desc = self.get(agent_id)
        return msg.type in desc.publish_allow

    def can_subscribe(self, agent_id: str, topic: str) -> bool:
        desc = self.get(agent_id)
        for pat in desc.subscriptions:
            if pat == "*":
                return True
            if pat.endswith("*") and topic.startswith(pat[:-1]):
                return True
            if pat == topic:
                return True
        return False


def build_default_agent_registry() -> AgentRegistry:
    reg = AgentRegistry()
    reg.register(AgentDescriptor(agent_id="monitor_agent", role="producer", subscriptions=["*"], publish_allow=["Event"]))
    reg.register(AgentDescriptor(agent_id="dialog_agent", role="producer", subscriptions=["*"], publish_allow=["Request", "Proposal", "Event"]))
    reg.register(AgentDescriptor(agent_id="nav_agent", role="producer", subscriptions=["*"], publish_allow=["Event"]))
    reg.register(AgentDescriptor(agent_id="strategy", role="consumer", subscriptions=["*"], publish_allow=["Proposal", "Decision", "Escalation"]))
    reg.register(AgentDescriptor(agent_id="planner", role="consumer", subscriptions=["REQ_PLAN", "SUGGEST_REPLAN"], publish_allow=["Event"]))
    reg.register(AgentDescriptor(agent_id="control", role="consumer", subscriptions=["*"], publish_allow=["Event", "Request", "Proposal", "Decision", "Override", "Escalation"]))
    reg.register(AgentDescriptor(agent_id="task_execution_agent", role="task", subscriptions=["*"], publish_allow=["Decision", "Event", "Proposal", "Request", "Escalation"]))
    return reg
