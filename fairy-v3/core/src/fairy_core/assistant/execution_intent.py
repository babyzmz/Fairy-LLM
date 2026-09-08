from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fairy_core.assistant.interpretation import (
    InterpretationConfidence,
    InterpretationDisposition,
    InterpretedObjective,
    RequestAction,
)

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
TargetDescription = Annotated[str, Field(min_length=1, max_length=1_000)]
UserConstraint = Annotated[str, Field(min_length=1, max_length=2_000)]


class ExecutionIntentSnapshot(BaseModel):
    """Immutable interpretation + execution binding, never an authorization grant.

    Target descriptions still need authoritative resolution. In particular, a model's
    path string must not become a Scope allowlist merely by appearing in this record.
    Tenant ownership is supplied by the repository, not by this JSON payload.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal[1] = 1
    turn_id: UUID
    task_id: UUID
    conversation_id: UUID
    project_id: UUID | None
    workspace_id: UUID
    base_version_id: UUID | None
    target_version_id: UUID | None
    execution_target: Literal["local", "cloud"]
    scope_digest: Digest
    interpretation_id: UUID
    interpretation_revision: int = Field(gt=0)
    source_message_id: UUID
    source_message_sha256: Digest
    action: RequestAction
    # Trusted transient Workflow projection, never part of persisted interpretation JSON.
    active_objective_index: int | None = Field(default=None, ge=0, lt=16, exclude=True)
    objectives: tuple[InterpretedObjective, ...] = Field(min_length=1, max_length=16)
    target_descriptions: tuple[TargetDescription, ...] = Field(max_length=64)
    user_constraints: tuple[UserConstraint, ...] = Field(max_length=64)
    confidence: InterpretationConfidence
    disposition: InterpretationDisposition
    source_prohibitions: tuple[
        Literal["mutation", "execution", "notification", "memory", "publication"], ...
    ] = ()

    @model_validator(mode="after")
    def validate_objective_dependencies(self) -> ExecutionIntentSnapshot:
        if (
            self.active_objective_index is not None
            and self.active_objective_index >= len(self.objectives)
        ):
            raise ValueError("active objective is outside the declared intent")
        for index, objective in enumerate(self.objectives):
            if any(dependency >= index for dependency in objective.depends_on):
                raise ValueError("intent dependencies must reference earlier objectives")
        return self


__all__ = ["ExecutionIntentSnapshot"]
