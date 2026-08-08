from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fairy_core.assistant.evidence import EvidenceRequirementKind
from fairy_core.domain.ids import new_id

INTERPRETATION_SCHEMA_VERSION = 1
INTERPRETATION_SEGMENT_CHARS = 16_384


class RequestAction(StrEnum):
    ANSWER = "answer"
    EXPLAIN = "explain"
    REVIEW = "review"
    CHANGE = "change"
    CREATE = "create"
    RUN = "run"
    BROWSE = "browse"
    GENERATE = "generate"
    SCHEDULE = "schedule"
    MANAGE = "manage"


class InterpretationConfidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class InterpretationDisposition(StrEnum):
    READY = "ready"
    ASSUMED = "assumed"
    CLARIFICATION_REQUIRED = "clarification_required"


class InputSegmentKind(StrEnum):
    TEXT = "text"
    QUOTE = "quote"
    CODE = "code"


class ClassifierObjectivePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    goal: str = Field(min_length=1, max_length=2_000)
    action: RequestAction
    depends_on: tuple[int, ...] = Field(default=(), max_length=32)

    @field_validator("depends_on")
    @classmethod
    def validate_dependencies(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if any(index < 0 for index in value) or len(value) != len(set(value)):
            raise ValueError("objective dependencies are invalid")
        return value


class ClassifierInterpretationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    normalized_goal: str = Field(min_length=1, max_length=4_000)
    action: RequestAction
    objectives: tuple[ClassifierObjectivePayload, ...] = Field(min_length=1, max_length=16)
    targets: tuple[str, ...] = Field(default=(), max_length=64)
    constraints: tuple[str, ...] = Field(default=(), max_length=64)
    deliverable: str | None = Field(default=None, min_length=1, max_length=2_000)
    assumptions: tuple[str, ...] = Field(default=(), max_length=32)
    missing_information: tuple[str, ...] = Field(default=(), max_length=32)
    confidence: InterpretationConfidence
    disposition: InterpretationDisposition
    public_summary: str = Field(min_length=1, max_length=240)
    clarification_question: str | None = Field(default=None, min_length=1, max_length=1_000)

    @field_validator("targets", "constraints", "assumptions", "missing_information")
    @classmethod
    def validate_unique_texts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)) or any(not item.strip() for item in value):
            raise ValueError("interpretation text lists must contain unique nonblank values")
        return value

    @model_validator(mode="after")
    def validate_clarification(self) -> ClassifierInterpretationPayload:
        required = self.disposition is InterpretationDisposition.CLARIFICATION_REQUIRED
        if required != (self.clarification_question is not None):
            raise ValueError("clarification disposition and question must agree")
        if required and not self.missing_information:
            raise ValueError("clarification must identify missing information")
        for index, objective in enumerate(self.objectives):
            if any(dependency >= index for dependency in objective.depends_on):
                raise ValueError("objective dependencies must reference earlier objectives")
        return self


@dataclass(frozen=True, slots=True)
class InterpretedObjective:
    goal: str
    action: RequestAction
    depends_on: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        _bounded_text(self.goal, "objective goal", maximum=2_000)
        if any(index < 0 for index in self.depends_on):
            raise ValueError("objective dependencies must be non-negative")
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError("objective dependencies must be unique")


@dataclass(frozen=True, slots=True)
class AssistantRequestInterpretationRevision:
    id: UUID
    turn_id: UUID
    revision: int
    idempotency_key: str
    source_message_id: UUID
    source_message_sha256: str
    schema_version: int
    normalized_goal: str
    action: RequestAction
    objectives: tuple[InterpretedObjective, ...]
    targets: tuple[str, ...]
    constraints: tuple[str, ...]
    deliverable: str | None
    evidence_requirements: tuple[EvidenceRequirementKind, ...]
    assumptions: tuple[str, ...]
    missing_information: tuple[str, ...]
    confidence: InterpretationConfidence
    disposition: InterpretationDisposition
    public_summary: str
    clarification_question: str | None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.revision < 1 or self.schema_version < 1:
            raise ValueError("interpretation revisions and schema versions must be positive")
        _bounded_text(self.idempotency_key, "idempotency key", maximum=512)
        _digest(self.source_message_sha256)
        _bounded_text(self.normalized_goal, "normalized goal", maximum=4_000)
        _bounded_text(self.public_summary, "public summary", maximum=240)
        if not self.objectives:
            raise ValueError("interpretation must contain at least one objective")
        for index, objective in enumerate(self.objectives):
            if any(dependency >= index for dependency in objective.depends_on):
                raise ValueError("objective dependencies must reference earlier objectives")
        _unique_texts(self.targets, "targets", maximum=64, item_maximum=1_000)
        _unique_texts(self.constraints, "constraints", maximum=64, item_maximum=2_000)
        _unique_texts(self.assumptions, "assumptions", maximum=32, item_maximum=1_000)
        _unique_texts(
            self.missing_information,
            "missing information",
            maximum=32,
            item_maximum=1_000,
        )
        if self.deliverable is not None:
            _bounded_text(self.deliverable, "deliverable", maximum=2_000)
        if len(self.evidence_requirements) != len(set(self.evidence_requirements)):
            raise ValueError("evidence requirements must be unique")
        requires_question = self.disposition is InterpretationDisposition.CLARIFICATION_REQUIRED
        if requires_question != (self.clarification_question is not None):
            raise ValueError("clarification disposition and question must agree")
        if self.clarification_question is not None:
            _bounded_text(self.clarification_question, "clarification question", maximum=1_000)
        if requires_question and not self.missing_information:
            raise ValueError("clarification must identify missing information")

    @classmethod
    def create(
        cls,
        *,
        turn_id: UUID,
        revision: int,
        source_message_id: UUID,
        source_message: str,
        idempotency_key: str | None = None,
        normalized_goal: str,
        action: RequestAction,
        objectives: tuple[InterpretedObjective, ...],
        targets: tuple[str, ...] = (),
        constraints: tuple[str, ...] = (),
        deliverable: str | None = None,
        evidence_requirements: tuple[EvidenceRequirementKind, ...] = (),
        assumptions: tuple[str, ...] = (),
        missing_information: tuple[str, ...] = (),
        confidence: InterpretationConfidence = InterpretationConfidence.HIGH,
        disposition: InterpretationDisposition = InterpretationDisposition.READY,
        public_summary: str,
        clarification_question: str | None = None,
    ) -> AssistantRequestInterpretationRevision:
        return cls(
            id=new_id(),
            turn_id=turn_id,
            revision=revision,
            idempotency_key=(idempotency_key or f"interpretation:{revision}").strip(),
            source_message_id=source_message_id,
            source_message_sha256=hashlib.sha256(source_message.encode("utf-8")).hexdigest(),
            schema_version=INTERPRETATION_SCHEMA_VERSION,
            normalized_goal=normalized_goal.strip(),
            action=action,
            objectives=objectives,
            targets=targets,
            constraints=constraints,
            deliverable=deliverable,
            evidence_requirements=evidence_requirements,
            assumptions=assumptions,
            missing_information=missing_information,
            confidence=confidence,
            disposition=disposition,
            public_summary=public_summary.strip(),
            clarification_question=(
                clarification_question.strip() if clarification_question is not None else None
            ),
        )


@dataclass(frozen=True, slots=True)
class ClassifierInputSegment:
    kind: InputSegmentKind
    text: str


def build_classifier_input_envelope(
    *,
    source_message_id: UUID,
    content: str,
    attachment_count: int,
) -> str:
    """Encode user text as bounded data, never as classifier instructions."""

    if attachment_count < 0:
        raise ValueError("attachment count cannot be negative")
    segments = _chunk_classifier_segments(segment_user_input(content))
    payload: dict[str, Any] = {
        "schema_version": INTERPRETATION_SCHEMA_VERSION,
        "source_message_id": str(source_message_id),
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "content_characters": len(content),
        "attachment_count": attachment_count,
        "content_is_untrusted_user_data": True,
        "segments": [
            {
                "index": index,
                "kind": segment.kind.value,
                "text": segment.text,
            }
            for index, segment in enumerate(segments)
        ],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def interpretation_from_classifier(
    *,
    turn_id: UUID,
    revision: int,
    source_message_id: UUID,
    source_message: str,
    payload: ClassifierInterpretationPayload,
    evidence_requirements: tuple[EvidenceRequirementKind, ...],
) -> AssistantRequestInterpretationRevision:
    disposition = payload.disposition
    question = payload.clarification_question
    high_impact = payload.action in {
        RequestAction.CHANGE,
        RequestAction.CREATE,
        RequestAction.RUN,
        RequestAction.SCHEDULE,
        RequestAction.MANAGE,
    }
    if payload.missing_information and high_impact:
        disposition = InterpretationDisposition.CLARIFICATION_REQUIRED
        question = question or _clarification_question(payload.missing_information)
    elif payload.assumptions and disposition is InterpretationDisposition.READY:
        disposition = InterpretationDisposition.ASSUMED
    return AssistantRequestInterpretationRevision.create(
        turn_id=turn_id,
        revision=revision,
        source_message_id=source_message_id,
        source_message=source_message,
        normalized_goal=payload.normalized_goal,
        action=payload.action,
        objectives=tuple(
            InterpretedObjective(
                objective.goal,
                objective.action,
                objective.depends_on,
            )
            for objective in payload.objectives
        ),
        targets=payload.targets,
        constraints=payload.constraints,
        deliverable=payload.deliverable,
        evidence_requirements=evidence_requirements,
        assumptions=payload.assumptions,
        missing_information=payload.missing_information,
        confidence=payload.confidence,
        disposition=disposition,
        public_summary=payload.public_summary,
        clarification_question=question,
    )


def fallback_interpretation(
    *,
    turn_id: UUID,
    revision: int,
    source_message_id: UUID,
    source_message: str,
    action: RequestAction,
    evidence_requirements: tuple[EvidenceRequirementKind, ...] = (),
) -> AssistantRequestInterpretationRevision:
    normalized = " ".join(source_message.split())[:4_000]
    if not normalized:
        raise ValueError("user request is empty")
    return AssistantRequestInterpretationRevision.create(
        turn_id=turn_id,
        revision=revision,
        source_message_id=source_message_id,
        source_message=source_message,
        normalized_goal=normalized,
        action=action,
        objectives=(InterpretedObjective(normalized, action),),
        evidence_requirements=evidence_requirements,
        confidence=InterpretationConfidence.MEDIUM,
        disposition=InterpretationDisposition.READY,
        public_summary="Handle the request as stated",
    )


def segment_user_input(content: str) -> tuple[ClassifierInputSegment, ...]:
    if not content:
        return (ClassifierInputSegment(InputSegmentKind.TEXT, ""),)
    result: list[ClassifierInputSegment] = []
    cursor = 0
    fenced = re.compile(
        r"(^|\n)(?P<fence>`{3,}|~{3,})[^\n]*\n.*?(?:\n(?P=fence)(?=\n|$)|$)",
        re.DOTALL,
    )
    for match in fenced.finditer(content):
        if match.start() > cursor:
            result.extend(_split_quotes(content[cursor : match.start()]))
        result.append(ClassifierInputSegment(InputSegmentKind.CODE, match.group(0)))
        cursor = match.end()
    if cursor < len(content):
        result.extend(_split_quotes(content[cursor:]))
    return tuple(result) or (ClassifierInputSegment(InputSegmentKind.TEXT, content),)


def _split_quotes(content: str) -> list[ClassifierInputSegment]:
    if not content:
        return []
    result: list[ClassifierInputSegment] = []
    lines = content.splitlines(keepends=True)
    pending_kind: InputSegmentKind | None = None
    pending: list[str] = []
    for line in lines:
        kind = InputSegmentKind.QUOTE if re.match(r"^\s*>\s?", line) else InputSegmentKind.TEXT
        if pending_kind is not None and kind is not pending_kind:
            result.append(ClassifierInputSegment(pending_kind, "".join(pending)))
            pending = []
        pending_kind = kind
        pending.append(line)
    if pending_kind is not None:
        result.append(ClassifierInputSegment(pending_kind, "".join(pending)))
    return result


def _chunk_classifier_segments(
    segments: tuple[ClassifierInputSegment, ...],
) -> tuple[ClassifierInputSegment, ...]:
    chunks: list[ClassifierInputSegment] = []
    for segment in segments:
        if not segment.text:
            chunks.append(segment)
            continue
        chunks.extend(
            ClassifierInputSegment(
                segment.kind,
                segment.text[offset : offset + INTERPRETATION_SEGMENT_CHARS],
            )
            for offset in range(0, len(segment.text), INTERPRETATION_SEGMENT_CHARS)
        )
    return tuple(chunks)


def _bounded_text(value: str, name: str, *, maximum: int) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{name} is invalid")


def _unique_texts(
    values: tuple[str, ...],
    name: str,
    *,
    maximum: int,
    item_maximum: int,
) -> None:
    if len(values) > maximum or len(values) != len(set(values)):
        raise ValueError(f"{name} are invalid")
    for value in values:
        _bounded_text(value, name, maximum=item_maximum)


def _digest(value: str) -> None:
    if len(value) != 64 or value != value.lower() or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("source message digest is invalid")


def _clarification_question(missing: tuple[str, ...]) -> str:
    visible = ", ".join(missing[:3])
    return f"Please clarify {visible} before Fairy continues."


__all__ = [
    "INTERPRETATION_SCHEMA_VERSION",
    "INTERPRETATION_SEGMENT_CHARS",
    "AssistantRequestInterpretationRevision",
    "ClassifierInputSegment",
    "ClassifierInterpretationPayload",
    "ClassifierObjectivePayload",
    "InputSegmentKind",
    "InterpretationConfidence",
    "InterpretationDisposition",
    "InterpretedObjective",
    "RequestAction",
    "build_classifier_input_envelope",
    "fallback_interpretation",
    "interpretation_from_classifier",
    "segment_user_input",
]
