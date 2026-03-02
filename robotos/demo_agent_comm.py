"""Agent communication demo and visualization helpers."""

from __future__ import annotations

import json

from robotos.app import run_demo


def to_mermaid(messages: list[dict]) -> str:
    """Render message history into a Mermaid sequence diagram."""
    lines = ["sequenceDiagram"]
    participants = sorted({m.get("sender", "unknown") for m in messages} | {"bus"})
    for p in participants:
        lines.append(f"    participant {p}")
    for m in messages:
        sender = m.get("sender", "unknown")
        topic = m.get("topic", "")
        mtype = m.get("type", "")
        lines.append(f"    {sender}->>bus: {mtype}:{topic}")
    return "\n".join(lines)


def run_agent_comm_demo() -> dict:
    """Run TARGET_GONE scenario and return raw + visualizable communication trace."""
    out = run_demo(emit_target_gone=True)
    mermaid = to_mermaid(out.get("messages", []))
    return {
        "session": out["session"],
        "message_count": len(out.get("messages", [])),
        "messages": out.get("messages", []),
        "mermaid": mermaid,
    }


def main() -> None:
    result = run_agent_comm_demo()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
