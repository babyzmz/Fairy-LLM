from __future__ import annotations

import platform
from uuid import UUID

from fairy_core.application.workspaces import WorkspaceApplication
from fairy_core.domain.ids import new_id
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.presentation.cache import presentation_cache_key
from fairy_core.presentation.document_inspector import (
    DocumentInspectionError,
    DocumentPackageInspector,
)
from fairy_core.presentation.models import (
    FilePresentation,
    FileRenderJob,
    PresentationFidelity,
    RenderJobStatus,
)

_DIRECT_MEDIA_PREFIXES = ("text/", "image/", "audio/", "video/")
_DIRECT_MEDIA_TYPES = frozenset(
    {
        "application/pdf",
        "application/json",
        "application/xml",
        "application/xhtml+xml",
        "model/gltf+json",
        "model/gltf-binary",
    }
)
_PACK_BY_MEDIA_TYPE = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "office",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "office",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "office",
    "application/vnd.ms-excel": "office",
    "application/msword": "office",
    "application/vnd.oasis.opendocument.text": "office",
    "application/vnd.oasis.opendocument.presentation": "office",
    "application/vnd.oasis.opendocument.spreadsheet": "office",
    "application/zip": "archive",
}
_OFFICE_EXTENSIONS = frozenset(
    {
        "doc",
        "docx",
        "key",
        "numbers",
        "odp",
        "ods",
        "odt",
        "pages",
        "ppt",
        "pptx",
        "xls",
        "xlsx",
    }
)
_BUILTIN_DOCUMENT_EXTENSIONS = frozenset(
    {"docx", "key", "numbers", "odp", "ods", "odt", "pages", "pptx", "xlsx"}
)
_PACK_BY_EXTENSION = {
    **{
        extension: "cad"
        for extension in ("brep", "dxf", "iges", "igs", "obj", "ply", "step", "stl", "stp")
    },
    **{extension: "bim" for extension in ("ifc", "ifczip")},
    **{extension: "dcc-3d" for extension in ("3mf", "blend", "dae", "fbx", "usda", "usdc", "usdz")},
}


class PresentationApplication:
    def __init__(
        self,
        *,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        workspaces: WorkspaceApplication,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._workspaces = workspaces
        self._documents = DocumentPackageInspector()

    def present(
        self,
        *,
        workspace_id: UUID,
        version_id: UUID | None,
        path: str,
        requested_mode: str,
    ) -> tuple[FileRenderJob, FilePresentation | None]:
        descriptor = self._workspaces.probe_file(
            workspace_id=workspace_id,
            version_id=version_id,
            path=path,
        )
        file_set = self._workspaces.resolve_file_set(
            workspace_id=workspace_id,
            version_id=version_id,
            path=path,
        )
        pack_id, pack_version = self._renderer_for(
            descriptor.media_type,
            descriptor.extension,
        )
        cache_key = presentation_cache_key(
            source_hash=descriptor.content_hash,
            dependency_hashes=[
                member.content_hash
                for member in file_set.members
                if member.path != file_set.primary_path
            ],
            renderer_pack_id=pack_id,
            renderer_pack_version=pack_version,
            parameters={"mode": requested_mode},
            platform=platform.system().lower(),
            color_configuration="srgb-v1",
        )
        with self._unit_of_work_factory() as unit_of_work:
            existing = unit_of_work.presentations.find_job_by_cache_key(
                workspace_id=workspace_id,
                version_id=file_set.version_id,
                cache_key=cache_key,
            )
            if existing is not None:
                return existing, unit_of_work.presentations.get_presentation_for_job(existing.id)
            job = FileRenderJob.create(
                workspace_id=workspace_id,
                version_id=file_set.version_id,
                file_set_id=file_set.id,
                source_path=file_set.primary_path,
                source_hash=descriptor.content_hash,
                cache_key=cache_key,
                requested_mode=requested_mode,
                renderer_pack_id=(
                    None if pack_id in {"browser-native", "builtin-document"} else pack_id
                ),
                renderer_pack_version=(
                    None if pack_id in {"browser-native", "builtin-document"} else pack_version
                ),
            )
            presentation: FilePresentation | None = None
            if pack_id == "browser-native":
                job = job.transition(
                    RenderJobStatus.READY,
                    public_summary="Ready for secure native presentation",
                )
                presentation = FilePresentation(
                    id=new_id(),
                    job_id=job.id,
                    workspace_id=job.workspace_id,
                    version_id=job.version_id,
                    file_set_id=job.file_set_id,
                    source_path=job.source_path,
                    source_hash=job.source_hash,
                    renderer=pack_id,
                    fidelity=PresentationFidelity.NATIVE,
                    status=RenderJobStatus.READY,
                    capabilities=self._capabilities(descriptor.media_type),
                )
            elif pack_id == "builtin-document":
                try:
                    _, content = self._workspaces.read_file(
                        workspace_id=workspace_id,
                        version_id=file_set.version_id,
                        path=file_set.primary_path,
                    )
                    assert content is not None
                    inspection = self._documents.inspect(
                        content,
                        extension=descriptor.extension or "",
                    )
                    target_status = (
                        RenderJobStatus.PARTIAL if inspection.partial else RenderJobStatus.READY
                    )
                    job = job.transition(RenderJobStatus.QUEUED, progress=5)
                    job = job.transition(RenderJobStatus.CONVERTING, progress=40)
                    job = job.transition(RenderJobStatus.VALIDATING, progress=90)
                    job = job.transition(
                        target_status,
                        public_summary=(
                            "Content-only document preview ready"
                            if inspection.fidelity is PresentationFidelity.CONTENT_ONLY
                            else "Embedded document preview ready"
                        ),
                    )
                    presentation_id = new_id()
                    presentation = FilePresentation(
                        id=presentation_id,
                        job_id=job.id,
                        workspace_id=job.workspace_id,
                        version_id=job.version_id,
                        file_set_id=job.file_set_id,
                        source_path=job.source_path,
                        source_hash=job.source_hash,
                        renderer=inspection.renderer,
                        fidelity=inspection.fidelity,
                        status=target_status,
                        capabilities=inspection.capabilities,
                        assets=(inspection.asset(presentation_id),),
                    )
                except DocumentInspectionError:
                    job = job.transition(
                        RenderJobStatus.QUARANTINED,
                        error_code="DOCUMENT_PACKAGE_INVALID",
                        public_summary="Document preview was blocked by package validation",
                    )
            else:
                job = job.transition(
                    RenderJobStatus.WAITING_FOR_PACK,
                    public_summary=f"Renderer Pack required: {pack_id}",
                )
            unit_of_work.presentations.save_job(job)
            if presentation is not None:
                unit_of_work.presentations.save_presentation(presentation)
            unit_of_work.commit()
            return job, presentation

    def cancel(self, job_id: UUID) -> tuple[FileRenderJob, FilePresentation | None]:
        with self._unit_of_work_factory() as unit_of_work:
            job = unit_of_work.presentations.get_job(job_id)
            if job is None:
                raise KeyError(f"File Render Job not found: {job_id}")
            if job.status in {
                RenderJobStatus.READY,
                RenderJobStatus.PARTIAL,
                RenderJobStatus.FAILED,
                RenderJobStatus.CANCELED,
                RenderJobStatus.QUARANTINED,
            }:
                return job, unit_of_work.presentations.get_presentation_for_job(job.id)
            job = job.transition(
                RenderJobStatus.CANCELED,
                public_summary="Presentation canceled",
            )
            unit_of_work.presentations.save_job(job)
            unit_of_work.commit()
            return job, None

    @staticmethod
    def _renderer_for(media_type: str, extension: str | None) -> tuple[str, str]:
        if extension and extension.lower() in _BUILTIN_DOCUMENT_EXTENSIONS:
            return "builtin-document", "1"
        if extension and extension.lower() in _OFFICE_EXTENSIONS:
            return "office", "latest"
        if extension and extension.lower() in _PACK_BY_EXTENSION:
            return _PACK_BY_EXTENSION[extension.lower()], "latest"
        if media_type.startswith(_DIRECT_MEDIA_PREFIXES) or media_type in _DIRECT_MEDIA_TYPES:
            return "browser-native", "1"
        return _PACK_BY_MEDIA_TYPE.get(media_type, "universal"), "latest"

    @staticmethod
    def _capabilities(media_type: str) -> tuple[str, ...]:
        if media_type.startswith("image/"):
            return ("zoom", "pan", "inspect", "annotate")
        if media_type.startswith(("audio/", "video/")):
            return ("play", "seek", "volume", "captions")
        if media_type == "application/pdf":
            return ("pages", "search", "zoom", "select", "annotate")
        if media_type.startswith("model/"):
            return ("orbit", "pan", "zoom", "inspect")
        return ("search", "select", "copy")


__all__ = ["PresentationApplication"]
