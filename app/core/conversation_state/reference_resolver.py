"""Reference Resolver — expands elliptical follow-up queries using conversation state."""
from __future__ import annotations
import logging
import re
from app.core.conversation_state.state_models import ConversationState

logger = logging.getLogger(__name__)

_SHORT_TOKEN_LIMIT = 12

_ZH_MARKERS = frozenset([
    "\u5462", "\u90a3\u4e2a", "\u8fd9\u91cc", "\u90a3\u91cc", "\u5750\u6807",
    "\u73b0\u5728", "\u591a\u5c11", "\u8fd8\u5728\u5417", "\u4ef7\u683c",
    "\u6c47\u7387", "\u80a1\u4ef7", "\u5929\u6c14", "\u51e0\u70b9",
    "\u4eba\u53e3", "\u9762\u79ef", "\u5730\u56fe", "\u591a\u8fdc",
    "\u591a\u5927", "\u660e\u5929",
])
_EN_MARKERS = frozenset([
    "now", "there", "that", "it", "how much", "coordinates",
    "price", "rate", "weather", "time", "what about",
    "how far", "how big", "population", "map",
])

# Ordered elliptical expansion patterns: (regex, template)
# {entity} = best entity from state; {msg} = original message
_ELLIPTICAL: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^\u4eba\u53e3\u591a\u5c11"),   "{entity}\u4eba\u53e3\u591a\u5c11"),  # 人口多少
    (re.compile(r"^\u9762\u79ef\u591a\u5c11"),   "{entity}\u9762\u79ef\u591a\u5c11"),  # 面积多少
    (re.compile(r"^\u5730\u56fe\u5462?$"),        "\u663e\u793a{entity}\u5730\u56fe"),   # 地图呢
    (re.compile(r"^\u5750\u6807\u5462?$"),        "{entity}\u5730\u7406\u5750\u6807"),   # 坐标呢
    (re.compile(r"^\u591a\u8fdc"),                "{entity}\u591a\u8fdc"),               # 多远
    (re.compile(r"^\u591a\u5927"),                "{entity}\u591a\u5927"),               # 多大
    (re.compile(r"^\u591a\u9ad8"),                "{entity}\u591a\u9ad8"),               # 多高
    (re.compile(r"^\u660e\u5929\u5462?$"),        "{entity}\u660e\u5929\u5929\u6c14"),   # 明天呢
    (re.compile(r"^\u4eca\u5929\u5462?$"),        "{entity}\u4eca\u5929\u5929\u6c14"),   # 今天呢
    (re.compile(r"^\u73b0\u5728\u5462?$"),        "{entity}\u73b0\u5728\u600e\u4e48\u6837"),  # 现在呢
    (re.compile(r"^\u90a3\u91cc\u5462?$"),        "{entity}"),                           # 那里呢
    (re.compile(r"population$", re.I),            "{entity} population"),
    (re.compile(r"^how far", re.I),               "how far is {entity}"),
    (re.compile(r"^how big", re.I),               "how big is {entity}"),
    (re.compile(r"^map\??$", re.I),               "{entity} map"),
]

# Intent-keyed templates (fallback when no elliptical pattern matches)
_INTENT_TEMPLATES: dict[str, str] = {
    "location":  "{entity}\u7684{msg}",
    "map":       "{entity}\u5728\u54ea\u91cc",
    "time":      "{entity}\u73b0\u5728\u51e0\u70b9",
    "weather":   "{entity}\u5929\u6c14\u600e\u4e48\u6837",
    "crypto":    "{entity}\u73b0\u5728\u4ef7\u683c\u591a\u5c11",
    "stock":     "{entity}\u73b0\u5728\u80a1\u4ef7\u591a\u5c11",
    "exchange":  "{entity}\u6c47\u7387\u591a\u5c11",
    "fuel":      "{entity}\u9644\u8fd1\u6cb9\u4ef7\u591a\u5c11",
}


class ReferenceResolver:
    """Resolves short/elliptical follow-up queries using active conversation state."""

    @classmethod
    def resolve(cls, user_message: str, state: ConversationState) -> str:
        """Expand follow-up; return original if no resolution applies."""
        resolved, _ = cls.resolve_with_confidence(user_message, state)
        return resolved

    @classmethod
    def resolve_with_confidence(
        cls, user_message: str, state: ConversationState
    ) -> tuple[str, float]:
        """Return (resolved_query, confidence). confidence=0.0 means no change."""
        msg = (user_message or "").strip()
        if not msg:
            return msg, 0.0

        # Length check
        is_zh = bool(re.search(r"[\u4e00-\u9fff]", msg))
        token_count = len(msg) if is_zh else len(msg.split())
        if token_count > _SHORT_TOKEN_LIMIT:
            logger.debug("reference_resolver skipped (long) msg=%s", msg[:30])
            return msg, 0.0

        if not cls._has_marker(msg):
            logger.debug("reference_resolver skipped (no marker) msg=%s", msg[:30])
            return msg, 0.0

        entity = state.best_entity()
        if not entity:
            logger.debug("reference_resolver no_entity msg=%s", msg[:30])
            return msg, 0.0

        # Try elliptical pattern templates first
        for pattern, template in _ELLIPTICAL:
            if pattern.search(msg):
                resolved = template.format(entity=entity, msg=msg)
                logger.info(
                    "reference_resolved pattern=%s original=%r resolved=%r entity=%s",
                    pattern.pattern[:30], msg[:40], resolved[:60], entity,
                )
                return resolved, 0.92

        # Fall back to intent-keyed template
        intent = state.last_intent or ""
        card_type = (state.last_card_type or "").replace("_card", "")
        key = card_type if card_type in _INTENT_TEMPLATES else intent
        template = _INTENT_TEMPLATES.get(key)
        if template:
            resolved = template.format(entity=entity, msg=msg)
            logger.info(
                "reference_resolved intent_template=%s original=%r resolved=%r",
                key, msg[:40], resolved[:60],
            )
            return resolved, 0.80

        # Generic prepend fallback
        sep = "" if is_zh else " "
        resolved = f"{entity}{sep}{msg}"
        logger.info(
            "reference_resolved generic original=%r resolved=%r entity=%s",
            msg[:40], resolved[:60], entity,
        )
        return resolved, 0.60

    @classmethod
    def _has_marker(cls, msg: str) -> bool:
        msg_lower = msg.lower()
        for m in _ZH_MARKERS:
            if m in msg:
                return True
        for m in _EN_MARKERS:
            if m in msg_lower:
                return True
        return False
