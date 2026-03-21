"""Conversation State Models.

Working memory only — never persisted to RAG, semantic memory, or profile.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class IntentFrame:
    """A single captured intent with its entity and slot context.

    Frames are stacked in ConversationState.active_frames so the system
    can walk back through recent conversational context.
    """

    intent: str
    entity: str | None = None
    slots: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    confidence: float = 1.0


@dataclass
class ConversationState:
    """Working memory for a single conversation session.

    Tracks the most recently resolved entities, intents, and agent results
    so that short follow-up queries can be expanded without asking the user
    for clarification.
    """

    session_id: str

    # Turn counter — incremented on every processed user message
    turn_index: int = 0

    # Raw and resolved text of the last exchange
    last_user_message: str | None = None
    last_resolved_query: str | None = None

    # Extracted entities
    last_entity: str | None = None          # e.g. "成都", "BTC", "NVDA"
    last_location: str | None = None        # e.g. "Melbourne", "东京"
    last_numeric_subject: str | None = None # e.g. "油价", "汇率"

    # Intent and domain context
    last_intent: str | None = None          # e.g. "weather", "crypto", "time"
    last_domain: str | None = None          # e.g. "realtime_lookup", "map"

    # Agent execution context
    last_agent: str | None = None           # e.g. "RealtimeLookupAgent"
    last_card_type: str | None = None       # e.g. "weather_card", "crypto_card"
    last_card_payload: dict[str, Any] | None = None

    # Timestamp of last update (time.time())
    last_update_ts: float = 0.0

    # Stacked intent frames — most recent last
    active_frames: list[IntentFrame] = field(default_factory=list)

    # Maximum frames to retain
    MAX_FRAMES: int = field(default=10, init=False, repr=False, compare=False)

    def push_frame(self, frame: IntentFrame) -> None:
        """Push a new intent frame; evict oldest if over capacity."""
        self.active_frames.append(frame)
        if len(self.active_frames) > self.MAX_FRAMES:
            self.active_frames = self.active_frames[-self.MAX_FRAMES :]

    def best_entity(self) -> str | None:
        """Return the most specific available entity for resolution."""
        return self.last_entity or self.last_location or self.last_numeric_subject

    @property
    def active_frame(self) -> IntentFrame | None:
        """Return the most recent IntentFrame, or None if stack is empty."""
        return self.active_frames[-1] if self.active_frames else None
