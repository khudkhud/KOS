"""Robot state estimation and fusion facade."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

from robotos.models import now_ms


@dataclass
class RobotState:
    pose: Dict[str, float] = field(default_factory=lambda: {"x": 0.0, "y": 0.0, "yaw": 0.0})
    battery_percent: int = 100
    localization_confidence: float = 1.0
    obstacle_nearby: bool = False
    ts: int = field(default_factory=now_ms)


class RobotStateEstimator:
    def __init__(self) -> None:
        self._state = RobotState()

    def update(self, **kwargs: object) -> None:
        state = self._state
        for k, v in kwargs.items():
            if hasattr(state, k):
                setattr(state, k, v)
        state.ts = now_ms()

    def snapshot(self) -> Dict[str, object]:
        return {
            "pose": dict(self._state.pose),
            "battery_percent": self._state.battery_percent,
            "localization_confidence": self._state.localization_confidence,
            "obstacle_nearby": self._state.obstacle_nearby,
            "ts": self._state.ts,
        }
