from __future__ import annotations

from dataclasses import dataclass, field
from time import time


@dataclass(slots=True)
class RendererTrace:
    request_id: str
    renderers: list[str] = field(default_factory=list)
    layout: str = ""
    fallback_reason: str = ""
    ts: float = field(default_factory=time)
