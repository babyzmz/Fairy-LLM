from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import insert, select, update

from fairy_core.assistant.evidence import EvidenceRequirementKind
from fairy_core.assistant.execution_intent import ExecutionIntentSnapshot
from fairy_core.assistant.execution_intent_policy import intent_prohibitions
from fairy_core.assistant.interpretation import (
    AssistantRequestInterpretationRevision,
    InterpretationConfidence,
    InterpretationDisposition,
    InterpretedObjective,
    RequestAction,
)
from fairy_core.assistant.models import AssistantTurnStatus, MessageRole
from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.storage.schema import (
    assistant_messages,
    assistant_request_interpretations,
    assistant_turns,
    tasks,
)


class AssistantInterpretationRepositoryMixin:
    def append_interpretation(
        self,
        interpretation: AssistantRequestInterpretationRevision,
        *,
        expected_revision: int | None,
    ) -> None:
        if (
            isinstance(expected_revision, bool)
            or (expected_revision is not None and expected_revision < 1)
            or interpretation.revision != (expected_revision or 0) + 1
        ):
            raise InvalidTransitionError("Interpretation revision must advance exactly once")
        values = {
            "tenant_id": self._tenant_id,
            **_interpretation_values(interpretation),
        }
        with self._session.write() as connection:
            source = connection.execute(
                select(
                    assistant_messages.c.turn_id,
                    assistant_messages.c.role,
                    assistant_messages.c.content,
                ).where(
                    assistant_messages.c.tenant_id == self._tenant_id,
                    assistant_messages.c.id == str(interpretation.source_message_id),
                )
            ).one_or_none()
            if (
                source is None
                or source.turn_id != str(interpretation.turn_id)
                or source.role != MessageRole.USER.value
            ):
                raise InvalidTransitionError(
                    "Interpretation source must be a user Message from the same Turn"
                )
            if hashlib.sha256(source.content.encode("utf-8")).hexdigest() != (
                interpretation.source_message_sha256
            ):
                raise InvalidTransitionError("Interpretation source Message digest changed")
            binding = (
                connection.execute(
                    select(
                        tasks.c.id.label("task_id"),
                        tasks.c.conversation_id,
                        tasks.c.project_id,
                        tasks.c.workspace_id,
                        tasks.c.base_version_id,
                        tasks.c.target_version_id,
                        tasks.c.execution_target,
                        assistant_turns.c.scope_digest,
                    )
                    .select_from(
                        assistant_turns.join(
                            tasks,
                            (tasks.c.tenant_id == assistant_turns.c.tenant_id)
                            & (tasks.c.id == assistant_turns.c.task_id)
                            & (tasks.c.conversation_id == assistant_turns.c.conversation_id),
                        )
                    )
                    .where(
                        assistant_turns.c.tenant_id == self._tenant_id,
                        assistant_turns.c.id == str(interpretation.turn_id),
                    )
                )
                .mappings()
                .one_or_none()
            )
            if binding is None:
                raise InvalidTransitionError("Interpretation execution binding is unavailable")
            intent = ExecutionIntentSnapshot(
                turn_id=interpretation.turn_id,
                task_id=UUID(binding["task_id"]),
                conversation_id=UUID(binding["conversation_id"]),
                project_id=UUID(binding["project_id"]) if binding["project_id"] else None,
                workspace_id=UUID(binding["workspace_id"]),
                base_version_id=(
                    UUID(binding["base_version_id"]) if binding["base_version_id"] else None
                ),
                target_version_id=(
                    UUID(binding["target_version_id"]) if binding["target_version_id"] else None
                ),
                execution_target=binding["execution_target"],
                scope_digest=binding["scope_digest"],
                interpretation_id=interpretation.id,
                interpretation_revision=interpretation.revision,
                source_message_id=interpretation.source_message_id,
                source_message_sha256=interpretation.source_message_sha256,
                action=interpretation.action,
                objectives=interpretation.objectives,
                target_descriptions=interpretation.targets,
                user_constraints=interpretation.constraints,
                confidence=interpretation.confidence,
                disposition=interpretation.disposition,
                source_prohibitions=intent_prohibitions(source.content),
            )
            values["execution_intent"] = intent.model_dump(mode="json")
            current_predicate = (
                assistant_turns.c.active_interpretation_revision.is_(None)
                if expected_revision is None
                else assistant_turns.c.active_interpretation_revision == expected_revision
            )
            changed = connection.execute(
                update(assistant_turns)
                .where(
                    assistant_turns.c.tenant_id == self._tenant_id,
                    assistant_turns.c.id == str(interpretation.turn_id),
                    current_predicate,
                    assistant_turns.c.status.not_in(
                        (
                            AssistantTurnStatus.COMPLETED.value,
                            AssistantTurnStatus.CANCELLED.value,
                            AssistantTurnStatus.FAILED.value,
                        )
                    ),
                )
                .values(
                    active_interpretation_revision=interpretation.revision,
                    updated_at=interpretation.created_at,
                )
            ).rowcount
            if changed != 1:
                raise InvalidTransitionError("Assistant Turn interpretation changed concurrently")
            connection.execute(insert(assistant_request_interpretations).values(**values))

    def get_execution_intent(
        self,
        turn_id: UUID,
        revision: int | None = None,
    ) -> ExecutionIntentSnapshot | None:
        """Read the active binding, or an explicit immutable revision for audit."""
        statement = select(assistant_request_interpretations).where(
            assistant_request_interpretations.c.tenant_id == self._tenant_id,
            assistant_request_interpretations.c.turn_id == str(turn_id),
        )
        if revision is None:
            statement = statement.join(
                assistant_turns,
                (assistant_turns.c.tenant_id == assistant_request_interpretations.c.tenant_id)
                & (assistant_turns.c.id == assistant_request_interpretations.c.turn_id)
                & (
                    assistant_turns.c.active_interpretation_revision
                    == assistant_request_interpretations.c.revision
                ),
            )
        else:
            if isinstance(revision, bool) or revision < 1:
                raise ValueError("execution intent revision must be positive")
            statement = statement.where(assistant_request_interpretations.c.revision == revision)
        row = self._first(statement)
        if row is None or row["execution_intent"] is None:
            return None
        intent = ExecutionIntentSnapshot.model_validate_json(json.dumps(row["execution_intent"]))
        if (
            intent.turn_id != turn_id
            or intent.interpretation_id != UUID(row["id"])
            or intent.interpretation_revision != row["revision"]
            or intent.source_message_id != UUID(row["source_message_id"])
            or intent.source_message_sha256 != row["source_message_sha256"]
        ):
            raise InvalidTransitionError("Execution intent does not match its interpretation")
        return intent

    def get_interpretation(
        self,
        turn_id: UUID,
        revision: int | None = None,
    ) -> AssistantRequestInterpretationRevision | None:
        statement = select(assistant_request_interpretations).where(
            assistant_request_interpretations.c.tenant_id == self._tenant_id,
            assistant_request_interpretations.c.turn_id == str(turn_id),
        )
        if revision is None:
            statement = statement.order_by(
                assistant_request_interpretations.c.revision.desc()
            ).limit(1)
        else:
            if revision < 1:
                raise ValueError("interpretation revision must be positive")
            statement = statement.where(assistant_request_interpretations.c.revision == revision)
        row = self._first(statement)
        return _interpretation_from_row(row) if row is not None else None

    def list_interpretations(
        self,
        turn_id: UUID,
    ) -> tuple[AssistantRequestInterpretationRevision, ...]:
        with self._session.read() as connection:
            rows = (
                connection.execute(
                    select(assistant_request_interpretations)
                    .where(
                        assistant_request_interpretations.c.tenant_id == self._tenant_id,
                        assistant_request_interpretations.c.turn_id == str(turn_id),
                    )
                    .order_by(assistant_request_interpretations.c.revision)
                )
                .mappings()
                .all()
            )
        return tuple(_interpretation_from_row(row) for row in rows)

    def find_interpretation_by_idempotency_key(
        self,
        turn_id: UUID,
        idempotency_key: str,
    ) -> AssistantRequestInterpretationRevision | None:
        row = self._first(
            select(assistant_request_interpretations).where(
                assistant_request_interpretations.c.tenant_id == self._tenant_id,
                assistant_request_interpretations.c.turn_id == str(turn_id),
                assistant_request_interpretations.c.idempotency_key == idempotency_key.strip(),
            )
        )
        return _interpretation_from_row(row) if row is not None else None


def _interpretation_values(
    value: AssistantRequestInterpretationRevision,
) -> dict[str, object]:
    return {
        "id": str(value.id),
        "turn_id": str(value.turn_id),
        "revision": value.revision,
        "idempotency_key": value.idempotency_key,
        "source_message_id": str(value.source_message_id),
        "source_message_sha256": value.source_message_sha256,
        "schema_version": value.schema_version,
        "normalized_goal": value.normalized_goal,
        "action": value.action.value,
        "objectives": [
            {
                "goal": objective.goal,
                "action": objective.action.value,
                "depends_on": list(objective.depends_on),
            }
            for objective in value.objectives
        ],
        "targets": list(value.targets),
        "constraints": list(value.constraints),
        "deliverable": value.deliverable,
        "evidence_requirements": [item.value for item in value.evidence_requirements],
        "assumptions": list(value.assumptions),
        "missing_information": list(value.missing_information),
        "confidence": value.confidence.value,
        "disposition": value.disposition.value,
        "public_summary": value.public_summary,
        "clarification_question": value.clarification_question,
        "created_at": value.created_at,
    }


def _interpretation_from_row(
    row: Mapping[str, Any],
) -> AssistantRequestInterpretationRevision:
    return AssistantRequestInterpretationRevision(
        id=UUID(row["id"]),
        turn_id=UUID(row["turn_id"]),
        revision=int(row["revision"]),
        idempotency_key=row["idempotency_key"],
        source_message_id=UUID(row["source_message_id"]),
        source_message_sha256=row["source_message_sha256"],
        schema_version=int(row["schema_version"]),
        normalized_goal=row["normalized_goal"],
        action=RequestAction(row["action"]),
        objectives=tuple(
            InterpretedObjective(
                goal=item["goal"],
                action=RequestAction(item["action"]),
                depends_on=tuple(int(index) for index in item.get("depends_on", ())),
            )
            for item in row["objectives"]
        ),
        targets=tuple(str(item) for item in row["targets"]),
        constraints=tuple(str(item) for item in row["constraints"]),
        deliverable=row["deliverable"],
        evidence_requirements=tuple(
            EvidenceRequirementKind(item) for item in row["evidence_requirements"]
        ),
        assumptions=tuple(str(item) for item in row["assumptions"]),
        missing_information=tuple(str(item) for item in row["missing_information"]),
        confidence=InterpretationConfidence(row["confidence"]),
        disposition=InterpretationDisposition(row["disposition"]),
        public_summary=row["public_summary"],
        clarification_question=row["clarification_question"],
        created_at=_datetime(row["created_at"]),
    )


def _datetime(value: datetime | str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


__all__ = ["AssistantInterpretationRepositoryMixin"]
