from robotos.skills.base import TimedSkill


def build_dialog_say_skill() -> TimedSkill:
    return TimedSkill(duration_ms=500)


def build_dialog_wait_skill() -> TimedSkill:
    return TimedSkill(duration_ms=800)
