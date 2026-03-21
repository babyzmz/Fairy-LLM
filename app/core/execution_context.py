"""Execution context — per-request metadata passed through the dispatch chain.

Future use: pass this to agents so they can log, trace, and abort consistently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExecutionContext:
    """Immutable context for a single agent invocation."""
    request_id: str = ""
    origin: str = "main_chat"          # e.g. main_chat, desktop_pet
    allowed_tools: list[str] = field(default_factory=list)
    strict_mode: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
