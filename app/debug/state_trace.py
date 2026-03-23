from __future__ import annotations

from dataclasses import dataclass, field
from time import time


@dataclass(slots=True)
class StateTrace:
    request_id: str
    from_state: str = ""
    to_state: str = ""
    ts: float = field(default_factory=time)
