from __future__ import annotations

from dataclasses import dataclass, field
from time import time


@dataclass(slots=True)
class SchemaTrace:
    request_id: str
    card_types: list[str] = field(default_factory=list)
    fallback_reason: str = ""
    ts: float = field(default_factory=time)
