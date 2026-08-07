from __future__ import annotations

from datetime import datetime
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
from fairy_core.assistant.trace_models import TraceStep, TraceStepKind, TurnTrace
from fairy_core.storage.pagination import StatePage


class AssistantRepository(Protocol):
    def save_turn(self, turn: AssistantTurn) -> None: ...

    def create_turn_if_absent(self, turn: AssistantTurn) -> tuple[AssistantTurn, bool]: ...

    def bind_turn_workflow(
        self,
        turn_id: UUID,
        *,
        workflow_run_id: UUID,
        engine_version: int,
    ) -> None: ...

    def update_turn(
        self,
        turn: AssistantTurn,
        *,
        expected_status: AssistantTurnStatus,
        expected_cancellation_revision: int,
    ) -> None: ...

    def get_turn(self, turn_id: UUID) -> AssistantTurn | None: ...

    def find_turn_by_idempotency_key(self, idempotency_key: str) -> AssistantTurn | None: ...

    def list_turns(
        self,
        *,
        statuses: frozenset[AssistantTurnStatus] | None = None,
        updated_since: datetime | None = None,
        limit: int = 200,
    ) -> tuple[AssistantTurn, ...]: ...

    def nonterminal_turns_for_tasks(
        self,
        task_ids: tuple[UUID, ...],
    ) -> tuple[AssistantTurn, ...]: ...

    def nonterminal_turn_for_conversation(
        self,
        conversation_id: UUID,
    ) -> AssistantTurn | None: ...

    def save_provider_attempt(self, attempt: ProviderAttempt) -> None: ...

    def update_provider_attempt(self, attempt: ProviderAttempt) -> None: ...

    def list_provider_attempts(self, turn_id: UUID) -> tuple[ProviderAttempt, ...]: ...

    def create_trace_if_absent(self, trace: TurnTrace) -> tuple[TurnTrace, bool]: ...

    def update_trace(self, trace: TurnTrace, *, expected_revision: int) -> None: ...

    def get_trace_by_turn_id(self, turn_id: UUID) -> TurnTrace | None: ...

    def allocate_trace_sequence(self, trace_id: UUID) -> int: ...

    def append_trace_step(self, step: TraceStep) -> None: ...

    def update_trace_step(self, step: TraceStep, *, expected_revision: int) -> None: ...

    def get_trace_step(self, step_id: UUID) -> TraceStep | None: ...

    def find_trace_step_by_command_run_id(
        self,
        command_run_id: UUID,
        *,
        kind: TraceStepKind | None = None,
    ) -> TraceStep | None: ...

    def list_trace_steps(self, turn_id: UUID) -> tuple[TraceStep, ...]: ...

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

    def purge_conversation_content(self, conversation_id: UUID) -> None: ...

    def purge_project_content(self, project_id: UUID) -> None: ...

    def purge_workspace_content(self, workspace_id: UUID) -> None: ...

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

    def find_tool_invocation_by_command_run_id(
        self,
        command_run_id: UUID,
    ) -> ToolInvocation | None: ...

    def list_tool_invocations(self, turn_id: UUID) -> tuple[ToolInvocation, ...]: ...

    def legacy_nonterminal_turn_ids(self) -> tuple[UUID, ...]: ...

    def resumable_waiting_turn_ids(self) -> tuple[UUID, ...]: ...

    def resumable_workflow_turn_ids(self) -> tuple[UUID, ...]: ...


__all__ = ["AssistantRepository"]
