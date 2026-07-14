from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Annotated, Any
from uuid import UUID

from fairy_core.contracts.files import (
    AssetSetCreateInput,
    AssetSetModel,
    AssetSetPageModel,
    FileDescriptorModel,
    FilePresentationResultModel,
    FilePresentInput,
    FileRenderJobCancelInput,
    FileSetGetInput,
    FileSetModel,
    FileSetResolveInput,
    RendererPackInstallInput,
    RendererPackModel,
    RendererPackPageModel,
    RendererPackRemoveInput,
    RendererPackRemoveResultModel,
)
from fairy_core.contracts.presentation import (
    AnnotationDocumentModel,
    AnnotationListInput,
    AnnotationResultModel,
    AnnotationUpdateInput,
    EditRecipeCreateInput,
    EditRecipeIdInput,
    EditRecipeModel,
    EditRecipeUpdateInput,
    SelectionCreateInput,
    SelectionReferenceModel,
)
from fairy_core.contracts.workspaces import (
    FileReadSessionModel,
    WorkspaceExportInput,
    WorkspaceExportModel,
    WorkspaceFileContentModel,
    WorkspaceFileMutateInput,
    WorkspaceFileMutationResultModel,
    WorkspaceFilePageModel,
    WorkspaceFileStreamInput,
    WorkspaceModel,
    WorkspaceVersionInput,
)
from fastapi import APIRouter, Header, HTTPException, Query


def install_workspace_routes(
    router: APIRouter,
    invoke: Callable[[str, Mapping[str, Any]], dict[str, Any]],
) -> None:
    @router.get(
        "/workspaces/{workspace_id}",
        operation_id="workspaces.get",
        response_model=WorkspaceModel,
    )
    def get_workspace(workspace_id: UUID) -> dict[str, Any]:
        return invoke("workspaces.get", {"workspace_id": str(workspace_id)})

    @router.get(
        "/workspaces/{workspace_id}/files",
        operation_id="workspaces.files.list",
        response_model=WorkspaceFilePageModel,
    )
    def list_workspace_files(
        workspace_id: UUID,
        version_id: UUID | None = None,
    ) -> dict[str, Any]:
        params = {"workspace_id": str(workspace_id)}
        if version_id is not None:
            params["version_id"] = str(version_id)
        return invoke("workspaces.files.list", params)

    @router.get(
        "/workspaces/{workspace_id}/file-content",
        operation_id="workspaces.files.read",
        response_model=WorkspaceFileContentModel,
    )
    def read_workspace_file(
        workspace_id: UUID,
        path: str = Query(min_length=1, max_length=1_024),
        version_id: UUID | None = None,
    ) -> dict[str, Any]:
        params = {"workspace_id": str(workspace_id), "path": path}
        if version_id is not None:
            params["version_id"] = str(version_id)
        return invoke("workspaces.files.read", params)

    @router.post(
        "/files/open-stream",
        operation_id="files.open_stream",
        response_model=FileReadSessionModel,
    )
    def open_file_stream(request: WorkspaceFileStreamInput) -> dict[str, Any]:
        return invoke("files.open_stream", request.model_dump(mode="json"))

    @router.get(
        "/workspaces/{workspace_id}/probe",
        operation_id="files.probe",
        response_model=FileDescriptorModel,
    )
    def probe_file(
        workspace_id: UUID,
        path: str = Query(min_length=1, max_length=1_024),
        version_id: UUID | None = None,
    ) -> dict[str, Any]:
        params = {"workspace_id": str(workspace_id), "path": path}
        if version_id is not None:
            params["version_id"] = str(version_id)
        return invoke("files.probe", params)

    @router.post(
        "/files/present",
        operation_id="files.present",
        response_model=FilePresentationResultModel,
    )
    def present_file(request: FilePresentInput) -> dict[str, Any]:
        return invoke("files.present", request.model_dump(mode="json"))

    @router.post(
        "/files/cancel",
        operation_id="files.cancel",
        response_model=FilePresentationResultModel,
    )
    def cancel_file_presentation(request: FileRenderJobCancelInput) -> dict[str, Any]:
        return invoke("files.cancel", request.model_dump(mode="json"))

    @router.post(
        "/asset-sets",
        operation_id="asset_sets.create",
        response_model=AssetSetModel,
    )
    def create_asset_set(
        request: AssetSetCreateInput,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=512),
        ],
    ) -> dict[str, Any]:
        if request.idempotency_key != idempotency_key:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "IDEMPOTENCY_CONFLICT",
                    "message": "Idempotency-Key does not match Core params",
                    "retryable": False,
                },
            )
        return invoke("asset_sets.create", request.model_dump(mode="json"))

    @router.post(
        "/asset-sets/list",
        operation_id="asset_sets.list",
        response_model=AssetSetPageModel,
    )
    def list_asset_sets(request: WorkspaceVersionInput) -> dict[str, Any]:
        return invoke("asset_sets.list", request.model_dump(mode="json", exclude_none=True))

    @router.get(
        "/renderer-packs",
        operation_id="renderer_packs.list",
        response_model=RendererPackPageModel,
    )
    def list_renderer_packs() -> dict[str, Any]:
        return invoke("renderer_packs.list", {})

    @router.get(
        "/renderer-packs/health",
        operation_id="renderer_packs.health",
        response_model=RendererPackPageModel,
    )
    def renderer_pack_health() -> dict[str, Any]:
        return invoke("renderer_packs.health", {})

    @router.post(
        "/renderer-packs/install",
        operation_id="renderer_packs.install",
        response_model=RendererPackModel,
    )
    def install_renderer_pack(request: RendererPackInstallInput) -> dict[str, Any]:
        return invoke("renderer_packs.install", request.model_dump(mode="json"))

    @router.post(
        "/renderer-packs/update",
        operation_id="renderer_packs.update",
        response_model=RendererPackModel,
    )
    def update_renderer_pack(request: RendererPackInstallInput) -> dict[str, Any]:
        return invoke("renderer_packs.update", request.model_dump(mode="json"))

    @router.post(
        "/renderer-packs/remove",
        operation_id="renderer_packs.remove",
        response_model=RendererPackRemoveResultModel,
    )
    def remove_renderer_pack(request: RendererPackRemoveInput) -> dict[str, Any]:
        return invoke("renderer_packs.remove", request.model_dump(mode="json"))

    @router.post(
        "/annotations/list", operation_id="annotations.list", response_model=AnnotationResultModel
    )
    def list_annotations(request: AnnotationListInput) -> dict[str, Any]:
        return invoke("annotations.list", request.model_dump(mode="json"))

    @router.post(
        "/annotations/update",
        operation_id="annotations.update",
        response_model=AnnotationDocumentModel,
    )
    def update_annotations(request: AnnotationUpdateInput) -> dict[str, Any]:
        return invoke("annotations.update", request.model_dump(mode="json"))

    @router.post(
        "/selections",
        operation_id="selections.create",
        response_model=SelectionReferenceModel,
    )
    def create_selection(request: SelectionCreateInput) -> dict[str, Any]:
        return invoke("selections.create", request.model_dump(mode="json"))

    @router.post(
        "/edit-recipes", operation_id="edit_recipes.create", response_model=EditRecipeModel
    )
    def create_edit_recipe(request: EditRecipeCreateInput) -> dict[str, Any]:
        return invoke("edit_recipes.create", request.model_dump(mode="json"))

    @router.post(
        "/edit-recipes/update",
        operation_id="edit_recipes.update",
        response_model=EditRecipeModel,
    )
    def update_edit_recipe(request: EditRecipeUpdateInput) -> dict[str, Any]:
        return invoke("edit_recipes.update", request.model_dump(mode="json"))

    @router.post(
        "/edit-recipes/apply", operation_id="edit_recipes.apply", response_model=EditRecipeModel
    )
    def apply_edit_recipe(request: EditRecipeIdInput) -> dict[str, Any]:
        return invoke("edit_recipes.apply", request.model_dump(mode="json"))

    @router.post(
        "/edit-recipes/discard",
        operation_id="edit_recipes.discard",
        response_model=EditRecipeModel,
    )
    def discard_edit_recipe(request: EditRecipeIdInput) -> dict[str, Any]:
        return invoke("edit_recipes.discard", request.model_dump(mode="json"))

    @router.post(
        "/file-sets/resolve",
        operation_id="file_sets.resolve",
        response_model=FileSetModel,
    )
    def resolve_file_set(request: FileSetResolveInput) -> dict[str, Any]:
        return invoke("file_sets.resolve", request.model_dump(mode="json"))

    @router.post(
        "/file-sets/get",
        operation_id="file_sets.get",
        response_model=FileSetModel,
    )
    def get_file_set(request: FileSetGetInput) -> dict[str, Any]:
        return invoke("file_sets.get", request.model_dump(mode="json"))

    @router.post(
        "/workspaces/files/mutate",
        operation_id="workspaces.files.mutate",
        response_model=WorkspaceFileMutationResultModel,
    )
    def mutate_workspace_files(request: WorkspaceFileMutateInput) -> dict[str, Any]:
        return invoke("workspaces.files.mutate", request.model_dump(mode="json"))

    @router.post(
        "/workspaces/export",
        operation_id="workspaces.export",
        response_model=WorkspaceExportModel,
    )
    def export_workspace(request: WorkspaceExportInput) -> dict[str, Any]:
        return invoke("workspaces.export", request.model_dump(mode="json"))


__all__ = ["install_workspace_routes"]
