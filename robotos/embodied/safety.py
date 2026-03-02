"""Safety supervisor with override decisions for embodied execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass
class SafetyDecision:
    allow_execute: bool
    reason: str
    level: str = "INFO"


class SafetySupervisor:
    def __init__(self, min_battery_percent: int = 15, min_localization_confidence: float = 0.5) -> None:
        self.min_battery_percent = min_battery_percent
        self.min_localization_confidence = min_localization_confidence

    def evaluate(self, robot_state: Dict[str, object]) -> SafetyDecision:
        battery = int(robot_state.get("battery_percent", 0))
        conf = float(robot_state.get("localization_confidence", 0.0))
        obstacle = bool(robot_state.get("obstacle_nearby", False))
        if battery < self.min_battery_percent:
            return SafetyDecision(False, "battery_below_threshold", level="WARN")
        if conf < self.min_localization_confidence:
            return SafetyDecision(False, "localization_unreliable", level="WARN")
        if obstacle:
            return SafetyDecision(False, "obstacle_blocking", level="WARN")
        return SafetyDecision(True, "safe_to_execute", level="INFO")
