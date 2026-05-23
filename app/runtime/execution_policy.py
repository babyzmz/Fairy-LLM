from __future__ import annotations

from typing import Any


_DISABLED_PROMPT_PATTERNS = (
    "主动推进",
    "主动提出",
    "主动建议",
    "可以尝试",
    "建议",
    "不妨",
    "叙述",
    "讲述",
    "故事",
    "背景",
    "上下文",
    "解释",
    "说明",
    "教学",
    "学习",
    "之前",
    "上次",
    "历史",
    "记得",
    "还记得",
)

_STRICT_MODE_SKILLS = {"realtime-lookup", "web-research"}
_STRICT_MODE_CARD_TYPES = {"time", "crypto", "stock", "fx_rate", "fuel_price", "weather", "news_list", "specs", "compare", "release", "web_brief"}


def filter_prompt_for_route(prompt: str, route_name: str) -> str:
    if route_name not in _STRICT_MODE_SKILLS:
        return prompt
    lines = prompt.splitlines()
    kept = [line for line in lines if not any(pattern in line for pattern in _DISABLED_PROMPT_PATTERNS)]
    return "\n".join(kept)


def should_apply_strict_mode(skill_name: str, card_type: str | None = None) -> bool:
    if skill_name not in _STRICT_MODE_SKILLS:
        return False
    if card_type is None:
        return True
    return card_type in _STRICT_MODE_CARD_TYPES


def apply_strict_mode(payload: dict[str, Any]) -> dict[str, Any]:
    payload["strict_mode"] = True
    payload["tool_lock"] = True
    payload["no_expansion"] = True
    payload["deterministic"] = True
    return payload
