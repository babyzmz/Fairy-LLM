from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from fairy_core.contracts.common import ContractModel
from fairy_core.contracts.model_routing import ModelSelectionSnapshotInput
from fairy_core.contracts.models import AssistantTurnModel


class AssistantMessageCancelInput(ContractModel):
    idempotency_key: str = Field(min_length=1, max_length=512)
    conversation_id: UUID | None = None

    @model_validator(mode="after")
    def validate_key(self) -> "AssistantMessageCancelInput":
        if not self.idempotency_key.strip():
            raise ValueError("Message idempotency key must not be blank")
        return self


class AssistantMessageCancellationResult(ContractModel):
    accepted: bool
    turn: AssistantTurnModel | None


class AssistantMessageSubmitInput(ContractModel):
    conversation_id: UUID
    content: str = Field(min_length=1, max_length=100_000)
    profile_id: str | None = Field(default=None, min_length=1, max_length=255)
    model_selection: ModelSelectionSnapshotInput | None = None
    idempotency_key: str = Field(min_length=1, max_length=512)
    source: Literal["chat", "pet", "stt"]

    @model_validator(mode="after")
    def validate_submission(self) -> "AssistantMessageSubmitInput":
        if not self.content.strip() or not self.idempotency_key.strip():
            raise ValueError("Message content and idempotency key must not be blank")
        if self.source == "pet" and len(self.content) > 4_000:
            raise ValueError("Pet messages cannot exceed 4000 characters")
        if (self.profile_id is None) == (self.model_selection is None):
            raise ValueError("provide exactly one of profile_id or model_selection")
        return self
