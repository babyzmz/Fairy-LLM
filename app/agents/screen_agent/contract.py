"""Public contract for the ScreenAgent (stub)."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScreenRequest:
    """Input to the ScreenAgent."""
    query: str
    screenshot_path: str = ""
    request_id: str = ""


@dataclass
class ScreenResult:
    """Output from the ScreenAgent."""
    success: bool
    description: str
    elements: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    error_message: str = ""
    # TODO: expand when ScreenAgent is implemented
