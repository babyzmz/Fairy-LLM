from __future__ import annotations

from typing import Protocol
from uuid import UUID

from fairy_core.assistant.models import (
    AssistantTurn,
    AssistantTurnStatus,
    ConversationMove,
    ImportedMessage,
    Message,
    MessageRole,
    MessageVisibility,
    ProviderAttempt,
    ToolInvocation,
    ToolInvocationStatus,
)
from fairy_core.storage.pagination import StatePage


class AssistantRepository(Protocol):
    def save_turn(self, turn: AssistantTurn) -> None: ...

    def create_turn_if_absent(self, turn: AssistantTurn) -> tuple[AssistantTurn, bool]: ...

    def update_turn(
        self,
        turn: AssistantTurn,
        *,
        expected_status: AssistantTurnStatus,
        expected_cancellation_revision: int,
    ) -> None: ...

    def get_turn(self, turn_id: UUID) -> AssistantTurn | None: ...

    def find_turn_by_idempotency_key(self, idempotency_key: str) -> AssistantTurn | None: ...

    def save_provider_attempt(self, attempt: ProviderAttempt) -> None: ...

    def update_provider_attempt(self, attempt: ProviderAttempt) -> None: ...

    def list_provider_attempts(self, turn_id: UUID) -> tuple[ProviderAttempt, ...]: ...

    def append_message(self, message: Message) -> None: ...

    def append_imported_message(self, message: ImportedMessage) -> None: ...

    def get_message(self, message_id: UUID) -> Message | None: ...

    def message_for_turn(self, turn_id: UUID, role: MessageRole) -> Message | None: ...

    def next_message_sequence(self, conversation_id: UUID) -> int: ...

    def list_messages(
        self,
        *,
        conversation_id: UUID,
        limit: int,
        cursor: str | None,
        allowed_visibilities: frozenset[MessageVisibility] | None = None,
    ) -> StatePage[Message]: ...

    def list_transcript(
        self,
        *,
        conversation_id: UUID,
        limit: int,
        cursor: str | None,
        allowed_visibilities: frozenset[MessageVisibility] | None = None,
    ) -> StatePage[Message | ImportedMessage]: ...

    def save_conversation_move(self, move: ConversationMove) -> None: ...

    def find_conversation_move(self, idempotency_key: str) -> ConversationMove | None: ...

    def save_tool_invocation(self, invocation: ToolInvocation) -> None: ...

    def update_tool_invocation(
        self,
        invocation: ToolInvocation,
        *,
        expected_status: ToolInvocationStatus,
    ) -> None: ...

    def get_tool_invocation(self, invocation_id: UUID) -> ToolInvocation | None: ...

    def list_tool_invocations(self, turn_id: UUID) -> tuple[ToolInvocation, ...]: ...

    def live_turn_ids(self) -> tuple[UUID, ...]: ...

    def interrupt_orphaned_turns(
        self,
        *,
        live_turn_ids: tuple[UUID, ...],
    ) -> tuple[AssistantTurn, ...]: ...


__all__ = ["AssistantRepository"]
