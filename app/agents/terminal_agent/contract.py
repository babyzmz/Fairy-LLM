"""Public contract for the TerminalAgent (stub)."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TerminalRequest:
    """Input to the TerminalAgent."""
    command: str
    working_dir: str = ""
    request_id: str = ""


@dataclass
class TerminalResult:
    """Output from the TerminalAgent."""
    success: bool
    stdout: str
    stderr: str = ""
    exit_code: int = 0
    error_message: str = ""
    # TODO: expand when TerminalAgent is implemented
