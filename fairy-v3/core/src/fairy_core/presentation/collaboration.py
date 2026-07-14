from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from fairy_core.domain.errors import VersionConflictError
from fairy_core.domain.ids import new_id


class EditRecipeStatus(StrEnum):
    DRAFT = "draft"
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


__all__ = [
    "AnnotationDocument",
    "EditRecipe",
    "EditRecipeStatus",
    "SelectionReference",
]
