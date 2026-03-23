from __future__ import annotations

from dataclasses import dataclass, field
from time import time


@dataclass(slots=True)
class ToolTrace:
    request_id: str
    tool_chain: list[str] = field(default_factory=list)
    selected_tool: str = ""
    selection_reason: str = ""
    fallback_reason: str = ""
    ts: float = field(default_factory=time)
