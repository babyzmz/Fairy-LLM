from datetime import datetime
from uuid import UUID

from pydantic import Field

from fairy_core.assistant.models import AssistantTurnStatus
from fairy_core.contracts.common import ContractModel


class AssistantTurnPresentation(ContractModel):
    id: UUID
    conversation_id: UUID
    status: AssistantTurnStatus
    cancellation_revision: int = Field(ge=0)
    cancellation_pending: bool
    error_code: str | None
    updated_at: datetime


class AssistantReplyPresentation(ContractModel):
    id: UUID
    text: str = Field(min_length=1, max_length=1200)


class AssistantConversationPresentation(ContractModel):
    conversation_id: UUID
    turn: AssistantTurnPresentation | None
    reply: AssistantReplyPresentation | None
