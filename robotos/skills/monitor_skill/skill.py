from robotos.models import Message


def low_battery_event(level: int) -> Message:
    return Message(type="Event", topic="LOW_BATTERY", severity="WARN", payload={"battery": level})
