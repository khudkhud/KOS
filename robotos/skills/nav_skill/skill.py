from robotos.skills.base import TimedSkill


def build_nav_skill() -> TimedSkill:
    return TimedSkill(duration_ms=1200)
