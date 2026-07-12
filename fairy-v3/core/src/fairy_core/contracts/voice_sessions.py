from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field, model_validator

from fairy_core.contracts.models import ContractModel, TaskIdInput


class VoiceSessionStatus(StrEnum):
    PREPARED = "prepared"
    CANCELLED = "cancelled"


class VoiceSessionStartInput(TaskIdInput):
    turn_id: UUID
    message_id: UUID | None = None
    start_offset: int = Field(ge=0, le=100_000)
    end_offset: int = Field(gt=0, le=100_000)
    idempotency_key: str = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def require_non_empty_range(self) -> VoiceSessionStartInput:
        if self.end_offset <= self.start_offset:
            raise ValueError("voice session range must be non-empty")
        return self


class VoiceSessionIdInput(ContractModel):
    session_id: UUID


class VoiceSessionModel(ContractModel):
    id: UUID
    task_id: UUID
    conversation_id: UUID
    turn_id: UUID
    message_id: UUID | None
    start_offset: int = Field(ge=0, le=100_000)
    end_offset: int = Field(gt=0, le=100_000)
    validated_text: str = Field(min_length=1, max_length=100_000)
    source_cursor: int = Field(ge=0)
    scope_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: VoiceSessionStatus
    created_at: datetime
    cancelled_at: datetime | None = None


__all__ = [
    "VoiceSessionIdInput",
    "VoiceSessionModel",
    "VoiceSessionStartInput",
    "VoiceSessionStatus",
]
