from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from fairy_core.contracts.common import ContractModel


class PresentationScopeInput(ContractModel):
    workspace_id: UUID
    version_id: UUID
    file_set_id: UUID
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class AnnotationListInput(ContractModel):
    workspace_id: UUID
    version_id: UUID
    file_set_id: UUID


class AnnotationDocumentModel(ContractModel):
    id: UUID
    workspace_id: UUID
    version_id: UUID
    file_set_id: UUID
    source_hash: str
    revision: int
    annotations: tuple[dict[str, Any], ...]
    created_at: datetime
    updated_at: datetime


class AnnotationResultModel(ContractModel):
    document: AnnotationDocumentModel | None


class AnnotationUpdateInput(PresentationScopeInput):
    expected_revision: int = Field(ge=0)
    annotations: tuple[dict[str, Any], ...] = Field(max_length=2_000)


class SelectionCreateInput(PresentationScopeInput):
    source_path: str = Field(min_length=1, max_length=4096)
    viewer_kind: str = Field(min_length=1, max_length=64)
    locator_kind: str = Field(min_length=1, max_length=64)
    locator: dict[str, Any]


class SelectionReferenceModel(ContractModel):
    id: UUID
    workspace_id: UUID
    version_id: UUID
    file_set_id: UUID
    source_path: str
    source_hash: str
    viewer_kind: str
    locator_kind: str
    locator: dict[str, Any]
    created_at: datetime


class EditRecipeCreateInput(PresentationScopeInput):
    kind: str = Field(min_length=1, max_length=64)
    operations: tuple[dict[str, Any], ...] = Field(max_length=1_000)


class EditRecipeUpdateInput(ContractModel):
    recipe_id: UUID
    expected_revision: int = Field(ge=1)
    operations: tuple[dict[str, Any], ...] = Field(max_length=1_000)


class EditRecipeIdInput(ContractModel):
    recipe_id: UUID


class EditRecipeModel(ContractModel):
    id: UUID
    workspace_id: UUID
    version_id: UUID
    file_set_id: UUID
    source_hash: str
    kind: str
    operations: tuple[dict[str, Any], ...]
    status: str
    revision: int
    created_at: datetime
    updated_at: datetime


__all__ = [
    "AnnotationDocumentModel",
    "AnnotationListInput",
    "AnnotationResultModel",
    "AnnotationUpdateInput",
    "EditRecipeCreateInput",
    "EditRecipeIdInput",
    "EditRecipeModel",
    "EditRecipeUpdateInput",
    "SelectionCreateInput",
    "SelectionReferenceModel",
]
