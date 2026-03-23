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
    speech_text: str
    card_payload: dict[str, Any]
    numeric_value: float | None
    confidence: float
    stage_used: str = ""
    subtype: str = ""
    source_urls: list[str] = field(default_factory=list)
    error_message: str = ""
    tool_lock: bool = True

    @property
    def success_flag(self) -> bool:
        return self.success

    def to_dict(self) -> dict[str, Any]:
        """Legacy-compatible dict for dispatch layer."""
        return {
            "success": self.success,
            "answer": self.speech_text,
            "card": self.card_payload,
            "source": self.stage_used,
            "source_urls": self.source_urls,
            "confidence": self.confidence,
            "tool_lock": self.tool_lock,
            "tool_lock_valid": True,
            "needs_clarification": False,
            "reason": self.error_message,
            "subtype": self.subtype,
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
        if self.reason == "cancelled":
            speech_text = "本次查询已取消。"
            card_payload = {"type": "generic_info", "data": {"title": "已取消", "summary": speech_text}}
        else:
            speech_text = "实时数据暂不可用"
            card_payload = {"type": "error_card", "message": self.reason}
        return RealtimeLookupResult(
            success=False,
            speech_text=speech_text,
            card_payload=card_payload,
            numeric_value=None,
            confidence=0.0,
            stage_used=self.stage_reached,
            subtype=self.subtype,
            error_message=self.reason,
            tool_lock=False,
        )
