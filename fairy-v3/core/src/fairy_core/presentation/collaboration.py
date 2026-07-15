from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from fairy_core.domain.errors import IdempotencyConflictError, VersionConflictError
from fairy_core.domain.ids import new_id


class EditRecipeStatus(StrEnum):
    DRAFT = "draft"
    APPLYING = "applying"
    APPLIED = "applied"
    DISCARDED = "discarded"


@dataclass(frozen=True, slots=True)
class AnnotationDocument:
    id: UUID
    workspace_id: UUID
    version_id: UUID
    file_set_id: UUID
    source_hash: str
    revision: int
    annotations: tuple[dict[str, Any], ...]
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def create(
        cls, *, workspace_id: UUID, version_id: UUID, file_set_id: UUID, source_hash: str
    ) -> AnnotationDocument:
        return cls(new_id(), workspace_id, version_id, file_set_id, source_hash, 1, ())

    def update(
        self, *, expected_revision: int, annotations: tuple[dict[str, Any], ...]
    ) -> AnnotationDocument:
        if expected_revision != self.revision:
            raise VersionConflictError("Annotation revision changed concurrently")
        if len(annotations) > 2_000:
            raise ValueError("Annotation document exceeds the item limit")
        return replace(
            self,
            revision=self.revision + 1,
            annotations=annotations,
            updated_at=datetime.now(UTC),
        )


@dataclass(frozen=True, slots=True)
class SelectionReference:
    id: UUID
    workspace_id: UUID
    version_id: UUID
    file_set_id: UUID
    source_path: str
    source_hash: str
    viewer_kind: str
    locator_kind: str
    locator: dict[str, Any]
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class EditRecipe:
    id: UUID
    workspace_id: UUID
    version_id: UUID
    file_set_id: UUID
    source_hash: str
    kind: str
    operations: tuple[dict[str, Any], ...]
    status: EditRecipeStatus = EditRecipeStatus.DRAFT
    revision: int = 1
    apply_idempotency_key: str | None = None
    applied_version_id: UUID | None = None
    output_hash: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def update(
        self, *, expected_revision: int, operations: tuple[dict[str, Any], ...]
    ) -> EditRecipe:
        if self.status != EditRecipeStatus.DRAFT:
            raise ValueError("only draft Edit Recipes can be updated")
        if expected_revision != self.revision:
            raise VersionConflictError("Edit Recipe revision changed concurrently")
        return replace(
            self,
            operations=operations,
            revision=self.revision + 1,
            updated_at=datetime.now(UTC),
        )

    def discard(self) -> EditRecipe:
        if self.status != EditRecipeStatus.DRAFT:
            return self
        return replace(
            self,
            status=EditRecipeStatus.DISCARDED,
            revision=self.revision + 1,
            updated_at=datetime.now(UTC),
        )

    def begin_apply(self, *, expected_revision: int, idempotency_key: str) -> EditRecipe:
        if self.status is EditRecipeStatus.APPLIED:
            if self.apply_idempotency_key != idempotency_key:
                raise IdempotencyConflictError("Edit Recipe was applied with a different key")
            return self
        if self.status is EditRecipeStatus.APPLYING:
            if self.apply_idempotency_key != idempotency_key:
                raise IdempotencyConflictError("Edit Recipe apply is already in progress")
            return self
        if self.status is not EditRecipeStatus.DRAFT:
            raise ValueError("only draft Edit Recipes can be applied")
        if expected_revision != self.revision:
            raise VersionConflictError("Edit Recipe revision changed concurrently")
        if not idempotency_key.strip() or len(idempotency_key) > 255:
            raise ValueError("Edit Recipe apply idempotency key is invalid")
        return replace(
            self,
            status=EditRecipeStatus.APPLYING,
            revision=self.revision + 1,
            apply_idempotency_key=idempotency_key,
            updated_at=datetime.now(UTC),
        )

    def finish_apply(self, *, applied_version_id: UUID, output_hash: str) -> EditRecipe:
        if self.status is EditRecipeStatus.APPLIED:
            return self
        if self.status is not EditRecipeStatus.APPLYING:
            raise ValueError("Edit Recipe is not being applied")
        if len(output_hash) != 64 or any(value not in "0123456789abcdef" for value in output_hash):
            raise ValueError("Edit Recipe output hash must be lowercase SHA-256")
        return replace(
            self,
            status=EditRecipeStatus.APPLIED,
            revision=self.revision + 1,
            applied_version_id=applied_version_id,
            output_hash=output_hash,
            updated_at=datetime.now(UTC),
        )


__all__ = [
    "AnnotationDocument",
    "EditRecipe",
    "EditRecipeStatus",
    "SelectionReference",
]
