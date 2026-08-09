from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from fairy_core.assistant.interpretation import (
    ClassifierInterpretationPayload,
    ClassifierObjectivePayload,
    InputSegmentKind,
    InterpretationConfidence,
    InterpretationDisposition,
    RequestAction,
    segment_user_input,
)
from fairy_core.assistant.request_intent_policy import apply_request_intent_policy

_VAGUE_ONLY = re.compile(
    r"^(?:(?:do|handle|continue|repeat|fix)\s+(?:it|this|that)|"
    r"(?:处理|继续|重复|修复)(?:一下)?(?:它|这个|那个)?)$",
    re.IGNORECASE,
)
_ACTIONS = (
    (
        RequestAction.GENERATE,
        re.compile(
            r"(?:generate|render|生成|渲染).{0,20}"
            r"(?:image|video|music|audio|图片|图像|视频|音乐|音频)",
            re.IGNORECASE,
        ),
    ),
    (
        RequestAction.RUN,
        re.compile(r"\b(?:run|execute|test|start)\b|运行|执行|测试|启动", re.IGNORECASE),
    ),
    (
        RequestAction.CHANGE,
        re.compile(
            r"\b(?:fix|edit|update|modify|delete|rename|rewrite)\b|"
            r"修复|修改|更新|删除|重命名|改写",
            re.IGNORECASE,
        ),
    ),
    (
        RequestAction.CREATE,
        re.compile(r"\b(?:create|write|draft|make)\b|创建|编写|撰写|制作", re.IGNORECASE),
    ),
    (
        RequestAction.BROWSE,
        re.compile(
            r"\b(?:search|browse|look\s+up|latest|current)\b|搜索|查询|浏览|最新|当前",
            re.IGNORECASE,
        ),
    ),
    (
        RequestAction.REVIEW,
        re.compile(r"\b(?:review|audit|inspect|check)\b|复审|审查|检查", re.IGNORECASE),
    ),
)


@dataclass(frozen=True, slots=True)
class ScheduledInstructionInterpretation:
    action: RequestAction
    public_summary: str
    instruction_sha256: str


def interpret_scheduled_instruction(instruction: str) -> ScheduledInstructionInterpretation:
    actionable = " ".join(
        segment.text
        for segment in segment_user_input(instruction)
        if segment.kind is InputSegmentKind.TEXT
    ).strip()
    normalized = " ".join(actionable.split())
    if not normalized or _VAGUE_ONLY.fullmatch(normalized):
        raise ValueError("Scheduled instruction requires an explicit task or outcome")
    action = next(
        (candidate for candidate, pattern in _ACTIONS if pattern.search(normalized)),
        RequestAction.ANSWER,
    )
    payload = ClassifierInterpretationPayload(
        normalized_goal=normalized[:4_000],
        action=action,
        objectives=(
            ClassifierObjectivePayload(goal=normalized[:2_000], action=action),
        ),
        targets=(normalized[:1_000],),
        deliverable=(
            normalized[:2_000]
            if action in {RequestAction.CREATE, RequestAction.GENERATE}
            else None
        ),
        confidence=InterpretationConfidence.MEDIUM,
        disposition=InterpretationDisposition.READY,
        public_summary=normalized[:240],
    )
    policy = apply_request_intent_policy(payload)
    if policy.disposition is InterpretationDisposition.CLARIFICATION_REQUIRED:
        raise ValueError(policy.clarification_question or "Scheduled instruction is ambiguous")
    return ScheduledInstructionInterpretation(
        action=action,
        public_summary=payload.public_summary,
        instruction_sha256=hashlib.sha256(instruction.strip().encode("utf-8")).hexdigest(),
    )


__all__ = ["ScheduledInstructionInterpretation", "interpret_scheduled_instruction"]
