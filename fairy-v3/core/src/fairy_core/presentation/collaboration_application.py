from __future__ import annotations

import json
from uuid import UUID

from fairy_core.application.workspaces import WorkspaceApplication
from fairy_core.domain.errors import (
    CommandRejectedError,
    PreviewScopeViolationError,
    VersionConflictError,
)
from fairy_core.domain.ids import new_id
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.presentation.collaboration import (
    AnnotationDocument,
    EditRecipe,
    SelectionReference,
)

_LOCATOR_FIELDS = {
    "text_range": frozenset({"start", "end"}),
    "pdf_region": frozenset({"page", "x", "y", "width", "height"}),
    "sheet_range": frozenset({"sheet", "range"}),
    "time_range": frozenset({"start_ms", "end_ms"}),
    "image_region": frozenset({"x", "y", "width", "height"}),
    "scene_node": frozenset({"node_id"}),
    "cad_entity": frozenset({"entity_id"}),
    "archive_entry": frozenset({"path"}),
}


class CollaborationApplication:
    def __init__(
        self,
        *,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        workspaces: WorkspaceApplication,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._workspaces = workspaces

    def annotations(
        self, *, workspace_id: UUID, version_id: UUID, file_set_id: UUID
    ) -> AnnotationDocument | None:
        self._workspaces.get_file_set(
            workspace_id=workspace_id,
            version_id=version_id,
            file_set_id=file_set_id,
        )
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.presentations.list_annotations(
                workspace_id=workspace_id,
                version_id=version_id,
                file_set_id=file_set_id,
            )

    def update_annotations(
        self,
        *,
        workspace_id: UUID,
        version_id: UUID,
        file_set_id: UUID,
        source_hash: str,
        expected_revision: int,
        annotations: tuple[dict[str, object], ...],
    ) -> AnnotationDocument:
        self._require_file_set(workspace_id, version_id, file_set_id, source_hash)
        _require_bounded_json(annotations, 512 * 1024)
        with self._unit_of_work_factory() as unit_of_work:
            document = unit_of_work.presentations.list_annotations(
                workspace_id=workspace_id, version_id=version_id, file_set_id=file_set_id
            )
            if document is None:
                if expected_revision != 0:
                    raise VersionConflictError("Annotation revision changed concurrently")
                document = AnnotationDocument.create(
                    workspace_id=workspace_id,
                    version_id=version_id,
                    file_set_id=file_set_id,
                    source_hash=source_hash,
                )
                document = AnnotationDocument(
                    id=document.id,
                    workspace_id=document.workspace_id,
                    version_id=document.version_id,
                    file_set_id=document.file_set_id,
                    source_hash=document.source_hash,
                    revision=1,
                    annotations=annotations,
                    created_at=document.created_at,
                    updated_at=document.updated_at,
                )
            else:
                if document.source_hash != source_hash:
                    raise PreviewScopeViolationError("Annotation source changed")
                document = document.update(
                    expected_revision=expected_revision,
                    annotations=annotations,
                )
            unit_of_work.presentations.save_annotations(document)
            unit_of_work.commit()
            return document

    def create_selection(
        self,
        *,
        workspace_id: UUID,
        version_id: UUID,
        file_set_id: UUID,
        source_path: str,
        source_hash: str,
        viewer_kind: str,
        locator_kind: str,
        locator: dict[str, object],
    ) -> SelectionReference:
        primary_path = self._require_file_set(workspace_id, version_id, file_set_id, source_hash)
        if source_path != primary_path:
            raise PreviewScopeViolationError("Selection path does not match its FileSet")
        required = _LOCATOR_FIELDS.get(locator_kind)
        if required is None or not required.issubset(locator):
            raise ValueError("Selection locator is invalid")
        _require_bounded_json(locator, 16 * 1024)
        selection = SelectionReference(
            id=new_id(),
            workspace_id=workspace_id,
            version_id=version_id,
            file_set_id=file_set_id,
            source_path=source_path,
            source_hash=source_hash,
            viewer_kind=viewer_kind,
            locator_kind=locator_kind,
            locator=locator,
        )
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.presentations.save_selection(selection)
            unit_of_work.commit()
        return selection

    def create_recipe(
        self,
        *,
        workspace_id: UUID,
        version_id: UUID,
        file_set_id: UUID,
        source_hash: str,
        kind: str,
        operations: tuple[dict[str, object], ...],
    ) -> EditRecipe:
        self._require_file_set(workspace_id, version_id, file_set_id, source_hash)
        _require_bounded_json(operations, 256 * 1024)
        recipe = EditRecipe(
            id=new_id(),
            workspace_id=workspace_id,
            version_id=version_id,
            file_set_id=file_set_id,
            source_hash=source_hash,
            kind=kind,
            operations=operations,
        )
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.presentations.save_edit_recipe(recipe)
            unit_of_work.commit()
        return recipe

    def update_recipe(
        self,
        recipe_id: UUID,
        *,
        expected_revision: int,
        operations: tuple[dict[str, object], ...],
    ) -> EditRecipe:
        _require_bounded_json(operations, 256 * 1024)
        with self._unit_of_work_factory() as unit_of_work:
            recipe = unit_of_work.presentations.get_edit_recipe(recipe_id)
            if recipe is None:
                raise KeyError(f"Edit Recipe not found: {recipe_id}")
            recipe = recipe.update(expected_revision=expected_revision, operations=operations)
            unit_of_work.presentations.save_edit_recipe(recipe)
            unit_of_work.commit()
            return recipe

    def discard_recipe(self, recipe_id: UUID) -> EditRecipe:
        with self._unit_of_work_factory() as unit_of_work:
            recipe = unit_of_work.presentations.get_edit_recipe(recipe_id)
            if recipe is None:
                raise KeyError(f"Edit Recipe not found: {recipe_id}")
            discarded = recipe.discard()
            if discarded != recipe:
                unit_of_work.presentations.save_edit_recipe(discarded)
                unit_of_work.commit()
            return discarded

    @staticmethod
    def apply_recipe(_recipe_id: UUID) -> None:
        raise CommandRejectedError(
            "No trusted exporter is available for this Edit Recipe",
            code="EDIT_NOT_EXPORTABLE",
        )

    def _require_file_set(
        self, workspace_id: UUID, version_id: UUID, file_set_id: UUID, source_hash: str
    ) -> str:
        file_set = self._workspaces.get_file_set(
            workspace_id=workspace_id,
            version_id=version_id,
            file_set_id=file_set_id,
        )
        primary = next(item for item in file_set.members if item.path == file_set.primary_path)
        if primary.content_hash != source_hash:
            raise PreviewScopeViolationError("Presentation source changed")
        return primary.path


def _require_bounded_json(value: object, max_bytes: int) -> None:
    if len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()) > max_bytes:
        raise ValueError("Presentation metadata exceeds its size limit")


__all__ = ["CollaborationApplication"]
