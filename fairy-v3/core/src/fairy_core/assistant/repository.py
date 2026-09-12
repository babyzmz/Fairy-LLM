from __future__ import annotations

import hmac
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Table, and_, insert, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, Engine, RowMapping

from fairy_core.assistant.content_purge_repository import (
    purge_conversation_content,
    purge_project_content,
    purge_workspace_content,
)
from fairy_core.assistant.evidence import (
    evidence_receipt_record,
)
from fairy_core.assistant.interpretation_repository import (
    AssistantInterpretationRepositoryMixin,
    _interpretation_from_row,
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
from fairy_core.assistant.record_codecs import (
    AssistantRecordCodecMixin,
    _datetime,
    _decode_message_cursor,
    _encode_message_cursor,
)
from fairy_core.assistant.repository_records import provider_attempt_values
from fairy_core.assistant.trace_repository import TurnTraceRepositoryMixin
from fairy_core.assistant.workflow_projection import (
    attach_workflow_summaries,
    attach_workflow_summary,
)
from fairy_core.commanding.models import CommandStatus
from fairy_core.commanding.schema import command_runs
from fairy_core.domain.errors import IdempotencyConflictError, InvalidTransitionError
from fairy_core.persistence.session import SqlAlchemySession
from fairy_core.persistence.tenant import normalize_tenant_id
from fairy_core.storage.pagination import StatePage, validate_limit
from fairy_core.storage.schema import (
    assistant_imported_messages,
    assistant_message_cancellations,
    assistant_message_sequences,
    assistant_message_submissions,
    assistant_messages,
    assistant_provider_attempts,
    assistant_request_interpretations,
    assistant_tool_invocations,
    assistant_turns,
    changesets,
    conversation_moves,
    tasks,
    workflow_runs,
)
from fairy_core.workflow.models import WorkflowRunStatus


class SqlAlchemyAssistantRepository(
    AssistantRecordCodecMixin,
    AssistantInterpretationRepositoryMixin,
    TurnTraceRepositoryMixin,
):
    def __init__(self, bind: Engine | Connection, *, tenant_id: str) -> None:
        if bind.dialect.name not in {"postgresql", "sqlite"}:
            raise ValueError(f"unsupported assistant repository dialect: {bind.dialect.name}")
        self._tenant_id = normalize_tenant_id(tenant_id)
        self._session = SqlAlchemySession(bind)

    def message_submission_digest(self, key_digest: str) -> str | None:
        row = self._first(
            select(assistant_message_submissions.c.request_digest).where(
                assistant_message_submissions.c.tenant_id == self._tenant_id,
                assistant_message_submissions.c.key_digest == key_digest,
            )
        )
        return row["request_digest"] if row is not None else None

    def reserve_message_submission(
        self,
        *,
        key_digest: str,
        request_digest: str,
        conversation_id: UUID,
    ) -> None:
        statement = (
            self._insert(assistant_message_submissions)
            .values(
                tenant_id=self._tenant_id,
                key_digest=key_digest,
                request_digest=request_digest,
                conversation_id=str(conversation_id),
                created_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "key_digest"])
        )
        with self._session.write() as connection:
            connection.execute(statement)
            actual = connection.execute(
                select(
                    assistant_message_submissions.c.request_digest,
                ).where(
                    assistant_message_submissions.c.tenant_id == self._tenant_id,
                    assistant_message_submissions.c.key_digest == key_digest,
                )
            ).scalar_one()
            if not hmac.compare_digest(actual, request_digest):
                raise IdempotencyConflictError("Message idempotency key has a different request")

    def message_submission_conversation(self, key_digest: str) -> UUID | None:
        row = self._first(
            select(assistant_message_submissions.c.conversation_id).where(
                assistant_message_submissions.c.tenant_id == self._tenant_id,
                assistant_message_submissions.c.key_digest == key_digest,
            )
        )
        return UUID(row["conversation_id"]) if row is not None else None

    def latest_turn_presentation(self, conversation_id: UUID) -> dict[str, Any] | None:
        columns = assistant_turns.c
        row = self._first(
            select(
                columns.id,
                columns.conversation_id,
                columns.status,
                columns.cancellation_revision,
                columns.error_code,
                columns.updated_at,
                and_(
                    columns.status == AssistantTurnStatus.CANCELLED.value,
                    self._pending_operations(columns.id),
                ).label("cancellation_pending"),
            )
            .where(
                columns.tenant_id == self._tenant_id,
                columns.conversation_id == str(conversation_id),
            )
            .order_by(columns.created_at.desc(), columns.id.desc())
            .limit(1)
        )
        return dict(row) if row is not None else None

    def message_cancellation_requested(
        self,
        key_digest: str,
        conversation_id: UUID | None,
    ) -> bool:
        row = self._first(
            select(assistant_message_cancellations.c.conversation_id).where(
                assistant_message_cancellations.c.tenant_id == self._tenant_id,
                assistant_message_cancellations.c.key_digest == key_digest,
            )
        )
        if row is None:
            return False
        if (
            row["conversation_id"] is not None
            and conversation_id is not None
            and row["conversation_id"] != str(conversation_id)
        ):
            raise IdempotencyConflictError(
                "Message cancellation belongs to a different conversation"
            )
        return True

    def request_message_cancellation(
        self,
        key_digest: str,
        conversation_id: UUID | None,
    ) -> None:
        statement = (
            self._insert(assistant_message_cancellations)
            .values(
                tenant_id=self._tenant_id,
                key_digest=key_digest,
                conversation_id=str(conversation_id) if conversation_id is not None else None,
                created_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "key_digest"])
        )
        with self._session.write() as connection:
            connection.execute(statement)
        self.message_cancellation_requested(key_digest, conversation_id)

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
        return self._with_workflow_summaries(tuple(self._turn_from_row(row) for row in rows))

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
                        or_(
                            assistant_turns.c.status.in_(
                                (
                                    AssistantTurnStatus.CREATED.value,
                                    AssistantTurnStatus.RUNNING.value,
                                    AssistantTurnStatus.WAITING_FOR_TOOL.value,
                                    AssistantTurnStatus.WAITING_FOR_INPUT.value,
                                )
                            ),
                            and_(
                                assistant_turns.c.status == AssistantTurnStatus.CANCELLED.value,
                                self._pending_operations(assistant_turns.c.id),
                            ),
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
                or_(
                    assistant_turns.c.status.in_(
                        (
                            AssistantTurnStatus.CREATED.value,
                            AssistantTurnStatus.RUNNING.value,
                            AssistantTurnStatus.WAITING_FOR_TOOL.value,
                            AssistantTurnStatus.WAITING_FOR_INPUT.value,
                        )
                    ),
                    and_(
                        assistant_turns.c.status == AssistantTurnStatus.CANCELLED.value,
                        self._pending_operations(assistant_turns.c.id),
                    ),
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

    def cancelled_provider_attempts(self, *, limit: int = 100) -> tuple[ProviderAttempt, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("cancelled Provider Attempt batch must be between 1 and 100")
        with self._session.read() as connection:
            rows = (
                connection.execute(
                    select(assistant_provider_attempts)
                    .where(
                        assistant_provider_attempts.c.tenant_id == self._tenant_id,
                        assistant_provider_attempts.c.status == "started",
                        assistant_provider_attempts.c.turn_id.in_(
                            select(assistant_turns.c.id).where(
                                assistant_turns.c.tenant_id == self._tenant_id,
                                assistant_turns.c.status == AssistantTurnStatus.CANCELLED.value,
                                assistant_turns.c.task_id.in_(
                                    select(tasks.c.id).where(
                                        tasks.c.tenant_id == self._tenant_id,
                                        tasks.c.execution_target == "local",
                                    )
                                ),
                            )
                        ),
                    )
                    .order_by(assistant_provider_attempts.c.id)
                    .limit(limit)
                )
                .mappings()
                .all()
            )
        return tuple(self._provider_attempt_from_row(row) for row in rows)

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

    def recent_transcript(
        self,
        *,
        conversation_id: UUID,
        limit: int,
        allowed_visibilities: frozenset[MessageVisibility] | None = None,
    ) -> tuple[Message | ImportedMessage, ...]:
        validate_limit(limit)
        combined: list[Message | ImportedMessage] = []
        for table, decode in (
            (assistant_messages, self._message_from_row),
            (assistant_imported_messages, self._imported_message_from_row),
        ):
            combined.extend(
                decode(row)
                for row in self._transcript_rows(
                    table,
                    conversation_id=conversation_id,
                    after_sequence=0,
                    limit=limit,
                    allowed_visibilities=allowed_visibilities,
                    newest_first=True,
                )
            )
        combined.sort(key=lambda item: (item.sequence, str(item.id)))
        return tuple(combined[-limit:])

    def _transcript_rows(
        self,
        table: Table,
        *,
        conversation_id: UUID,
        after_sequence: int,
        limit: int,
        allowed_visibilities: frozenset[MessageVisibility] | None,
        newest_first: bool = False,
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
                    .order_by(
                        table.c.sequence.desc() if newest_first else table.c.sequence,
                        table.c.id.desc() if newest_first else table.c.id,
                    )
                    .limit(limit if newest_first else limit + 1)
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
            "workflow_run_id": (
                str(invocation.workflow_run_id) if invocation.workflow_run_id else None
            ),
            "workflow_plan_revision": invocation.workflow_plan_revision,
            "workflow_objective_index": invocation.workflow_objective_index,
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
                    assistant_tool_invocations.c.workflow_run_id
                    == (str(invocation.workflow_run_id) if invocation.workflow_run_id else None),
                    assistant_tool_invocations.c.workflow_plan_revision
                    == invocation.workflow_plan_revision,
                    assistant_tool_invocations.c.workflow_objective_index
                    == invocation.workflow_objective_index,
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
            failed_apply_rows = connection.execute(
                select(assistant_turns.c.id)
                .join(
                    tasks,
                    and_(
                        tasks.c.tenant_id == assistant_turns.c.tenant_id,
                        tasks.c.id == assistant_turns.c.task_id,
                        tasks.c.conversation_id == assistant_turns.c.conversation_id,
                    ),
                )
                .join(
                    workflow_runs,
                    and_(
                        workflow_runs.c.tenant_id == assistant_turns.c.tenant_id,
                        workflow_runs.c.id == assistant_turns.c.workflow_run_id,
                        workflow_runs.c.owner_id == assistant_turns.c.id,
                        workflow_runs.c.owner_kind == "assistant_turn",
                        workflow_runs.c.engine_version
                        == assistant_turns.c.execution_engine_version,
                    ),
                )
                .where(
                    assistant_turns.c.tenant_id == self._tenant_id,
                    assistant_turns.c.status == "waiting_for_tool",
                    tasks.c.status == "failed",
                    workflow_runs.c.status.in_(("waiting_for_approval", "paused")),
                    select(changesets.c.id)
                    .where(
                        changesets.c.tenant_id == assistant_turns.c.tenant_id,
                        changesets.c.task_id == assistant_turns.c.task_id,
                        changesets.c.conversation_id == assistant_turns.c.conversation_id,
                        changesets.c.status == "failed",
                    )
                    .exists(),
                ),
            ).all()
        turn_ids = {UUID(str(row[0])) for row in (*tool_rows, *budget_rows, *failed_apply_rows)}
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

    def unsettled_failed_workflow_ids(self, *, limit: int = 64) -> tuple[UUID, ...]:
        validate_limit(limit)
        with self._session.read() as connection:
            rows = connection.execute(
                select(workflow_runs.c.id)
                .join(
                    assistant_turns,
                    and_(
                        assistant_turns.c.tenant_id == workflow_runs.c.tenant_id,
                        assistant_turns.c.workflow_run_id == workflow_runs.c.id,
                        assistant_turns.c.id == workflow_runs.c.owner_id,
                        assistant_turns.c.execution_engine_version
                        == workflow_runs.c.engine_version,
                    ),
                )
                .where(
                    workflow_runs.c.tenant_id == self._tenant_id,
                    workflow_runs.c.owner_kind == "assistant_turn",
                    workflow_runs.c.engine_version.in_((2, 3, 4)),
                    workflow_runs.c.status == WorkflowRunStatus.FAILED.value,
                    assistant_turns.c.status.not_in(("completed", "cancelled", "failed")),
                )
                .order_by(workflow_runs.c.updated_at, workflow_runs.c.id)
                .limit(limit)
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

    def _with_workflow_summary(self, turn: AssistantTurn) -> AssistantTurn:
        if turn.status is AssistantTurnStatus.CANCELLED:
            turn.cancellation_pending = (
                self._first(
                    select(assistant_turns.c.id)
                    .where(
                        assistant_turns.c.tenant_id == self._tenant_id,
                        assistant_turns.c.id == str(turn.id),
                        self._pending_operations(assistant_turns.c.id),
                    )
                    .limit(1)
                )
                is not None
            )
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

    def _with_workflow_summaries(
        self,
        turns: tuple[AssistantTurn, ...],
    ) -> tuple[AssistantTurn, ...]:
        projected = attach_workflow_summaries(self._session, tenant_id=self._tenant_id, turns=turns)
        cancelled = {str(turn.id) for turn in turns if turn.status is AssistantTurnStatus.CANCELLED}
        interpreted = {
            str(turn.id): turn.active_interpretation_revision
            for turn in turns
            if turn.active_interpretation_revision is not None
        }
        with self._session.read() as connection:
            pending = (
                set(
                    connection.execute(
                        select(assistant_turns.c.id).where(
                            assistant_turns.c.tenant_id == self._tenant_id,
                            assistant_turns.c.id.in_(cancelled),
                            self._pending_operations(assistant_turns.c.id),
                        )
                    ).scalars()
                )
                if cancelled
                else set()
            )
            summaries = {}
            if interpreted:
                for row in connection.execute(
                    select(assistant_request_interpretations).where(
                        assistant_request_interpretations.c.tenant_id == self._tenant_id,
                        tuple_(
                            assistant_request_interpretations.c.turn_id,
                            assistant_request_interpretations.c.revision,
                        ).in_(
                            tuple(interpreted.items()),
                        ),
                    )
                ).mappings():
                    if row["revision"] == interpreted[str(row["turn_id"])]:
                        summaries[str(row["turn_id"])] = _interpretation_from_row(row)
        for turn in projected:
            turn.cancellation_pending = str(turn.id) in pending
            turn.interpretation_summary = summaries.get(str(turn.id))
        return projected

    def _pending_operations(self, turn_id):
        return or_(
            select(assistant_tool_invocations.c.id)
            .where(
                assistant_tool_invocations.c.tenant_id == self._tenant_id,
                assistant_tool_invocations.c.turn_id == turn_id,
                assistant_tool_invocations.c.status == ToolInvocationStatus.RUNNING.value,
            )
            .exists(),
            select(assistant_provider_attempts.c.id)
            .where(
                assistant_provider_attempts.c.tenant_id == self._tenant_id,
                assistant_provider_attempts.c.turn_id == turn_id,
                assistant_provider_attempts.c.status == "started",
            )
            .exists(),
        )


__all__ = ["SqlAlchemyAssistantRepository"]
