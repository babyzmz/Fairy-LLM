from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Table, and_, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, Engine, RowMapping

from fairy_core.assistant.content_purge_repository import (
    purge_conversation_content,
    purge_project_content,
    purge_workspace_content,
)
from fairy_core.assistant.evidence import (
    evidence_receipt_from_record,
    evidence_receipt_record,
)
from fairy_core.assistant.interpretation_repository import (
    AssistantInterpretationRepositoryMixin,
)
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
from fairy_core.assistant.repository_records import provider_attempt_values
from fairy_core.assistant.routing import (
    routing_decision_from_record,
    routing_decision_record,
)
from fairy_core.assistant.trace_repository import TurnTraceRepositoryMixin
from fairy_core.assistant.workflow_projection import attach_workflow_summary
from fairy_core.commanding.models import CommandStatus
from fairy_core.commanding.schema import command_runs
from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.model_catalog.models import (
    ModelEndpointKind,
    ModelSelectionMode,
    ModelSelectionSnapshot,
)
from fairy_core.persistence.session import SqlAlchemySession
from fairy_core.persistence.tenant import normalize_tenant_id
from fairy_core.providers import (
    ModelExecutionRole,
    ProviderAttemptStatus,
    ProviderErrorCategory,
)
from fairy_core.storage.pagination import StatePage, validate_limit
from fairy_core.storage.schema import (
    assistant_imported_messages,
    assistant_message_sequences,
    assistant_messages,
    assistant_provider_attempts,
    assistant_tool_invocations,
    assistant_turns,
    conversation_moves,
    workflow_runs,
)
from fairy_core.workflow.models import WorkflowRunStatus


class SqlAlchemyAssistantRepository(
    AssistantInterpretationRepositoryMixin,
    TurnTraceRepositoryMixin,
):
    def __init__(self, bind: Engine | Connection, *, tenant_id: str) -> None:
        if bind.dialect.name not in {"postgresql", "sqlite"}:
            raise ValueError(f"unsupported assistant repository dialect: {bind.dialect.name}")
        self._tenant_id = normalize_tenant_id(tenant_id)
        self._session = SqlAlchemySession(bind)

    def save_turn(self, turn: AssistantTurn) -> None:
        scoped_values = {"tenant_id": self._tenant_id, **self._turn_values(turn)}
        with self._session.write() as connection:
            connection.execute(insert(assistant_turns).values(**scoped_values))

    def create_turn_if_absent(self, turn: AssistantTurn) -> tuple[AssistantTurn, bool]:
        values = {"tenant_id": self._tenant_id, **self._turn_values(turn)}
        statement = self._insert(assistant_turns).values(**values)
        statement = statement.on_conflict_do_nothing(
            index_elements=[assistant_turns.c.tenant_id, assistant_turns.c.idempotency_key]
        )
        with self._session.write() as connection:
            inserted_id = connection.execute(
                statement.returning(assistant_turns.c.id)
            ).scalar_one_or_none()
            row = (
                connection.execute(
                    select(assistant_turns).where(
                        assistant_turns.c.tenant_id == self._tenant_id,
                        assistant_turns.c.idempotency_key == turn.idempotency_key,
                    )
                )
                .mappings()
                .one()
            )
        return self._with_workflow_summary(self._turn_from_row(row)), inserted_id is not None

    def bind_turn_workflow(
        self,
        turn_id: UUID,
        *,
        workflow_run_id: UUID,
        engine_version: int,
    ) -> None:
        if engine_version < 2:
            raise ValueError("Workflow-backed Assistant engine version must be at least 2")
        with self._session.write() as connection:
            changed = connection.execute(
                update(assistant_turns)
                .where(
                    assistant_turns.c.tenant_id == self._tenant_id,
                    assistant_turns.c.id == str(turn_id),
                    assistant_turns.c.workflow_run_id.is_(None),
                    assistant_turns.c.execution_engine_version == engine_version,
                )
                .values(workflow_run_id=str(workflow_run_id))
            ).rowcount
        if changed != 1:
            current = self.get_turn(turn_id)
            if current is None or current.workflow_run_id != workflow_run_id:
                raise InvalidTransitionError("Assistant Turn Workflow binding changed concurrently")

    def update_turn(
        self,
        turn: AssistantTurn,
        *,
        expected_status: AssistantTurnStatus,
        expected_cancellation_revision: int,
    ) -> None:
        with self._session.write() as connection:
            result = connection.execute(
                update(assistant_turns)
                .where(
                    assistant_turns.c.tenant_id == self._tenant_id,
                    assistant_turns.c.id == str(turn.id),
                    assistant_turns.c.conversation_id == str(turn.conversation_id),
                    assistant_turns.c.task_id == str(turn.task_id),
                    assistant_turns.c.profile_id == turn.profile_id,
                    assistant_turns.c.scope_digest == turn.scope_digest,
                    assistant_turns.c.memory_snapshot_id == str(turn.memory_snapshot_id),
                    assistant_turns.c.memory_snapshot_hash == turn.memory_snapshot_hash,
                    assistant_turns.c.knowledge_snapshot_id
                    == (
                        str(turn.knowledge_snapshot_id)
                        if turn.knowledge_snapshot_id is not None
                        else None
                    ),
                    assistant_turns.c.knowledge_snapshot_hash == turn.knowledge_snapshot_hash,
                    assistant_turns.c.harness_manifest_id
                    == (
                        str(turn.harness_manifest_id)
                        if turn.harness_manifest_id is not None
                        else None
                    ),
                    assistant_turns.c.harness_manifest_hash == turn.harness_manifest_hash,
                    assistant_turns.c.idempotency_key == turn.idempotency_key,
                    assistant_turns.c.workflow_run_id
                    == (str(turn.workflow_run_id) if turn.workflow_run_id else None),
                    assistant_turns.c.execution_engine_version == turn.execution_engine_version,
                    assistant_turns.c.status == expected_status.value,
                    assistant_turns.c.cancellation_revision == expected_cancellation_revision,
                )
                .values(**self._turn_mutable_values(turn))
            )
        if result.rowcount != 1:
            raise InvalidTransitionError("Assistant Turn changed concurrently")

    def get_turn(self, turn_id: UUID) -> AssistantTurn | None:
        row = self._first(
            select(assistant_turns).where(
                assistant_turns.c.tenant_id == self._tenant_id,
                assistant_turns.c.id == str(turn_id),
            )
        )
        return self._with_workflow_summary(self._turn_from_row(row)) if row is not None else None

    def find_turn_by_idempotency_key(self, idempotency_key: str) -> AssistantTurn | None:
        row = self._first(
            select(assistant_turns).where(
                assistant_turns.c.tenant_id == self._tenant_id,
                assistant_turns.c.idempotency_key == idempotency_key,
            )
        )
        return self._with_workflow_summary(self._turn_from_row(row)) if row is not None else None

    def list_turns(
        self,
        *,
        statuses: frozenset[AssistantTurnStatus] | None = None,
        updated_since: datetime | None = None,
        limit: int = 200,
    ) -> tuple[AssistantTurn, ...]:
        if limit < 1 or limit > 500:
            raise ValueError("Assistant Turn list limit is invalid")
        predicates = [assistant_turns.c.tenant_id == self._tenant_id]
        if statuses is not None:
            if not statuses:
                return ()
            predicates.append(
                assistant_turns.c.status.in_(tuple(status.value for status in statuses))
            )
        if updated_since is not None:
            predicates.append(assistant_turns.c.updated_at >= updated_since)
        with self._session.read() as connection:
            rows = (
                connection.execute(
                    select(assistant_turns)
                    .where(*predicates)
                    .order_by(assistant_turns.c.updated_at.desc(), assistant_turns.c.id.desc())
                    .limit(limit)
                )
                .mappings()
                .all()
            )
        return tuple(self._with_workflow_summary(self._turn_from_row(row)) for row in rows)

    def nonterminal_turns_for_tasks(
        self,
        task_ids: tuple[UUID, ...],
    ) -> tuple[AssistantTurn, ...]:
        if not task_ids:
            return ()
        with self._session.read() as connection:
            rows = (
                connection.execute(
                    select(assistant_turns)
                    .where(
                        assistant_turns.c.tenant_id == self._tenant_id,
                        assistant_turns.c.task_id.in_(tuple(map(str, task_ids))),
                        assistant_turns.c.status.in_(
                            (
                                AssistantTurnStatus.CREATED.value,
                                AssistantTurnStatus.RUNNING.value,
                                AssistantTurnStatus.WAITING_FOR_TOOL.value,
                                AssistantTurnStatus.WAITING_FOR_INPUT.value,
                            )
                        ),
                    )
                    .order_by(assistant_turns.c.created_at.asc(), assistant_turns.c.id.asc())
                )
                .mappings()
                .all()
            )
        return tuple(self._with_workflow_summary(self._turn_from_row(row)) for row in rows)

    def nonterminal_turn_for_conversation(
        self,
        conversation_id: UUID,
    ) -> AssistantTurn | None:
        row = self._first(
            select(assistant_turns)
            .where(
                assistant_turns.c.tenant_id == self._tenant_id,
                assistant_turns.c.conversation_id == str(conversation_id),
                assistant_turns.c.status.in_(
                    (
                        AssistantTurnStatus.CREATED.value,
                        AssistantTurnStatus.RUNNING.value,
                        AssistantTurnStatus.WAITING_FOR_TOOL.value,
                        AssistantTurnStatus.WAITING_FOR_INPUT.value,
                    )
                ),
            )
            .order_by(assistant_turns.c.created_at, assistant_turns.c.id)
            .limit(1)
        )
        return self._with_workflow_summary(self._turn_from_row(row)) if row is not None else None

    def save_provider_attempt(self, attempt: ProviderAttempt) -> None:
        with self._session.write() as connection:
            connection.execute(
                insert(assistant_provider_attempts).values(
                    tenant_id=self._tenant_id,
                    **provider_attempt_values(attempt),
                )
            )

    def update_provider_attempt(self, attempt: ProviderAttempt) -> None:
        with self._session.write() as connection:
            result = connection.execute(
                update(assistant_provider_attempts)
                .where(
                    assistant_provider_attempts.c.tenant_id == self._tenant_id,
                    assistant_provider_attempts.c.id == str(attempt.id),
                    assistant_provider_attempts.c.status == "started",
                )
                .values(
                    status=attempt.status.value,
                    error_category=(
                        attempt.error_category.value if attempt.error_category is not None else None
                    ),
                    usage=dict(attempt.usage),
                    usage_cost=attempt.usage_cost,
                    completed_at=attempt.completed_at,
                )
            )
        if result.rowcount != 1:
            raise InvalidTransitionError("Provider Attempt changed concurrently")

    def list_provider_attempts(self, turn_id: UUID) -> tuple[ProviderAttempt, ...]:
        with self._session.read() as connection:
            rows = (
                connection.execute(
                    select(assistant_provider_attempts)
                    .where(
                        assistant_provider_attempts.c.tenant_id == self._tenant_id,
                        assistant_provider_attempts.c.turn_id == str(turn_id),
                    )
                    .order_by(
                        assistant_provider_attempts.c.model_round,
                        assistant_provider_attempts.c.attempt_number,
                    )
                )
                .mappings()
                .all()
            )
        return tuple(self._provider_attempt_from_row(row) for row in rows)

    def append_message(self, message: Message) -> None:
        values = {
            "tenant_id": self._tenant_id,
            "id": str(message.id),
            "conversation_id": str(message.conversation_id),
            "task_id": str(message.task_id),
            "turn_id": str(message.turn_id) if message.turn_id else None,
            "sequence": message.sequence,
            "role": message.role.value,
            "visibility": message.visibility.value,
            "content": message.content,
            "created_at": message.created_at,
        }
        with self._session.write() as connection:
            connection.execute(insert(assistant_messages).values(**values))

    def append_imported_message(self, message: ImportedMessage) -> None:
        values = {
            "tenant_id": self._tenant_id,
            "id": str(message.id),
            "conversation_id": str(message.conversation_id),
            "task_id": str(message.task_id),
            "turn_id": str(message.turn_id) if message.turn_id else None,
            "sequence": message.sequence,
            "role": message.role.value,
            "visibility": message.visibility.value,
            "content": message.content,
            "created_at": message.created_at,
            "source_conversation_id": str(message.source_conversation_id),
            "source_message_id": str(message.source_message_id),
            "source_hash": message.source_hash,
            "imported_at": message.imported_at,
        }
        with self._session.write() as connection:
            connection.execute(insert(assistant_imported_messages).values(**values))

    def get_message(self, message_id: UUID) -> Message | None:
        row = self._first(
            select(assistant_messages).where(
                assistant_messages.c.tenant_id == self._tenant_id,
                assistant_messages.c.id == str(message_id),
            )
        )
        return self._message_from_row(row) if row is not None else None

    def message_for_turn(self, turn_id: UUID, role: MessageRole) -> Message | None:
        normalized_role = MessageRole(role)
        with self._session.read() as connection:
            row = (
                connection.execute(
                    select(assistant_messages)
                    .where(
                        assistant_messages.c.tenant_id == self._tenant_id,
                        assistant_messages.c.turn_id == str(turn_id),
                        assistant_messages.c.role == normalized_role.value,
                    )
                    .order_by(assistant_messages.c.sequence, assistant_messages.c.id)
                    .limit(1)
                )
                .mappings()
                .first()
            )
        return self._message_from_row(row) if row is not None else None

    def next_message_sequence(self, conversation_id: UUID) -> int:
        statement = self._insert(assistant_message_sequences).values(
            tenant_id=self._tenant_id,
            conversation_id=str(conversation_id),
            last_sequence=1,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[
                assistant_message_sequences.c.tenant_id,
                assistant_message_sequences.c.conversation_id,
            ],
            set_={
                "last_sequence": assistant_message_sequences.c.last_sequence + 1,
            },
        )
        with self._session.write() as connection:
            value = connection.execute(
                statement.returning(assistant_message_sequences.c.last_sequence)
            ).scalar_one()
        return int(value)

    def list_messages(
        self,
        *,
        conversation_id: UUID,
        limit: int,
        cursor: str | None,
        allowed_visibilities: frozenset[MessageVisibility] | None = None,
    ) -> StatePage[Message]:
        validate_limit(limit)
        after_sequence = _decode_message_cursor(
            cursor,
            conversation_id=conversation_id,
            allowed_visibilities=allowed_visibilities,
        )
        predicates = [
            assistant_messages.c.tenant_id == self._tenant_id,
            assistant_messages.c.conversation_id == str(conversation_id),
            assistant_messages.c.sequence > after_sequence,
        ]
        if allowed_visibilities is not None:
            if not allowed_visibilities:
                return StatePage(items=(), next_cursor=None)
            predicates.append(
                assistant_messages.c.visibility.in_(
                    visibility.value for visibility in allowed_visibilities
                )
            )
        statement = (
            select(assistant_messages)
            .where(*predicates)
            .order_by(assistant_messages.c.sequence, assistant_messages.c.id)
            .limit(limit + 1)
        )
        with self._session.read() as connection:
            rows = list(connection.execute(statement).mappings().all())
        page_rows = rows[:limit]
        next_cursor = None
        if len(rows) > limit:
            next_cursor = _encode_message_cursor(
                conversation_id=conversation_id,
                sequence=int(page_rows[-1]["sequence"]),
                allowed_visibilities=allowed_visibilities,
            )
        return StatePage(
            items=tuple(self._message_from_row(row) for row in page_rows),
            next_cursor=next_cursor,
        )

    def purge_conversation_content(self, conversation_id: UUID) -> None:
        purge_conversation_content(self._session, self._tenant_id, conversation_id)

    def purge_project_content(self, project_id: UUID) -> None:
        purge_project_content(self._session, self._tenant_id, project_id)

    def purge_workspace_content(self, workspace_id: UUID) -> None:
        purge_workspace_content(self._session, self._tenant_id, workspace_id)

    def list_transcript(
        self,
        *,
        conversation_id: UUID,
        limit: int,
        cursor: str | None,
        allowed_visibilities: frozenset[MessageVisibility] | None = None,
    ) -> StatePage[Message | ImportedMessage]:
        validate_limit(limit)
        after_sequence = _decode_message_cursor(
            cursor,
            conversation_id=conversation_id,
            allowed_visibilities=allowed_visibilities,
        )
        native = self._transcript_rows(
            assistant_messages,
            conversation_id=conversation_id,
            after_sequence=after_sequence,
            limit=limit,
            allowed_visibilities=allowed_visibilities,
        )
        imported = self._transcript_rows(
            assistant_imported_messages,
            conversation_id=conversation_id,
            after_sequence=after_sequence,
            limit=limit,
            allowed_visibilities=allowed_visibilities,
        )
        combined = sorted(
            [
                *(self._message_from_row(row) for row in native),
                *(self._imported_message_from_row(row) for row in imported),
            ],
            key=lambda item: (item.sequence, str(item.id)),
        )
        page_items = combined[:limit]
        next_cursor = None
        if len(combined) > limit:
            next_cursor = _encode_message_cursor(
                conversation_id=conversation_id,
                sequence=page_items[-1].sequence,
                allowed_visibilities=allowed_visibilities,
            )
        return StatePage(items=tuple(page_items), next_cursor=next_cursor)

    def _transcript_rows(
        self,
        table: Table,
        *,
        conversation_id: UUID,
        after_sequence: int,
        limit: int,
        allowed_visibilities: frozenset[MessageVisibility] | None,
    ) -> list[RowMapping]:
        predicates = [
            table.c.tenant_id == self._tenant_id,
            table.c.conversation_id == str(conversation_id),
            table.c.sequence > after_sequence,
        ]
        if allowed_visibilities is not None:
            if not allowed_visibilities:
                return []
            predicates.append(table.c.visibility.in_(value.value for value in allowed_visibilities))
        with self._session.read() as connection:
            return list(
                connection.execute(
                    select(table)
                    .where(*predicates)
                    .order_by(table.c.sequence, table.c.id)
                    .limit(limit + 1)
                )
                .mappings()
                .all()
            )

    def save_conversation_move(self, move: ConversationMove) -> None:
        with self._session.write() as connection:
            connection.execute(
                insert(conversation_moves).values(
                    tenant_id=self._tenant_id,
                    idempotency_key=move.idempotency_key,
                    source_conversation_id=str(move.source_conversation_id),
                    destination_conversation_id=str(move.destination_conversation_id),
                    target_project_id=str(move.target_project_id),
                    imported_count=move.imported_count,
                    created_at=move.created_at,
                )
            )

    def find_conversation_move(self, idempotency_key: str) -> ConversationMove | None:
        row = self._first(
            select(conversation_moves).where(
                conversation_moves.c.tenant_id == self._tenant_id,
                conversation_moves.c.idempotency_key == idempotency_key,
            )
        )
        if row is None:
            return None
        return ConversationMove(
            idempotency_key=row["idempotency_key"],
            source_conversation_id=UUID(row["source_conversation_id"]),
            destination_conversation_id=UUID(row["destination_conversation_id"]),
            target_project_id=UUID(row["target_project_id"]),
            imported_count=int(row["imported_count"]),
            created_at=_datetime(row["created_at"]),
        )

    def save_tool_invocation(self, invocation: ToolInvocation) -> None:
        values = {
            "tenant_id": self._tenant_id,
            "id": str(invocation.id),
            "turn_id": str(invocation.turn_id),
            "task_id": str(invocation.task_id),
            "model_round": invocation.model_round,
            "sequence": invocation.sequence,
            "provider_call_id": invocation.provider_call_id,
            "tool_name": invocation.tool_name,
            "scope_digest": invocation.scope_digest,
            "argument_hash": invocation.argument_hash,
            "arguments": invocation.arguments,
            "command_run_id": (
                str(invocation.command_run_id) if invocation.command_run_id else None
            ),
            "status": invocation.status.value,
            "public_summary": invocation.public_summary,
            "model_content": invocation.model_content,
            "artifact_ids": [str(value) for value in invocation.artifact_ids],
            "evidence_receipts": [
                evidence_receipt_record(value) for value in invocation.evidence_receipts
            ],
            "error_code": invocation.error_code,
            "created_at": invocation.created_at,
            "updated_at": invocation.updated_at,
        }
        with self._session.write() as connection:
            connection.execute(insert(assistant_tool_invocations).values(**values))

    def update_tool_invocation(
        self,
        invocation: ToolInvocation,
        *,
        expected_status: ToolInvocationStatus,
    ) -> None:
        with self._session.write() as connection:
            result = connection.execute(
                update(assistant_tool_invocations)
                .where(
                    assistant_tool_invocations.c.tenant_id == self._tenant_id,
                    assistant_tool_invocations.c.id == str(invocation.id),
                    assistant_tool_invocations.c.turn_id == str(invocation.turn_id),
                    assistant_tool_invocations.c.task_id == str(invocation.task_id),
                    assistant_tool_invocations.c.model_round == invocation.model_round,
                    assistant_tool_invocations.c.sequence == invocation.sequence,
                    assistant_tool_invocations.c.provider_call_id == invocation.provider_call_id,
                    assistant_tool_invocations.c.tool_name == invocation.tool_name,
                    assistant_tool_invocations.c.scope_digest == invocation.scope_digest,
                    assistant_tool_invocations.c.argument_hash == invocation.argument_hash,
                    assistant_tool_invocations.c.status == expected_status.value,
                )
                .values(
                    command_run_id=(
                        str(invocation.command_run_id) if invocation.command_run_id else None
                    ),
                    status=invocation.status.value,
                    public_summary=invocation.public_summary,
                    model_content=invocation.model_content,
                    artifact_ids=[str(value) for value in invocation.artifact_ids],
                    evidence_receipts=[
                        evidence_receipt_record(value) for value in invocation.evidence_receipts
                    ],
                    error_code=invocation.error_code,
                    updated_at=invocation.updated_at,
                )
            )
        if result.rowcount != 1:
            raise InvalidTransitionError("Tool Invocation changed concurrently")

    def get_tool_invocation(self, invocation_id: UUID) -> ToolInvocation | None:
        row = self._first(
            select(assistant_tool_invocations).where(
                assistant_tool_invocations.c.tenant_id == self._tenant_id,
                assistant_tool_invocations.c.id == str(invocation_id),
            )
        )
        return self._tool_from_row(row) if row is not None else None

    def find_tool_invocation_by_command_run_id(
        self,
        command_run_id: UUID,
    ) -> ToolInvocation | None:
        row = self._first(
            select(assistant_tool_invocations).where(
                assistant_tool_invocations.c.tenant_id == self._tenant_id,
                assistant_tool_invocations.c.command_run_id == str(command_run_id),
            )
        )
        return self._tool_from_row(row) if row is not None else None

    def list_tool_invocations(self, turn_id: UUID) -> tuple[ToolInvocation, ...]:
        with self._session.read() as connection:
            rows = (
                connection.execute(
                    select(assistant_tool_invocations)
                    .where(
                        assistant_tool_invocations.c.tenant_id == self._tenant_id,
                        assistant_tool_invocations.c.turn_id == str(turn_id),
                    )
                    .order_by(
                        assistant_tool_invocations.c.sequence,
                        assistant_tool_invocations.c.id,
                    )
                )
                .mappings()
                .all()
            )
        return tuple(self._tool_from_row(row) for row in rows)

    def legacy_nonterminal_turn_ids(self) -> tuple[UUID, ...]:
        with self._session.read() as connection:
            rows = connection.execute(
                select(assistant_turns.c.id)
                .where(
                    assistant_turns.c.tenant_id == self._tenant_id,
                    assistant_turns.c.status.not_in(
                        (
                            AssistantTurnStatus.COMPLETED.value,
                            AssistantTurnStatus.CANCELLED.value,
                            AssistantTurnStatus.FAILED.value,
                        )
                    ),
                    or_(
                        assistant_turns.c.workflow_run_id.is_(None),
                        assistant_turns.c.execution_engine_version < 2,
                    ),
                )
                .order_by(assistant_turns.c.created_at, assistant_turns.c.id)
            ).all()
        return tuple(UUID(str(row[0])) for row in rows)

    def resumable_waiting_turn_ids(self) -> tuple[UUID, ...]:
        now = datetime.now(UTC)
        resumable_command = or_(
            command_runs.c.status.in_((CommandStatus.QUEUED.value, CommandStatus.REJECTED.value)),
            and_(
                command_runs.c.status == CommandStatus.RUNNING.value,
                or_(
                    command_runs.c.lease_until.is_(None),
                    command_runs.c.lease_until <= now,
                ),
            ),
        )
        with self._session.read() as connection:
            tool_rows = connection.execute(
                select(assistant_tool_invocations.c.turn_id)
                .join(
                    assistant_turns,
                    and_(
                        assistant_turns.c.tenant_id == assistant_tool_invocations.c.tenant_id,
                        assistant_turns.c.id == assistant_tool_invocations.c.turn_id,
                    ),
                )
                .join(
                    command_runs,
                    and_(
                        command_runs.c.tenant_id == assistant_tool_invocations.c.tenant_id,
                        command_runs.c.id == assistant_tool_invocations.c.command_run_id,
                    ),
                )
                .where(
                    assistant_tool_invocations.c.tenant_id == self._tenant_id,
                    assistant_turns.c.status == AssistantTurnStatus.WAITING_FOR_TOOL.value,
                    assistant_tool_invocations.c.status.in_(
                        (
                            ToolInvocationStatus.QUEUED.value,
                            ToolInvocationStatus.RUNNING.value,
                        )
                    ),
                    resumable_command,
                )
            ).all()
            budget_rows = connection.execute(
                select(assistant_turns.c.id)
                .join(
                    command_runs,
                    and_(
                        command_runs.c.tenant_id == assistant_turns.c.tenant_id,
                        command_runs.c.id == assistant_turns.c.budget_approval_run_id,
                    ),
                )
                .where(
                    assistant_turns.c.tenant_id == self._tenant_id,
                    assistant_turns.c.status == AssistantTurnStatus.WAITING_FOR_TOOL.value,
                    assistant_turns.c.budget_approval_run_id.is_not(None),
                    resumable_command,
                )
            ).all()
        turn_ids = {UUID(str(row[0])) for row in (*tool_rows, *budget_rows)}
        return tuple(sorted(turn_ids, key=str))

    def resumable_workflow_turn_ids(self) -> tuple[UUID, ...]:
        with self._session.read() as connection:
            rows = connection.execute(
                select(assistant_turns.c.id)
                .join(
                    workflow_runs,
                    and_(
                        workflow_runs.c.tenant_id == assistant_turns.c.tenant_id,
                        workflow_runs.c.id == assistant_turns.c.workflow_run_id,
                    ),
                )
                .where(
                    assistant_turns.c.tenant_id == self._tenant_id,
                    assistant_turns.c.status == AssistantTurnStatus.RUNNING.value,
                    assistant_turns.c.workflow_run_id.is_not(None),
                    workflow_runs.c.status == WorkflowRunStatus.PAUSED.value,
                )
                .order_by(assistant_turns.c.created_at, assistant_turns.c.id)
            ).all()
        return tuple(UUID(str(row[0])) for row in rows)

    def _first(self, statement: Any) -> RowMapping | None:
        with self._session.read() as connection:
            return connection.execute(statement).mappings().first()

    def _insert(self, table: Table):
        return (
            postgresql_insert(table)
            if self._session.dialect_name == "postgresql"
            else sqlite_insert(table)
        )

    @staticmethod
    def _turn_values(turn: AssistantTurn) -> dict[str, object]:
        return {
            "id": str(turn.id),
            "conversation_id": str(turn.conversation_id),
            "task_id": str(turn.task_id),
            "profile_id": turn.profile_id,
            "scope_digest": turn.scope_digest,
            "memory_snapshot_id": str(turn.memory_snapshot_id),
            "memory_snapshot_hash": turn.memory_snapshot_hash,
            "knowledge_snapshot_id": (
                str(turn.knowledge_snapshot_id) if turn.knowledge_snapshot_id else None
            ),
            "knowledge_snapshot_hash": turn.knowledge_snapshot_hash,
            "harness_manifest_id": (
                str(turn.harness_manifest_id) if turn.harness_manifest_id else None
            ),
            "harness_manifest_hash": turn.harness_manifest_hash,
            "idempotency_key": turn.idempotency_key,
            "model_selection": _model_selection_record(turn.model_selection),
            "routing_decision": (
                routing_decision_record(turn.routing_decision)
                if turn.routing_decision is not None
                else None
            ),
            "budget_approval_run_id": (
                str(turn.budget_approval_run_id) if turn.budget_approval_run_id else None
            ),
            "cited_evidence_receipt_ids": [str(value) for value in turn.cited_evidence_receipt_ids],
            "workflow_run_id": str(turn.workflow_run_id) if turn.workflow_run_id else None,
            "execution_engine_version": turn.execution_engine_version,
            "active_interpretation_revision": turn.active_interpretation_revision,
            "status": turn.status.value,
            "cancellation_revision": turn.cancellation_revision,
            "usage": dict(turn.usage),
            "error_code": turn.error_code,
            "created_at": turn.created_at,
            "updated_at": turn.updated_at,
            "started_at": turn.started_at,
            "completed_at": turn.completed_at,
        }

    @staticmethod
    def _turn_mutable_values(turn: AssistantTurn) -> dict[str, object]:
        return {
            "status": turn.status.value,
            "cancellation_revision": turn.cancellation_revision,
            "usage": dict(turn.usage),
            "error_code": turn.error_code,
            "routing_decision": (
                routing_decision_record(turn.routing_decision)
                if turn.routing_decision is not None
                else None
            ),
            "active_interpretation_revision": turn.active_interpretation_revision,
            "budget_approval_run_id": (
                str(turn.budget_approval_run_id) if turn.budget_approval_run_id else None
            ),
            "cited_evidence_receipt_ids": [str(value) for value in turn.cited_evidence_receipt_ids],
            "updated_at": turn.updated_at,
            "started_at": turn.started_at,
            "completed_at": turn.completed_at,
        }

    @staticmethod
    def _turn_from_row(row: Mapping[str, Any]) -> AssistantTurn:
        return AssistantTurn(
            id=UUID(row["id"]),
            conversation_id=UUID(row["conversation_id"]),
            task_id=UUID(row["task_id"]),
            profile_id=row["profile_id"],
            scope_digest=row["scope_digest"],
            memory_snapshot_id=UUID(row["memory_snapshot_id"]),
            memory_snapshot_hash=row["memory_snapshot_hash"],
            knowledge_snapshot_id=(
                UUID(row["knowledge_snapshot_id"])
                if row.get("knowledge_snapshot_id") is not None
                else None
            ),
            knowledge_snapshot_hash=row.get("knowledge_snapshot_hash"),
            harness_manifest_id=(
                UUID(row["harness_manifest_id"])
                if row.get("harness_manifest_id") is not None
                else None
            ),
            harness_manifest_hash=row.get("harness_manifest_hash"),
            idempotency_key=row["idempotency_key"],
            model_selection=_model_selection_from_record(row.get("model_selection")),
            routing_decision=routing_decision_from_record(row.get("routing_decision")),
            budget_approval_run_id=(
                UUID(row["budget_approval_run_id"])
                if row.get("budget_approval_run_id") is not None
                else None
            ),
            cited_evidence_receipt_ids=tuple(
                UUID(str(value)) for value in row.get("cited_evidence_receipt_ids", ())
            ),
            workflow_run_id=(
                UUID(row["workflow_run_id"]) if row.get("workflow_run_id") is not None else None
            ),
            execution_engine_version=int(row.get("execution_engine_version", 1)),
            active_interpretation_revision=(
                int(row["active_interpretation_revision"])
                if row.get("active_interpretation_revision") is not None
                else None
            ),
            status=AssistantTurnStatus(row["status"]),
            cancellation_revision=int(row["cancellation_revision"]),
            usage={name: int(value) for name, value in row["usage"].items()},
            error_code=row["error_code"],
            created_at=_datetime(row["created_at"]),
            updated_at=_datetime(row["updated_at"]),
            started_at=_optional_datetime(row["started_at"]),
            completed_at=_optional_datetime(row["completed_at"]),
        )

    def _with_workflow_summary(self, turn: AssistantTurn) -> AssistantTurn:
        projected = attach_workflow_summary(
            self._session,
            tenant_id=self._tenant_id,
            turn=turn,
        )
        if projected.active_interpretation_revision is not None:
            projected.interpretation_summary = self.get_interpretation(
                projected.id,
                projected.active_interpretation_revision,
            )
        return projected

    @staticmethod
    def _provider_attempt_from_row(row: Mapping[str, Any]) -> ProviderAttempt:
        return ProviderAttempt(
            id=UUID(row["id"]),
            turn_id=UUID(row["turn_id"]),
            task_id=UUID(row["task_id"]),
            model_round=int(row["model_round"]),
            attempt_number=int(row["attempt_number"]),
            profile_id=row["profile_id"],
            model_id=row["model_id"],
            endpoint_kind=ModelEndpointKind(row["endpoint_kind"]),
            model_role=ModelExecutionRole(row["model_role"]),
            status=ProviderAttemptStatus(row["status"]),
            error_category=(
                ProviderErrorCategory(row["error_category"])
                if row["error_category"] is not None
                else None
            ),
            usage={name: int(value) for name, value in row["usage"].items()},
            usage_cost=row["usage_cost"],
            created_at=_datetime(row["created_at"]),
            completed_at=_optional_datetime(row["completed_at"]),
        )

    @staticmethod
    def _message_from_row(row: Mapping[str, Any]) -> Message:
        return Message(
            id=UUID(row["id"]),
            conversation_id=UUID(row["conversation_id"]),
            task_id=UUID(row["task_id"]),
            turn_id=UUID(row["turn_id"]) if row["turn_id"] else None,
            sequence=int(row["sequence"]),
            role=MessageRole(row["role"]),
            visibility=MessageVisibility(row["visibility"]),
            content=row["content"],
            created_at=_datetime(row["created_at"]),
        )

    @staticmethod
    def _imported_message_from_row(row: Mapping[str, Any]) -> ImportedMessage:
        return ImportedMessage(
            id=UUID(row["id"]),
            conversation_id=UUID(row["conversation_id"]),
            task_id=UUID(row["task_id"]),
            turn_id=UUID(row["turn_id"]) if row["turn_id"] else None,
            sequence=int(row["sequence"]),
            role=MessageRole(row["role"]),
            visibility=MessageVisibility(row["visibility"]),
            content=row["content"],
            created_at=_datetime(row["created_at"]),
            source_conversation_id=UUID(row["source_conversation_id"]),
            source_message_id=UUID(row["source_message_id"]),
            source_hash=row["source_hash"],
            imported_at=_datetime(row["imported_at"]),
        )

    @staticmethod
    def _tool_from_row(row: Mapping[str, Any]) -> ToolInvocation:
        return ToolInvocation(
            id=UUID(row["id"]),
            turn_id=UUID(row["turn_id"]),
            task_id=UUID(row["task_id"]),
            model_round=int(row["model_round"]),
            sequence=int(row["sequence"]),
            provider_call_id=row["provider_call_id"],
            tool_name=row["tool_name"],
            scope_digest=row["scope_digest"],
            argument_hash=row["argument_hash"],
            arguments=dict(row["arguments"]),
            command_run_id=UUID(row["command_run_id"]) if row["command_run_id"] else None,
            status=ToolInvocationStatus(row["status"]),
            public_summary=row["public_summary"],
            model_content=row["model_content"],
            artifact_ids=tuple(UUID(value) for value in row["artifact_ids"]),
            evidence_receipts=tuple(
                evidence_receipt_from_record(value) for value in row.get("evidence_receipts", ())
            ),
            error_code=row["error_code"],
            created_at=_datetime(row["created_at"]),
            updated_at=_datetime(row["updated_at"]),
        )


def _encode_message_cursor(
    *,
    conversation_id: UUID,
    sequence: int,
    allowed_visibilities: frozenset[MessageVisibility] | None,
) -> str:
    scope = _message_cursor_scope(conversation_id, allowed_visibilities)
    payload = json.dumps(
        {"v": 1, "c": str(conversation_id), "q": sequence, "s": scope},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _decode_message_cursor(
    cursor: str | None,
    *,
    conversation_id: UUID,
    allowed_visibilities: frozenset[MessageVisibility] | None,
) -> int:
    if cursor is None:
        return 0
    try:
        if not cursor or len(cursor) > 2048:
            raise ValueError
        decoded = base64.b64decode(
            cursor + "=" * (-len(cursor) % 4),
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(decoded)
        expected_scope = _message_cursor_scope(conversation_id, allowed_visibilities)
        if (
            not isinstance(payload, dict)
            or set(payload) != {"v", "c", "q", "s"}
            or payload["v"] != 1
            or payload["c"] != str(conversation_id)
            or not isinstance(payload["q"], int)
            or isinstance(payload["q"], bool)
            or payload["q"] < 1
            or not isinstance(payload["s"], str)
            or not hmac.compare_digest(payload["s"], expected_scope)
        ):
            raise ValueError
    except (
        binascii.Error,
        json.JSONDecodeError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
    ) as error:
        raise ValueError("cursor is invalid for this Conversation") from error
    return payload["q"]


def _message_cursor_scope(
    conversation_id: UUID,
    allowed_visibilities: frozenset[MessageVisibility] | None,
) -> str:
    visibility_scope = (
        "all"
        if allowed_visibilities is None
        else ",".join(sorted(value.value for value in allowed_visibilities))
    )
    value = f"assistant-messages:{conversation_id}:{visibility_scope}"
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _model_selection_record(
    selection: ModelSelectionSnapshot | None,
) -> dict[str, object] | None:
    if selection is None:
        return None
    return {
        "mode": selection.mode.value,
        "model_id": selection.model_id,
        "allow_free_fallback": selection.allow_free_fallback,
        "zero_data_retention": selection.zero_data_retention,
        "revision": selection.revision,
        "captured_at": selection.captured_at.isoformat(),
    }


def _model_selection_from_record(record: object) -> ModelSelectionSnapshot | None:
    if record is None:
        return None
    if not isinstance(record, dict):
        raise ValueError("stored model selection snapshot is invalid")
    return ModelSelectionSnapshot(
        mode=ModelSelectionMode(record["mode"]),
        model_id=str(record["model_id"]) if record.get("model_id") is not None else None,
        allow_free_fallback=bool(record["allow_free_fallback"]),
        zero_data_retention=bool(record["zero_data_retention"]),
        revision=int(record["revision"]),
        captured_at=_datetime(str(record["captured_at"])),
    )


def _datetime(value: datetime | str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _optional_datetime(value: datetime | str | None) -> datetime | None:
    return _datetime(value) if value is not None else None


__all__ = ["SqlAlchemyAssistantRepository"]
