from __future__ import annotations

from datetime import datetime

from pydantic import Field, model_validator

from fairy_core.assistant.routing import RoutingComplexity, RoutingTaskKind
from fairy_core.contracts.common import ContractModel
from fairy_core.model_catalog.models import ModelSelectionMode


class ModelSelectionSnapshotInput(ContractModel):
    mode: ModelSelectionMode
    model_id: str | None = Field(default=None, min_length=1, max_length=255)
    revision: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_selection_shape(self) -> ModelSelectionSnapshotInput:
        if self.mode is ModelSelectionMode.AUTO and self.model_id is not None:
            raise ValueError("auto model selection cannot include a model id")
        if self.mode is ModelSelectionMode.MANUAL and self.model_id is None:
            raise ValueError("manual model selection requires a model id")
        return self


class ModelSelectionSnapshotModel(ContractModel):
    mode: ModelSelectionMode
    model_id: str | None = Field(default=None, min_length=1, max_length=255)
    allow_free_fallback: bool
    zero_data_retention: bool
    revision: int = Field(ge=0)
    captured_at: datetime


class RoutingDecisionModel(ContractModel):
    task_kind: RoutingTaskKind
    complexity: RoutingComplexity
    primary_model_id: str = Field(min_length=1, max_length=255)
    reviewer_model_id: str | None = Field(default=None, min_length=1, max_length=255)
    media_model_id: str | None = Field(default=None, min_length=1, max_length=255)
    estimated_output_tokens: int = Field(ge=256, le=16_384)
    estimated_cost_usd: str | None = Field(
        default=None,
        pattern=r"^(0|[1-9][0-9]*)(\.[0-9]+)?$",
    )
    cost_estimate_known: bool
    approval_required: bool
    public_summary: str = Field(min_length=1, max_length=240)


__all__ = [
    "ModelSelectionSnapshotInput",
    "ModelSelectionSnapshotModel",
    "RoutingDecisionModel",
]
