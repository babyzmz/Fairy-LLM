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

from sqlalchemy import Table, insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, Engine, RowMapping

from fairy_core.assistant.models import (
    AssistantTurn,
    AssistantTurnStatus,
    Message,
    MessageRole,
    MessageVisibility,
    ToolInvocation,
    ToolInvocationStatus,
)
from fairy_core.commanding.models import CommandStatus
from fairy_core.commanding.schema import command_runs
from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.persistence.session import SqlAlchemySession
from fairy_core.persistence.tenant import normalize_tenant_id
from fairy_core.storage.pagination import StatePage, validate_limit
from fairy_core.storage.schema import (
    assistant_message_sequences,
    assistant_messages,
    assistant_tool_invocations,
    assistant_turns,
)

_ACTIVE_TURN_STATUSES = (
    AssistantTurnStatus.RUNNING.value,
    AssistantTurnStatus.WAITING_FOR_TOOL.value,
)


class SqlAlchemyAssistantRepository:
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
        return self._turn_from_row(row), inserted_id is not None

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
                    assistant_turns.c.idempotency_key == turn.idempotency_key,
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
        return self._turn_from_row(row) if row is not None else None

    def find_turn_by_idempotency_key(self, idempotency_key: str) -> AssistantTurn | None:
        row = self._first(
            select(assistant_turns).where(
                assistant_turns.c.tenant_id == self._tenant_id,
                assistant_turns.c.idempotency_key == idempotency_key,
            )
        )
        return self._turn_from_row(row) if row is not None else None

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

    def get_message(self, message_id: UUID) -> Message | None:
        row = self._first(
            select(assistant_messages).where(
                assistant_messages.c.tenant_id == self._tenant_id,
                assistant_messages.c.id == str(message_id),
            )
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

    def save_tool_invocation(self, invocation: ToolInvocation) -> None:
        values = {
            "tenant_id": self._tenant_id,
            "id": str(invocation.id),
            "turn_id": str(invocation.turn_id),
            "task_id": str(invocation.task_id),
            "sequence": invocation.sequence,
            "tool_name": invocation.tool_name,
            "scope_digest": invocation.scope_digest,
            "argument_hash": invocation.argument_hash,
            "arguments": invocation.arguments,
            "command_run_id": (
                str(invocation.command_run_id) if invocation.command_run_id else None
            ),
            "status": invocation.status.value,
            "public_summary": invocation.public_summary,
            "artifact_ids": [str(value) for value in invocation.artifact_ids],
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
                    assistant_tool_invocations.c.sequence == invocation.sequence,
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
                    artifact_ids=[str(value) for value in invocation.artifact_ids],
                    error_code=invocation.error_code,
                    updated_at=invocation.updated_at,
                )
            )
        if result.rowcount != 1:
            raise InvalidTransitionError("Tool Invocation changed concurrently")

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

    def live_turn_ids(self) -> tuple[UUID, ...]:
        now = datetime.now(UTC)
        with self._session.read() as connection:
            runs = (
                connection.execute(
                    select(
                        command_runs.c.id,
                        command_runs.c.command_name,
                        command_runs.c.input,
                    ).where(
                        command_runs.c.tenant_id == self._tenant_id,
                        command_runs.c.status == CommandStatus.RUNNING.value,
                        command_runs.c.lease_until.is_not(None),
                        command_runs.c.lease_until > now,
                    )
                )
                .mappings()
                .all()
            )
            invocation_rows = (
                connection.execute(
                    select(
                        assistant_tool_invocations.c.command_run_id,
                        assistant_tool_invocations.c.turn_id,
                    ).where(
                        assistant_tool_invocations.c.tenant_id == self._tenant_id,
                        assistant_tool_invocations.c.command_run_id.is_not(None),
                        assistant_tool_invocations.c.status == ToolInvocationStatus.RUNNING.value,
                    )
                )
                .mappings()
                .all()
            )
        invocation_turns = {
            str(row["command_run_id"]): UUID(str(row["turn_id"])) for row in invocation_rows
        }
        live: set[UUID] = set()
        for row in runs:
            linked_turn = invocation_turns.get(str(row["id"]))
            if linked_turn is not None:
                live.add(linked_turn)
                continue
            if row["command_name"] != "model.generate":
                continue
            payload = row["input"]
            turn_id = payload.get("turn_id") if isinstance(payload, Mapping) else None
            try:
                live.add(UUID(str(turn_id)))
            except (TypeError, ValueError):
                continue
        return tuple(sorted(live, key=str))

    def interrupt_orphaned_turns(
        self,
        *,
        live_turn_ids: tuple[UUID, ...],
    ) -> tuple[AssistantTurn, ...]:
        statement = select(assistant_turns).where(
            assistant_turns.c.tenant_id == self._tenant_id,
            assistant_turns.c.status.in_(_ACTIVE_TURN_STATUSES),
        )
        if live_turn_ids:
            statement = statement.where(
                assistant_turns.c.id.not_in(tuple(str(value) for value in live_turn_ids))
            )
        statement = statement.order_by(assistant_turns.c.created_at, assistant_turns.c.id)
        with self._session.read() as connection:
            rows = connection.execute(statement).mappings().all()
        interrupted = tuple(self._turn_from_row(row) for row in rows)
        for turn in interrupted:
            expected_status = turn.status
            expected_revision = turn.cancellation_revision
            turn.interrupt()
            self.update_turn(
                turn,
                expected_status=expected_status,
                expected_cancellation_revision=expected_revision,
            )
        return interrupted

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
            "idempotency_key": turn.idempotency_key,
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
            idempotency_key=row["idempotency_key"],
            status=AssistantTurnStatus(row["status"]),
            cancellation_revision=int(row["cancellation_revision"]),
            usage={name: int(value) for name, value in row["usage"].items()},
            error_code=row["error_code"],
            created_at=_datetime(row["created_at"]),
            updated_at=_datetime(row["updated_at"]),
            started_at=_optional_datetime(row["started_at"]),
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
    def _tool_from_row(row: Mapping[str, Any]) -> ToolInvocation:
        return ToolInvocation(
            id=UUID(row["id"]),
            turn_id=UUID(row["turn_id"]),
            task_id=UUID(row["task_id"]),
            sequence=int(row["sequence"]),
            tool_name=row["tool_name"],
            scope_digest=row["scope_digest"],
            argument_hash=row["argument_hash"],
            arguments=dict(row["arguments"]),
            command_run_id=UUID(row["command_run_id"]) if row["command_run_id"] else None,
            status=ToolInvocationStatus(row["status"]),
            public_summary=row["public_summary"],
            artifact_ids=tuple(UUID(value) for value in row["artifact_ids"]),
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


def _datetime(value: datetime | str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _optional_datetime(value: datetime | str | None) -> datetime | None:
    return _datetime(value) if value is not None else None


__all__ = ["SqlAlchemyAssistantRepository"]
