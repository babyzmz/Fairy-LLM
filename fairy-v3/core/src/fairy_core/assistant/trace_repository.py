from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import insert, select, update

from fairy_core.assistant.trace_models import (
    TraceStep,
    TraceStepKind,
    TraceStepStatus,
    TraceVisibility,
    TurnTrace,
)
from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.providers import ModelExecutionRole
from fairy_core.storage.schema import turn_trace_steps, turn_traces


class TurnTraceRepositoryMixin:
    def create_trace_if_absent(self, trace: TurnTrace) -> tuple[TurnTrace, bool]:
        values = {"tenant_id": self._tenant_id, **self._trace_values(trace)}
        statement = (
            self._insert(turn_traces)
            .values(**values)
            .on_conflict_do_nothing(index_elements=[turn_traces.c.tenant_id, turn_traces.c.turn_id])
        )
        with self._session.write() as connection:
            inserted_id = connection.execute(
                statement.returning(turn_traces.c.id)
            ).scalar_one_or_none()
            row = (
                connection.execute(
                    select(turn_traces).where(
                        turn_traces.c.tenant_id == self._tenant_id,
                        turn_traces.c.turn_id == str(trace.turn_id),
                    )
                )
                .mappings()
                .one()
            )
        persisted = self._trace_from_row(row)
        if (
            persisted.turn_id != trace.turn_id
            or persisted.task_id != trace.task_id
            or persisted.conversation_id != trace.conversation_id
        ):
            raise InvalidTransitionError("Turn Trace identity changed concurrently")
        return persisted, inserted_id is not None

    def update_trace(self, trace: TurnTrace, *, expected_revision: int) -> None:
        with self._session.write() as connection:
            result = connection.execute(
                update(turn_traces)
                .where(
                    turn_traces.c.tenant_id == self._tenant_id,
                    turn_traces.c.id == str(trace.id),
                    turn_traces.c.turn_id == str(trace.turn_id),
                    turn_traces.c.conversation_id == str(trace.conversation_id),
                    turn_traces.c.task_id == str(trace.task_id),
                    turn_traces.c.revision == expected_revision,
                )
                .values(
                    legacy=trace.legacy,
                    revision=trace.revision,
                    updated_at=trace.updated_at,
                    started_at=trace.started_at,
                    completed_at=trace.completed_at,
                )
            )
        if result.rowcount != 1:
            raise InvalidTransitionError("Turn Trace changed concurrently")

    def get_trace_by_turn_id(self, turn_id: UUID) -> TurnTrace | None:
        row = self._first(
            select(turn_traces).where(
                turn_traces.c.tenant_id == self._tenant_id,
                turn_traces.c.turn_id == str(turn_id),
            )
        )
        return self._trace_from_row(row) if row is not None else None

    def allocate_trace_sequence(self, trace_id: UUID) -> int:
        with self._session.write() as connection:
            sequence = connection.execute(
                update(turn_traces)
                .where(
                    turn_traces.c.tenant_id == self._tenant_id,
                    turn_traces.c.id == str(trace_id),
                )
                .values(
                    last_sequence=turn_traces.c.last_sequence + 1,
                    revision=turn_traces.c.revision + 1,
                    updated_at=datetime.now(UTC),
                )
                .returning(turn_traces.c.last_sequence)
            ).scalar_one_or_none()
        if sequence is None:
            raise InvalidTransitionError("Turn Trace is missing")
        return int(sequence)

    def append_trace_step(self, step: TraceStep) -> None:
        values = {"tenant_id": self._tenant_id, **self._trace_step_values(step)}
        with self._session.write() as connection:
            connection.execute(insert(turn_trace_steps).values(**values))

    def update_trace_step(self, step: TraceStep, *, expected_revision: int) -> None:
        with self._session.write() as connection:
            result = connection.execute(
                update(turn_trace_steps)
                .where(
                    turn_trace_steps.c.tenant_id == self._tenant_id,
                    turn_trace_steps.c.id == str(step.id),
                    turn_trace_steps.c.trace_id == str(step.trace_id),
                    turn_trace_steps.c.turn_id == str(step.turn_id),
                    turn_trace_steps.c.sequence == step.sequence,
                    turn_trace_steps.c.kind == step.kind.value,
                    turn_trace_steps.c.revision == expected_revision,
                )
                .values(**self._trace_step_mutable_values(step))
            )
        if result.rowcount != 1:
            raise InvalidTransitionError("Trace Step changed concurrently")

    def get_trace_step(self, step_id: UUID) -> TraceStep | None:
        row = self._first(
            select(turn_trace_steps).where(
                turn_trace_steps.c.tenant_id == self._tenant_id,
                turn_trace_steps.c.id == str(step_id),
            )
        )
        return self._trace_step_from_row(row) if row is not None else None

    def find_trace_step_by_command_run_id(
        self,
        command_run_id: UUID,
        *,
        kind: TraceStepKind | None = None,
    ) -> TraceStep | None:
        predicates = [
            turn_trace_steps.c.tenant_id == self._tenant_id,
            turn_trace_steps.c.command_run_id == str(command_run_id),
        ]
        if kind is not None:
            predicates.append(turn_trace_steps.c.kind == TraceStepKind(kind).value)
        row = self._first(
            select(turn_trace_steps)
            .where(*predicates)
            .order_by(turn_trace_steps.c.sequence.desc(), turn_trace_steps.c.id.desc())
        )
        return self._trace_step_from_row(row) if row is not None else None

    def list_trace_steps(self, turn_id: UUID) -> tuple[TraceStep, ...]:
        with self._session.read() as connection:
            rows = (
                connection.execute(
                    select(turn_trace_steps)
                    .where(
                        turn_trace_steps.c.tenant_id == self._tenant_id,
                        turn_trace_steps.c.turn_id == str(turn_id),
                    )
                    .order_by(turn_trace_steps.c.sequence, turn_trace_steps.c.id)
                )
                .mappings()
                .all()
            )
        return tuple(self._trace_step_from_row(row) for row in rows)

    @staticmethod
    def _trace_values(trace: TurnTrace) -> dict[str, object]:
        return {
            "id": str(trace.id),
            "turn_id": str(trace.turn_id),
            "conversation_id": str(trace.conversation_id),
            "task_id": str(trace.task_id),
            "legacy": trace.legacy,
            "last_sequence": trace.last_sequence,
            "revision": trace.revision,
            "created_at": trace.created_at,
            "updated_at": trace.updated_at,
            "started_at": trace.started_at,
            "completed_at": trace.completed_at,
        }

    @staticmethod
    def _trace_from_row(row: Mapping[str, Any]) -> TurnTrace:
        return TurnTrace(
            id=UUID(row["id"]),
            turn_id=UUID(row["turn_id"]),
            conversation_id=UUID(row["conversation_id"]),
            task_id=UUID(row["task_id"]),
            legacy=bool(row["legacy"]),
            last_sequence=int(row["last_sequence"]),
            revision=int(row["revision"]),
            created_at=_datetime(row["created_at"]),
            updated_at=_datetime(row["updated_at"]),
            started_at=_optional_datetime(row["started_at"]),
            completed_at=_optional_datetime(row["completed_at"]),
        )

    @classmethod
    def _trace_step_values(cls, step: TraceStep) -> dict[str, object]:
        return {
            "id": str(step.id),
            "trace_id": str(step.trace_id),
            "turn_id": str(step.turn_id),
            "sequence": step.sequence,
            "parent_step_id": str(step.parent_step_id) if step.parent_step_id else None,
            "caused_by_step_id": (str(step.caused_by_step_id) if step.caused_by_step_id else None),
            "kind": step.kind.value,
            **cls._trace_step_mutable_values(step),
            "created_at": step.created_at,
        }

    @staticmethod
    def _trace_step_mutable_values(step: TraceStep) -> dict[str, object]:
        return {
            "status": step.status.value,
            "public_summary": step.public_summary,
            "public_detail": step.public_detail,
            "model_id": step.model_id,
            "model_role": step.model_role.value if step.model_role is not None else None,
            "provider_attempt_id": (
                str(step.provider_attempt_id) if step.provider_attempt_id else None
            ),
            "command_run_id": str(step.command_run_id) if step.command_run_id else None,
            "artifact_refs": [str(value) for value in step.artifact_refs],
            "visibility": step.visibility.value,
            "revision": step.revision,
            "updated_at": step.updated_at,
            "started_at": step.started_at,
            "completed_at": step.completed_at,
        }

    @staticmethod
    def _trace_step_from_row(row: Mapping[str, Any]) -> TraceStep:
        return TraceStep(
            id=UUID(row["id"]),
            trace_id=UUID(row["trace_id"]),
            turn_id=UUID(row["turn_id"]),
            sequence=int(row["sequence"]),
            parent_step_id=UUID(row["parent_step_id"]) if row["parent_step_id"] else None,
            caused_by_step_id=(
                UUID(row["caused_by_step_id"]) if row["caused_by_step_id"] else None
            ),
            kind=TraceStepKind(row["kind"]),
            status=TraceStepStatus(row["status"]),
            public_summary=row["public_summary"],
            public_detail=row["public_detail"],
            model_id=row["model_id"],
            model_role=(ModelExecutionRole(row["model_role"]) if row["model_role"] else None),
            provider_attempt_id=(
                UUID(row["provider_attempt_id"]) if row["provider_attempt_id"] else None
            ),
            command_run_id=UUID(row["command_run_id"]) if row["command_run_id"] else None,
            artifact_refs=tuple(UUID(value) for value in row["artifact_refs"]),
            visibility=TraceVisibility(row["visibility"]),
            revision=int(row["revision"]),
            created_at=_datetime(row["created_at"]),
            updated_at=_datetime(row["updated_at"]),
            started_at=_optional_datetime(row["started_at"]),
            completed_at=_optional_datetime(row["completed_at"]),
        )


def _datetime(value: datetime | str) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)


def _optional_datetime(value: datetime | str | None) -> datetime | None:
    return _datetime(value) if value is not None else None


__all__ = ["TurnTraceRepositoryMixin"]
