from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from robotos.models import now_ms


@dataclass
class Span:
    name: str
    ts: int
    attrs: Dict[str, Any]


class TraceEmitter:
    def __init__(self) -> None:
        self.spans: List[Span] = []

    def emit(self, name: str, **attrs: Any) -> None:
        self.spans.append(Span(name=name, ts=now_ms(), attrs=attrs))
