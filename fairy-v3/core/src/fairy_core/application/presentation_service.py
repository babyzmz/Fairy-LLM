from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from pydantic import BaseModel

from fairy_core.application.workspaces import WorkspaceApplication
from fairy_core.contracts.files import (
    FilePresentInput,
    FileRenderJobCancelInput,
    RendererPackInstallInput,
    RendererPackRemoveInput,
)
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.presentation.application import PresentationApplication
from fairy_core.presentation.pack_application import RendererPackApplication
from fairy_core.presentation.packs import RendererPackInstaller, RendererPackRecord


def presentation_service_handlers(
    *,
    unit_of_work_factory: CoreUnitOfWorkFactory,
    workspaces: WorkspaceApplication,
    renderer_pack_installer: RendererPackInstaller | None,
) -> Mapping[str, Callable[[BaseModel], object]]:
    files = PresentationApplication(
        unit_of_work_factory=unit_of_work_factory,
        workspaces=workspaces,
    )
    packs = RendererPackApplication(
        unit_of_work_factory=unit_of_work_factory,
        installer=renderer_pack_installer,
    )

    def present(request: BaseModel) -> object:
        assert isinstance(request, FilePresentInput)
        job, presentation = files.present(
            workspace_id=request.workspace_id,
            version_id=request.version_id,
            path=request.path,
            requested_mode=request.requested_mode,
        )
        return {"job": job, "presentation": presentation}

    def cancel(request: BaseModel) -> object:
        assert isinstance(request, FileRenderJobCancelInput)
        job, presentation = files.cancel(request.job_id)
        return {"job": job, "presentation": presentation}

    def list_packs(_request: BaseModel) -> object:
        return {"items": [_pack_record(item) for item in packs.list()]}

    def install_pack(request: BaseModel) -> object:
        assert isinstance(request, RendererPackInstallInput)
        return _pack_record(
            packs.install(Path(request.bundle_path), user_confirmed=request.user_confirmed)
        )

    def remove_pack(request: BaseModel) -> object:
        assert isinstance(request, RendererPackRemoveInput)
        return {
            "removed": packs.remove(
                request.pack_id,
                request.version,
                user_confirmed=request.user_confirmed,
            )
        }

    return {
        "files.present": present,
        "files.cancel": cancel,
        "renderer_packs.list": list_packs,
        "renderer_packs.health": list_packs,
        "renderer_packs.install": install_pack,
        "renderer_packs.update": install_pack,
        "renderer_packs.remove": remove_pack,
    }


def _pack_record(record: RendererPackRecord) -> dict[str, object]:
    return {
        "id": record.manifest.id,
        "version": record.manifest.version,
        "platform": record.manifest.platform,
        "input_media_types": record.manifest.input_media_types,
        "output_media_types": record.manifest.output_media_types,
        "features": record.manifest.features,
        "limits": dict(record.manifest.limits),
        "license": record.manifest.license,
        "sandbox": record.manifest.sandbox,
        "reproducible": record.manifest.reproducible,
        "health": record.health,
        "installed_at": record.installed_at,
    }


__all__ = ["presentation_service_handlers"]
