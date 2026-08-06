from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from math import ceil
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from fairy_core.assistant.evidence import EvidenceRequirementKind
from fairy_core.model_catalog.models import (
    MODEL_ALLOWLIST_BY_ID,
    ModelAvailability,
    ModelCatalogSnapshot,
    ModelCategory,
    ModelEndpointKind,
    ModelSelectionMode,
    ModelSelectionSnapshot,
)
from fairy_core.providers import (
    ModelExecutionRole,
    ModelMessage,
    ModelRequest,
    ModelRole,
    ModelTool,
    ProviderCapability,
)

DEEPSEEK_MODEL_ID = "deepseek/deepseek-v4-pro"
GLM_MODEL_ID = "z-ai/glm-5.2"
KIMI_MODEL_ID = "moonshotai/kimi-k2.7-code"
IMAGE_MODEL_ID = "google/gemini-3.1-flash-lite-image"
MUSIC_MODEL_ID = "google/lyria-3-pro-preview"
VIDEO_MODEL_ID = "bytedance/seedance-2.0"
NEMOTRON_FREE_MODEL_ID = "nvidia/nemotron-3-ultra-550b-a55b:free"
QWEN_FREE_MODEL_ID = "qwen/qwen3-coder:free"

ROUTER_MAX_OUTPUT_TOKENS = 384
CODE_MAX_OUTPUT_TOKENS = 16_384
TURN_AUTO_APPROVAL_USD = Decimal("0.25")
TURN_AUTOMATIC_TARGET_USD = Decimal("0.10")


class RoutingTaskKind(StrEnum):
    GENERAL = "general"
    REASONING = "reasoning"
    CODE = "code"
    BROWSER = "browser"
    IMAGE = "image"
    MUSIC = "music"
    VIDEO = "video"


class RoutingComplexity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class _RoutingPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    task_kind: RoutingTaskKind
    complexity: RoutingComplexity
    needs_review: bool
    requires_workspace_changes: bool
    evidence_requirements: tuple[EvidenceRequirementKind, ...] = Field(
        default=(),
        max_length=len(EvidenceRequirementKind),
    )
    estimated_output_tokens: int = Field(ge=256, le=16_384)
    public_summary: str = Field(min_length=1, max_length=240)

    @field_validator("evidence_requirements")
    @classmethod
    def require_unique_evidence(
        cls,
        value: tuple[EvidenceRequirementKind, ...],
    ) -> tuple[EvidenceRequirementKind, ...]:
        if len(value) != len(set(value)):
            raise ValueError("evidence requirements must be unique")
        return value


class EvidenceClassificationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    evidence_requirements: tuple[EvidenceRequirementKind, ...] = Field(
        default=(),
        max_length=len(EvidenceRequirementKind),
    )
    requires_workspace_changes: bool
    public_summary: str = Field(min_length=1, max_length=240)

    @field_validator("evidence_requirements")
    @classmethod
    def require_unique_evidence(
        cls,
        value: tuple[EvidenceRequirementKind, ...],
    ) -> tuple[EvidenceRequirementKind, ...]:
        if len(value) != len(set(value)):
            raise ValueError("evidence requirements must be unique")
        return value


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    task_kind: RoutingTaskKind
    complexity: RoutingComplexity
    primary_model_id: str
    reviewer_model_id: str | None
    media_model_id: str | None
    estimated_output_tokens: int
    estimated_cost_usd: str | None
    cost_estimate_known: bool
    approval_required: bool
    requires_workspace_changes: bool
    public_summary: str
    evidence_requirements: tuple[EvidenceRequirementKind, ...] = ()
    evidence_classified: bool = True

    def __post_init__(self) -> None:
        for model_id in (
            self.primary_model_id,
            self.reviewer_model_id,
            self.media_model_id,
        ):
            if model_id is not None and model_id not in MODEL_ALLOWLIST_BY_ID:
                raise ValueError("routing decision must use an allowlisted model")
        primary = MODEL_ALLOWLIST_BY_ID[self.primary_model_id]
        if primary.endpoint_kind is not ModelEndpointKind.CHAT:
            raise ValueError("routing primary model must use the chat endpoint")
        if self.reviewer_model_id is not None:
            reviewer = MODEL_ALLOWLIST_BY_ID[self.reviewer_model_id]
            if reviewer.endpoint_kind is not ModelEndpointKind.CHAT:
                raise ValueError("routing reviewer must use the chat endpoint")
        if self.media_model_id is not None:
            media = MODEL_ALLOWLIST_BY_ID[self.media_model_id]
            if media.endpoint_kind is ModelEndpointKind.CHAT:
                raise ValueError("routing media model must use a specialized endpoint")
        if not 256 <= self.estimated_output_tokens <= 16_384:
            raise ValueError("routing output estimate is outside the supported range")
        if not self.public_summary.strip() or len(self.public_summary) > 240:
            raise ValueError("routing public summary is invalid")
        if self.cost_estimate_known != (self.estimated_cost_usd is not None):
            raise ValueError("routing cost estimate state is inconsistent")
        if self.estimated_cost_usd is not None:
            _non_negative_decimal(self.estimated_cost_usd, "estimated_cost_usd")
        normalized_requirements = tuple(
            EvidenceRequirementKind(value) for value in self.evidence_requirements
        )
        if len(normalized_requirements) != len(set(normalized_requirements)):
            raise ValueError("routing evidence requirements must be unique")
        if not isinstance(self.evidence_classified, bool):
            raise ValueError("routing evidence_classified must be a boolean")
        object.__setattr__(self, "evidence_requirements", normalized_requirements)

    @property
    def execution_model_ids(self) -> tuple[str, ...]:
        return tuple(
            model_id
            for model_id in (
                self.primary_model_id,
                self.reviewer_model_id,
                self.media_model_id,
            )
            if model_id is not None
        )

    @property
    def media_tool_name(self) -> str | None:
        return {
            IMAGE_MODEL_ID: "media.images.generate",
            MUSIC_MODEL_ID: "media.audio.generate",
            VIDEO_MODEL_ID: "media.videos.start",
        }.get(self.media_model_id)


def build_router_request(
    *,
    profile_id: str,
    user_request: str,
    attachment_count: int,
    selection: ModelSelectionSnapshot,
    fallback_profile_ids: tuple[str, ...],
) -> ModelRequest:
    if selection.mode is not ModelSelectionMode.AUTO:
        raise ValueError("only Auto selection can invoke the model router")
    schema = _RoutingPayload.model_json_schema()
    return ModelRequest.create(
        profile_id=profile_id,
        messages=(
            ModelMessage.create(
                role=ModelRole.SYSTEM,
                content=(
                    "Classify the user's requested outcome for Fairy. Return only the strict "
                    "RoutingDecision JSON. Do not expose hidden reasoning. public_summary must be "
                    "a short user-safe explanation of the classification, without a first-person "
                    "promise, proposed answer, or repetition of the request. Select image, music, "
                    "or video only when generation of that medium is the requested deliverable. "
                    "Set requires_workspace_changes=true only when the requested outcome must "
                    "create, modify, delete, rename, test, or run durable Workspace files. Code "
                    "questions, explanations, and reviews that do not request edits must be false; "
                    "specialized image, music, and video generation must also be false. Use "
                    "browser for inspecting, clicking, scrolling, testing, or capturing an "
                    "existing page or Preview; a screenshot is a browser capture, not generated "
                    "media. Classify facts that depend on the currently bound Workspace as "
                    "workspace_structure and/or workspace_content; recent public facts as "
                    "web_current; current Preview, Browser, device, or Runtime state as "
                    "runtime_current; and the user's current Memory, Knowledge, Documents, or "
                    "connected private data as private_current. Stable conversation and durable "
                    "common knowledge need no evidence. When uncertain, require evidence."
                ),
            ),
            ModelMessage.create(
                role=ModelRole.USER,
                content=json.dumps(
                    {
                        "attachment_count": attachment_count,
                        "request": user_request,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            ),
        ),
        tools=(),
        required_capabilities=frozenset(
            {ProviderCapability.TEXT, ProviderCapability.STRUCTURED_OUTPUT}
        ),
        max_output_tokens=ROUTER_MAX_OUTPUT_TOKENS,
        model_role=ModelExecutionRole.COORDINATOR,
        fallback_profile_ids=fallback_profile_ids,
        allow_profile_fallback=False,
        response_schema_name="fairy_routing_decision",
        response_schema=schema,
        require_parameters=True,
        deny_data_collection=True,
        zero_data_retention=selection.zero_data_retention,
    )


def parse_router_output(value: str) -> _RoutingPayload:
    try:
        return _RoutingPayload.model_validate_json(value)
    except ValidationError as error:
        raise ValueError("router returned invalid structured output") from error


def auto_routing_decision(
    *,
    routed: _RoutingPayload,
    catalog: ModelCatalogSnapshot,
    user_request: str,
    attachment_count: int,
    allow_free_fallback: bool,
) -> RoutingDecision:
    from fairy_core.assistant.intent_guard import guarded_task_kind

    task_kind = guarded_task_kind(
        user_request=user_request,
        routed_kind=routed.task_kind,
    )
    media_model_id = {
        RoutingTaskKind.IMAGE: IMAGE_MODEL_ID,
        RoutingTaskKind.MUSIC: MUSIC_MODEL_ID,
        RoutingTaskKind.VIDEO: VIDEO_MODEL_ID,
    }.get(task_kind)
    if task_kind in {RoutingTaskKind.CODE, RoutingTaskKind.BROWSER}:
        primary_model_id = KIMI_MODEL_ID
    elif task_kind is RoutingTaskKind.REASONING or (
        task_kind is RoutingTaskKind.GENERAL and routed.complexity is RoutingComplexity.HIGH
    ):
        primary_model_id = GLM_MODEL_ID
    else:
        primary_model_id = DEEPSEEK_MODEL_ID
    if attachment_count and task_kind not in {
        RoutingTaskKind.IMAGE,
        RoutingTaskKind.MUSIC,
        RoutingTaskKind.VIDEO,
    }:
        primary_model_id = KIMI_MODEL_ID

    preferred_model_id = primary_model_id
    if (
        attachment_count == 0
        and task_kind is not RoutingTaskKind.BROWSER
        and not _catalog_model_usable(catalog, primary_model_id)
    ):
        candidates = {
            KIMI_MODEL_ID: (DEEPSEEK_MODEL_ID, GLM_MODEL_ID),
            GLM_MODEL_ID: (DEEPSEEK_MODEL_ID,),
            DEEPSEEK_MODEL_ID: (GLM_MODEL_ID,),
        }.get(primary_model_id, ())
        if allow_free_fallback:
            candidates = (
                *candidates,
                QWEN_FREE_MODEL_ID if task_kind is RoutingTaskKind.CODE else NEMOTRON_FREE_MODEL_ID,
            )
        primary_model_id = next(
            (model_id for model_id in candidates if _catalog_model_usable(catalog, model_id)),
            primary_model_id,
        )

    reviewer_model_id: str | None = None
    if task_kind is not RoutingTaskKind.BROWSER and (
        routed.needs_review or routed.complexity is RoutingComplexity.HIGH
    ):
        preferred_reviewer = DEEPSEEK_MODEL_ID if primary_model_id == GLM_MODEL_ID else GLM_MODEL_ID
        if _catalog_model_usable(catalog, preferred_reviewer):
            reviewer_model_id = preferred_reviewer

    estimated_output_tokens = max(
        routed.estimated_output_tokens,
        {
            RoutingTaskKind.CODE: CODE_MAX_OUTPUT_TOKENS,
            RoutingTaskKind.IMAGE: 1_024,
            RoutingTaskKind.MUSIC: 1_024,
            RoutingTaskKind.VIDEO: 1_024,
        }.get(task_kind, 256),
    )
    route_calls = (
        (DEEPSEEK_MODEL_ID, ROUTER_MAX_OUTPUT_TOKENS),
        (primary_model_id, estimated_output_tokens),
        *(((reviewer_model_id, estimated_output_tokens),) if reviewer_model_id else ()),
    )
    estimate = estimate_text_cost(
        catalog,
        calls=route_calls,
        prompt_characters=max(1, len(user_request)),
    )
    if media_model_id is not None and task_kind is RoutingTaskKind.IMAGE:
        media_estimate = _single_media_request_cost(catalog, media_model_id)
        estimate = (
            estimate + media_estimate
            if estimate is not None and media_estimate is not None
            else None
        )
    execution_models = tuple(
        model_id
        for model_id in (primary_model_id, reviewer_model_id, media_model_id)
        if model_id is not None
    )
    paid_execution_count = sum(
        1 for model_id in set(execution_models) if MODEL_ALLOWLIST_BY_ID[model_id].paid
    )
    approval_required = (
        (task_kind is RoutingTaskKind.IMAGE and estimate is None)
        or (
            task_kind is RoutingTaskKind.IMAGE
            and estimate is not None
            and estimate > TURN_AUTOMATIC_TARGET_USD
        )
        or estimate is None
        or estimate > TURN_AUTO_APPROVAL_USD
        or paid_execution_count > 2
    )
    public_summary = routed.public_summary.strip()
    if task_kind is RoutingTaskKind.BROWSER and routed.task_kind is not task_kind:
        public_summary = "Inspect the current Preview with the scoped Browser."
    elif task_kind is RoutingTaskKind.GENERAL and routed.task_kind in {
        RoutingTaskKind.IMAGE,
        RoutingTaskKind.MUSIC,
        RoutingTaskKind.VIDEO,
    }:
        public_summary = "Handle the request without generating unrequested media."
    if primary_model_id != preferred_model_id:
        fallback_note = " A compatible available model was selected."
        public_summary = f"{public_summary[: 240 - len(fallback_note)]}{fallback_note}"
    return RoutingDecision(
        task_kind=task_kind,
        complexity=routed.complexity,
        primary_model_id=primary_model_id,
        reviewer_model_id=reviewer_model_id,
        media_model_id=media_model_id,
        estimated_output_tokens=estimated_output_tokens,
        estimated_cost_usd=_decimal_text(estimate) if estimate is not None else None,
        cost_estimate_known=estimate is not None,
        approval_required=approval_required,
        requires_workspace_changes=(
            routed.requires_workspace_changes and task_kind is not RoutingTaskKind.BROWSER
        ),
        public_summary=public_summary,
        evidence_requirements=routed.evidence_requirements,
    )


def manual_routing_decision(
    *,
    selection: ModelSelectionSnapshot,
    catalog: ModelCatalogSnapshot,
    user_request: str,
    evidence: EvidenceClassificationPayload | None = None,
) -> RoutingDecision:
    if selection.mode is not ModelSelectionMode.MANUAL or selection.model_id is None:
        raise ValueError("manual routing requires a selected model")
    allowed = MODEL_ALLOWLIST_BY_ID[selection.model_id]
    if allowed.endpoint_kind is not ModelEndpointKind.CHAT:
        task_kind = {
            ModelEndpointKind.IMAGES: RoutingTaskKind.IMAGE,
            ModelEndpointKind.AUDIO: RoutingTaskKind.MUSIC,
            ModelEndpointKind.VIDEOS: RoutingTaskKind.VIDEO,
        }[allowed.endpoint_kind]
        estimate = estimate_text_cost(
            catalog,
            calls=((DEEPSEEK_MODEL_ID, 1_024),),
            prompt_characters=max(1, len(user_request)),
        )
        if task_kind is RoutingTaskKind.IMAGE:
            media_estimate = _single_media_request_cost(catalog, selection.model_id)
            estimate = (
                estimate + media_estimate
                if estimate is not None and media_estimate is not None
                else None
            )
        approval_required = (
            estimate is None
            or estimate > TURN_AUTO_APPROVAL_USD
            or (task_kind is RoutingTaskKind.IMAGE and estimate > TURN_AUTOMATIC_TARGET_USD)
        )
        return RoutingDecision(
            task_kind=task_kind,
            complexity=RoutingComplexity.MEDIUM,
            primary_model_id=DEEPSEEK_MODEL_ID,
            reviewer_model_id=None,
            media_model_id=selection.model_id,
            estimated_output_tokens=1_024,
            estimated_cost_usd=_decimal_text(estimate) if estimate is not None else None,
            cost_estimate_known=estimate is not None,
            approval_required=approval_required,
            requires_workspace_changes=False,
            public_summary=(f"DeepSeek will prepare the specification for {allowed.display_name}."),
            evidence_requirements=(),
        )
    task_kind = (
        RoutingTaskKind.CODE
        if allowed.category in {ModelCategory.CODE, ModelCategory.FREE_CODE}
        else RoutingTaskKind.GENERAL
    )
    output_tokens = CODE_MAX_OUTPUT_TOKENS if task_kind is RoutingTaskKind.CODE else 4_096
    estimate = estimate_text_cost(
        catalog,
        calls=((selection.model_id, output_tokens),),
        prompt_characters=max(1, len(user_request)),
    )
    return RoutingDecision(
        task_kind=task_kind,
        complexity=RoutingComplexity.MEDIUM,
        primary_model_id=selection.model_id,
        reviewer_model_id=None,
        media_model_id=None,
        estimated_output_tokens=output_tokens,
        estimated_cost_usd=_decimal_text(estimate) if estimate is not None else None,
        cost_estimate_known=estimate is not None,
        approval_required=(allowed.paid and estimate is None)
        or (estimate is not None and estimate > TURN_AUTO_APPROVAL_USD),
        requires_workspace_changes=(
            evidence.requires_workspace_changes if evidence is not None else False
        ),
        public_summary=(
            evidence.public_summary.strip()
            if evidence is not None
            else f"Using {allowed.display_name} for this turn."
        ),
        evidence_requirements=(
            evidence.evidence_requirements if evidence is not None else ()
        ),
        evidence_classified=evidence is not None,
    )


def build_manual_evidence_request(
    *,
    profile_id: str,
    user_request: str,
    selection: ModelSelectionSnapshot,
    use_structured_output: bool,
) -> ModelRequest:
    if selection.mode is not ModelSelectionMode.MANUAL or selection.model_id is None:
        raise ValueError("manual evidence classification requires a selected model")
    schema = EvidenceClassificationPayload.model_json_schema()
    system = ModelMessage.create(
        role=ModelRole.SYSTEM,
        content=(
            "Classify only whether this Fairy request requires evidence from current state. "
            "Use workspace_structure for current file layout or project metadata, "
            "workspace_content for current file contents, web_current for recent public facts, "
            "runtime_current for the current Browser, Preview, device, or Runtime, and "
            "private_current for current Memory, Knowledge, Documents, or connected private "
            "data. Stable conversation and durable common knowledge use an empty list. Set "
            "requires_workspace_changes only when the requested result must modify durable "
            "Workspace files. When uncertain, require evidence. Return no answer and no hidden "
            "reasoning; public_summary is one short user-safe classification summary."
        ),
    )
    user = ModelMessage.create(role=ModelRole.USER, content=user_request)
    if use_structured_output:
        return ModelRequest.create(
            profile_id=profile_id,
            messages=(system, user),
            tools=(),
            required_capabilities=frozenset(
                {ProviderCapability.TEXT, ProviderCapability.STRUCTURED_OUTPUT}
            ),
            max_output_tokens=ROUTER_MAX_OUTPUT_TOKENS,
            model_role=ModelExecutionRole.COORDINATOR,
            fallback_profile_ids=(),
            allow_profile_fallback=False,
            response_schema_name="fairy_evidence_classification",
            response_schema=schema,
            require_parameters=True,
            deny_data_collection=True,
            zero_data_retention=selection.zero_data_retention,
        )
    return ModelRequest.create(
        profile_id=profile_id,
        messages=(system, user),
        tools=(
            ModelTool.create(
                name="evidence.classify",
                description="Return the required evidence classification for this Turn.",
                input_schema=schema,
            ),
        ),
        required_capabilities=frozenset(
            {ProviderCapability.TEXT, ProviderCapability.TOOLS}
        ),
        max_output_tokens=ROUTER_MAX_OUTPUT_TOKENS,
        model_role=ModelExecutionRole.COORDINATOR,
        fallback_profile_ids=(),
        allow_profile_fallback=False,
        deny_data_collection=True,
        zero_data_retention=selection.zero_data_retention,
    )


def parse_evidence_classification(value: str) -> EvidenceClassificationPayload:
    try:
        return EvidenceClassificationPayload.model_validate_json(value)
    except ValidationError as error:
        raise ValueError("evidence classifier returned invalid structured output") from error


def estimate_text_cost(
    catalog: ModelCatalogSnapshot,
    *,
    calls: tuple[tuple[str, int], ...],
    prompt_characters: int,
) -> Decimal | None:
    entries = {entry.model_id: entry for entry in catalog.entries}
    prompt_tokens = max(1, ceil(prompt_characters / 4)) + 2_048
    total = Decimal(0)
    prior_output = 0
    for model_id, output_tokens in calls:
        allowed = MODEL_ALLOWLIST_BY_ID[model_id]
        if not allowed.paid:
            continue
        entry = entries.get(model_id)
        if entry is None:
            return None
        prompt_price = next(
            (
                price
                for price in entry.prices
                if price.billable == "prompt" and price.unit == "token"
            ),
            None,
        )
        completion_price = next(
            (
                price
                for price in entry.prices
                if price.billable == "completion" and price.unit == "token"
            ),
            None,
        )
        request_prices = tuple(price for price in entry.prices if price.unit == "request")
        if prompt_price is None or completion_price is None:
            if len(request_prices) != 1:
                return None
            total += _non_negative_decimal(request_prices[0].cost_usd, "request price")
        else:
            total += _non_negative_decimal(prompt_price.cost_usd, "prompt price") * (
                prompt_tokens + prior_output
            )
            total += (
                _non_negative_decimal(
                    completion_price.cost_usd,
                    "completion price",
                )
                * output_tokens
            )
        prior_output += output_tokens
    return total


def routing_decision_record(decision: RoutingDecision) -> dict[str, Any]:
    return {
        "task_kind": decision.task_kind.value,
        "complexity": decision.complexity.value,
        "primary_model_id": decision.primary_model_id,
        "reviewer_model_id": decision.reviewer_model_id,
        "media_model_id": decision.media_model_id,
        "estimated_output_tokens": decision.estimated_output_tokens,
        "estimated_cost_usd": decision.estimated_cost_usd,
        "cost_estimate_known": decision.cost_estimate_known,
        "approval_required": decision.approval_required,
        "requires_workspace_changes": decision.requires_workspace_changes,
        "public_summary": decision.public_summary,
        "evidence_requirements": [value.value for value in decision.evidence_requirements],
        "evidence_classified": decision.evidence_classified,
    }


def routing_decision_from_record(record: object) -> RoutingDecision | None:
    if record is None:
        return None
    if not isinstance(record, dict):
        raise ValueError("stored routing decision is invalid")
    return RoutingDecision(
        task_kind=RoutingTaskKind(record["task_kind"]),
        complexity=RoutingComplexity(record["complexity"]),
        primary_model_id=str(record["primary_model_id"]),
        reviewer_model_id=(
            str(record["reviewer_model_id"])
            if record.get("reviewer_model_id") is not None
            else None
        ),
        media_model_id=(
            str(record["media_model_id"]) if record.get("media_model_id") is not None else None
        ),
        estimated_output_tokens=int(record["estimated_output_tokens"]),
        estimated_cost_usd=(
            str(record["estimated_cost_usd"])
            if record.get("estimated_cost_usd") is not None
            else None
        ),
        cost_estimate_known=bool(record["cost_estimate_known"]),
        approval_required=bool(record["approval_required"]),
        requires_workspace_changes=bool(record.get("requires_workspace_changes", False)),
        public_summary=str(record["public_summary"]),
        evidence_requirements=tuple(
            EvidenceRequirementKind(str(value))
            for value in record.get("evidence_requirements", ())
        ),
        evidence_classified=bool(record.get("evidence_classified", True)),
    )


def _non_negative_decimal(value: str, name: str) -> Decimal:
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"{name} is invalid") from error
    if not result.is_finite() or result < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return result


def _catalog_model_usable(catalog: ModelCatalogSnapshot, model_id: str) -> bool:
    return any(
        entry.model_id == model_id and entry.availability is not ModelAvailability.UNAVAILABLE
        for entry in catalog.entries
    )


def _single_media_request_cost(
    catalog: ModelCatalogSnapshot,
    model_id: str,
) -> Decimal | None:
    entry = next((item for item in catalog.entries if item.model_id == model_id), None)
    if entry is None:
        return None
    prices = tuple(
        price
        for price in entry.prices
        if price.unit in {"request", "image"} and price.variant in {None, "1k", "1024x1024"}
    )
    if len(prices) != 1:
        return None
    return _non_negative_decimal(prices[0].cost_usd, "media request price")


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


__all__ = [
    "DEEPSEEK_MODEL_ID",
    "GLM_MODEL_ID",
    "IMAGE_MODEL_ID",
    "KIMI_MODEL_ID",
    "MUSIC_MODEL_ID",
    "NEMOTRON_FREE_MODEL_ID",
    "QWEN_FREE_MODEL_ID",
    "ROUTER_MAX_OUTPUT_TOKENS",
    "TURN_AUTOMATIC_TARGET_USD",
    "VIDEO_MODEL_ID",
    "ModelExecutionRole",
    "RoutingComplexity",
    "RoutingDecision",
    "RoutingTaskKind",
    "auto_routing_decision",
    "build_router_request",
    "estimate_text_cost",
    "manual_routing_decision",
    "parse_router_output",
    "routing_decision_from_record",
    "routing_decision_record",
]
