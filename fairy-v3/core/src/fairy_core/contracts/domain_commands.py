from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from fairy_core.contracts.common import ContractModel
from fairy_core.contracts.models import AssistantTurnModel, ConversationModel


class AssistantCommandInput(ContractModel):
    text: str = Field(min_length=1, max_length=512)
    idempotency_key: str = Field(min_length=1, max_length=512)
    conversation_id: UUID | None = None
    turn_id: UUID | None = None
    expected_cancellation_revision: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_key(self) -> "AssistantCommandInput":
        if not self.idempotency_key.strip():
            raise ValueError("Command idempotency key must not be blank")
        return self


class AssistantCommandUiAction(ContractModel):
    kind: Literal["show_project", "request_permission"]
    profile: Literal["observe", "standard", "autonomous"] | None = None


class AssistantCommandResult(ContractModel):
    command: Literal["new", "clear", "stop", "project", "permission", "help"]
    conversation: ConversationModel | None = None
    turn: AssistantTurnModel | None = None
    ui_action: AssistantCommandUiAction | None = None
    notice: str | None = None
