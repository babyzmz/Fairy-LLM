from __future__ import annotations

from dataclasses import dataclass, field
from time import time


@dataclass(slots=True)
class SpeechTrace:
    request_id: str
    mode: str = ""
    route: str = ""
    ts: float = field(default_factory=time)
