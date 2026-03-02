from __future__ import annotations

from typing import Dict

from robotos.kernel.action.supervisor import Skill
from robotos.models import ActionResult


class TimedSkill(Skill):
    def __init__(self, duration_ms: int, fail: bool = False, error_code: str = "") -> None:
        self.duration_ms = duration_ms
        self.fail = fail
        self.error_code = error_code
        self.jobs: Dict[str, int] = {}

    def start(self, action_id: str, tool: str, args: Dict[str, object]) -> None:
        _ = tool, args
        self.jobs[action_id] = 0

    def step(self, action_id: str, now: int):
        elapsed = self.jobs.get(action_id)
        if elapsed is None:
            return None
        elapsed += 200
        self.jobs[action_id] = elapsed
        if elapsed >= self.duration_ms:
            self.jobs.pop(action_id, None)
            if self.fail:
                return ActionResult(status="FAILED", error_code=self.error_code or "SKILL_FAIL", error_msg="simulated failure")
            return ActionResult(status="SUCCEEDED")
        return None

    def cancel(self, action_id: str):
        self.jobs.pop(action_id, None)
        return ActionResult(status="CANCELED")
