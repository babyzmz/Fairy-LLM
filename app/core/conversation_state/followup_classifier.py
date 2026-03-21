"""FollowUp Classifier — classifies user messages for the follow-up resolution pipeline.

Classification types:
  COMPLETE_QUERY     — fully self-contained, route as-is
  ELLIPTICAL_FOLLOWUP — definitely refers to prior context (high confidence)
  AMBIGUOUS_FOLLOWUP — possibly refers to prior context (medium confidence)
  NEW_TOPIC          — new topic detected despite short length
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class FollowUpType(Enum):
    COMPLETE_QUERY = "complete_query"
    ELLIPTICAL_FOLLOWUP = "elliptical_followup"
    AMBIGUOUS_FOLLOWUP = "ambiguous_followup"
    NEW_TOPIC = "new_topic"


@dataclass
class FollowUpClassification:
    follow_up_type: FollowUpType
    confidence: float           # 0.0 – 1.0
    reason: str = ""

    @property
    def is_followup(self) -> bool:
        return self.follow_up_type in (
            FollowUpType.ELLIPTICAL_FOLLOWUP,
            FollowUpType.AMBIGUOUS_FOLLOWUP,
        )

    @property
    def needs_clarification(self) -> bool:
        return self.follow_up_type == FollowUpType.AMBIGUOUS_FOLLOWUP and self.confidence < 0.4


# ---------------------------------------------------------------------------
# Pattern tables
# ---------------------------------------------------------------------------

# Quantity-only patterns — always elliptical when an entity is in state
_QUANTITY_PATTERNS = re.compile(
    r"^("
    r"\u4eba\u53e3\u591a\u5c11|"   # 人口多少
    r"\u9762\u79ef\u591a\u5c11|"   # 面积多少
    r"\u591a\u8fdc|"               # 多远
    r"\u591a\u5927|"               # 多大
    r"\u591a\u9ad8|"               # 多高
    r"\u591a\u957f|"               # 多长
    r"\u591a\u5c11\u4eba|"         # 多少人
    r"\u591a\u5c11\u9519|"         # 多少钱
    r"\u591a\u5c11\u5c81|"         # 多少岁
    r"how far|how big|how tall|how long|how many|how much"
    r")$",
    re.I,
)

# Strong continuation markers
_CONTINUATION_ZH = re.compile(
    r"(\u5462$|\u90a3\u91cc\u5462|\u8fd9\u91cc\u5462|"
    r"\u90a3\u8fb9\u5462|\u73b0\u5728\u5462|\u660e\u5929\u5462|"
    r"\u540e\u5929\u5462|\u5462\?|\u90a3\u4e2a\u5462)"
)
_CONTINUATION_EN = re.compile(
    r"\b(now\?|there\?|what about (it|that|there)|and (now|there|that)\??)",
    re.I,
)

# Pronoun-like reference words (standalone)
_PRONOUN_ZH = re.compile(
    r"(\u90a3\u91cc|\u90a3\u4e2a|\u90a3\u8fb9|\u8fd9\u91cc|\u8fd9\u4e2a|\u5b83|\u90a3)"
)
_PRONOUN_EN = re.compile(r"\b(there|that|it|this)\b", re.I)

# Temporal-only follow-ups
_TEMPORAL_PATTERNS = re.compile(
    r"^("
    r"\u73b0\u5728\u5462?|"         # 现在/现在呢
    r"\u660e\u5929\u5462?|"         # 明天/明天呢
    r"\u540e\u5929\u5462?|"         # 后天
    r"\u4eca\u5929\u5462?|"         # 今天
    r"now\??|tomorrow\??|tonight\??"
    r")$",
    re.I,
)

# Topic-shift signals — new noun subjects that suggest a new topic
_NEW_TOPIC_ZH = re.compile(
    r"^\u6211\u60f3|^\u5e2e\u6211|^\u8bf7\u95ee|^\u53e6\u5916|^\u6362\u4e2a|^\u4e0d\u5bf9"
)
_NEW_TOPIC_EN = re.compile(
    r"^(actually|never mind|forget it|change topic|what about [A-Z])",
    re.I,
)

# Chinese character detection
_HAS_ZH = re.compile(r"[\u4e00-\u9fff]")


class FollowUpClassifier:
    """Classify a user message relative to the current conversation state."""

    # Token threshold for "short query" — beyond this, treat as complete
    SHORT_ZH_CHARS = 10
    SHORT_EN_TOKENS = 6

    @classmethod
    def classify(
        cls,
        message: str,
        has_active_entity: bool,
        last_intent: str | None = None,
    ) -> FollowUpClassification:
        """Classify *message* given whether conversation state has an active entity.

        Args:
            message: Raw user message.
            has_active_entity: True if ConversationState.best_entity() is not None.
            last_intent: Last known intent (e.g. "weather", "location").

        Returns:
            FollowUpClassification with type and confidence.
        """
        msg = (message or "").strip()
        if not msg:
            return FollowUpClassification(FollowUpType.COMPLETE_QUERY, 0.0, "empty")

        # --- topic shift detection (run first) ---
        if _NEW_TOPIC_ZH.search(msg) or _NEW_TOPIC_EN.search(msg):
            return FollowUpClassification(FollowUpType.NEW_TOPIC, 0.9, "topic_shift_marker")

        # --- compute length ---
        is_zh = bool(_HAS_ZH.search(msg))
        length = len(msg) if is_zh else len(msg.split())
        is_short = length <= (cls.SHORT_ZH_CHARS if is_zh else cls.SHORT_EN_TOKENS)

        # --- quantity-only: always elliptical ---
        if _QUANTITY_PATTERNS.match(msg):
            if has_active_entity:
                return FollowUpClassification(
                    FollowUpType.ELLIPTICAL_FOLLOWUP, 0.95, "quantity_only_pattern"
                )
            return FollowUpClassification(
                FollowUpType.AMBIGUOUS_FOLLOWUP, 0.5, "quantity_only_no_entity"
            )

        # --- temporal-only pattern ---
        if _TEMPORAL_PATTERNS.match(msg):
            if has_active_entity:
                return FollowUpClassification(
                    FollowUpType.ELLIPTICAL_FOLLOWUP, 0.90, "temporal_continuation"
                )
            return FollowUpClassification(
                FollowUpType.AMBIGUOUS_FOLLOWUP, 0.45, "temporal_no_entity"
            )

        # --- strong continuation marker ---
        if _CONTINUATION_ZH.search(msg) or _CONTINUATION_EN.search(msg):
            if has_active_entity:
                return FollowUpClassification(
                    FollowUpType.ELLIPTICAL_FOLLOWUP, 0.88, "continuation_marker"
                )
            return FollowUpClassification(
                FollowUpType.AMBIGUOUS_FOLLOWUP, 0.50, "continuation_no_entity"
            )

        # --- pronoun-like reference word + short ---
        if is_short and (_PRONOUN_ZH.search(msg) or _PRONOUN_EN.search(msg)):
            if has_active_entity:
                return FollowUpClassification(
                    FollowUpType.ELLIPTICAL_FOLLOWUP, 0.80, "pronoun_short"
                )
            return FollowUpClassification(
                FollowUpType.AMBIGUOUS_FOLLOWUP, 0.40, "pronoun_no_entity"
            )

        # --- short but no explicit marker ---
        if is_short and has_active_entity:
            return FollowUpClassification(
                FollowUpType.AMBIGUOUS_FOLLOWUP, 0.35, "short_with_entity"
            )

        return FollowUpClassification(FollowUpType.COMPLETE_QUERY, 0.95, "long_or_no_marker")
