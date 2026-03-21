"""Data models for the RealtimeLookup agent public contract.

All external callers must use these models.
Internal implementation details are hidden behind agent.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RealtimeLookupRequest:
    """Input to the RealtimeLookup agent."""

    query: str
    allowed_tools: list[str] = field(default_factory=list)
    strict_mode: bool = True
    request_id: str = ""


@dataclass
class RealtimeLookupResult:
    """Structured result from the RealtimeLookup agent.

    Matches the Stage-3 RealtimeResult protocol fields.
    """

    success: bool
    speech_text: str               # TTS-friendly plain text
    card_payload: dict[str, Any]   # Structured card for UI
    numeric_value: float | None    # Primary numeric value
    confidence: float              # 0.0 – 1.0
    stage_used: str = ""           # Which stage produced this result
    subtype: str = ""
    source_urls: list[str] = field(default_factory=list)
    error_message: str = ""
    tool_lock: bool = True         # Factual data — do not rewrite

    @property
    def success_flag(self) -> bool:
        return self.success

    def to_dict(self) -> dict[str, Any]:
        """Legacy-compatible dict for dispatch layer."""
        return {
            "success":       self.success,
            "answer":        self.speech_text,
            "card":          self.card_payload,
            "source":        self.stage_used,
            "source_urls":   self.source_urls,
            "confidence":    self.confidence,
            "tool_lock":     self.tool_lock,
            "tool_lock_valid": True,
            "needs_clarification": False,
            "reason":        self.error_message,
            "subtype":       self.subtype,
            "numeric_value": self.numeric_value,
        }


@dataclass
class RealtimeLookupError:
    """Error/status model returned when the agent cannot fulfil the request."""

    reason: str
    subtype: str = ""
    stage_reached: str = ""
    retryable: bool = False

    def to_result(self) -> RealtimeLookupResult:
        return RealtimeLookupResult(
            success=False,
            speech_text="实时数据暂不可用",
            card_payload={"type": "error_card", "message": self.reason},
            numeric_value=None,
            confidence=0.0,
            stage_used=self.stage_reached,
            subtype=self.subtype,
            error_message=self.reason,
            tool_lock=False,
        )
