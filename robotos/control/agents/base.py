"""Agent abstraction for long-horizon orchestration roles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class AgentProfile:
    agent_id: str
    role: str
    responsibilities: List[str]


class BaseAgent:
    def __init__(self, profile: AgentProfile) -> None:
        self.profile = profile

    @property
    def agent_id(self) -> str:
        return self.profile.agent_id
