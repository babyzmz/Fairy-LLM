from __future__ import annotations

import re
from dataclasses import replace
from uuid import UUID

from fairy_core.assistant.interpretation import (
    AssistantRequestInterpretationRevision,
    InputSegmentKind,
    RequestAction,
    fallback_interpretation,
    segment_user_input,
)

_DIRECT_REQUESTS = {
    "system.notify": r"notify me\b|send (?:me )?a notification\b|通知我|提醒我",
    "system.copy_text": r"copy\b.{0,200}\b(?:to (?:the )?)?clipboard\b|复制到剪贴板|拷贝到剪贴板",
    "system.reveal_path": r"(?:show|open)\b.{0,200}\bin (?:file )?explorer\b|打开所在位置",
    "system.open_settings": r"open (?:windows|system) settings\b|打开(?:系统|windows)\s*设置",
    "system.open_url": r"open\b.{0,200}\bin (?:the )?(?:default|external|system)?\s*browser\b|"
    r"(?:用|使用)(?:默认|系统)浏览器打开|打开链接",
}


def explicit_system_actions(user_text: str) -> frozenset[str]:
    """Recognize narrow direct commands, never quoted examples or mere tool mentions."""
    actionable = " ".join(
        segment.text
        for segment in segment_user_input(user_text)
        if segment.kind is InputSegmentKind.TEXT
    )
    return frozenset(
        name
        for name, pattern in _DIRECT_REQUESTS.items()
        if re.search(
            rf"(?:^|[;。\uFF1B\n])\s*(?:please\s+|请)?(?:{pattern})",
            actionable,
            re.IGNORECASE,
        )
    )


def unrouted_interpretation(
    *,
    turn_id: UUID,
    revision: int,
    source_message_id: UUID,
    source_message: str,
) -> AssistantRequestInterpretationRevision:
    direct_actions = explicit_system_actions(source_message)
    interpretation = fallback_interpretation(
        turn_id=turn_id,
        revision=revision,
        source_message_id=source_message_id,
        source_message=source_message,
        action=RequestAction.MANAGE if direct_actions else RequestAction.ANSWER,
    )
    return replace(interpretation, targets=tuple(sorted(direct_actions)))


__all__ = ["explicit_system_actions", "unrouted_interpretation"]
