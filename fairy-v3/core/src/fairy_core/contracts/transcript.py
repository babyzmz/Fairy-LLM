from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from fairy_core.contracts.common import ContractModel
from fairy_core.contracts.models import MessageModel


class ImportedMessageModel(MessageModel):
    source_conversation_id: UUID
    source_message_id: UUID
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    imported_at: datetime
    read_only: Literal[True] = True


ConversationTranscriptItemModel = MessageModel | ImportedMessageModel


class MessagePageModel(ContractModel):
    items: tuple[ConversationTranscriptItemModel, ...]
    next_cursor: str | None


__all__ = [
    "ConversationTranscriptItemModel",
    "ImportedMessageModel",
    "MessagePageModel",
]
