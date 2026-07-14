from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from uuid import UUID

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Connection, RowMapping
from sqlalchemy.exc import IntegrityError

from fairy_core.domain.errors import VersionConflictError
from fairy_core.persistence.session import SqlAlchemySession
from fairy_core.persistence.tenant import normalize_tenant_id
from fairy_core.presentation.collaboration import (
    AnnotationDocument,
    EditRecipe,
    EditRecipeStatus,
    SelectionReference,
)
from fairy_core.presentation.models import (
    DerivedAsset,
    FilePresentation,
    FileRenderJob,
    PresentationFidelity,
    RenderJobStatus,
)
from fairy_core.presentation.packs import RendererPackManifest, RendererPackRecord
from fairy_core.storage.schema import (
    annotation_documents,
    derived_assets,
    edit_recipes,
    file_presentations,
    file_render_jobs,
    renderer_packs,
    selection_references,
)


def _datetime(value: datetime | str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


class SqlAlchemyPresentationRepository:
    def __init__(self, connection: Connection, *, tenant_id: str) -> None:
        self._tenant_id = normalize_tenant_id(tenant_id)
        self._session = SqlAlchemySession(connection)

    def get_job(self, job_id: UUID) -> FileRenderJob | None:
        with self._session.read() as connection:
            row = (
                connection.execute(
                    select(file_render_jobs).where(
                        file_render_jobs.c.tenant_id == self._tenant_id,
                        file_render_jobs.c.id == str(job_id),
                    )
                )
                .mappings()
                .first()
            )
        return self._job(row) if row else None

    def find_job_by_cache_key(
        self, *, workspace_id: UUID, version_id: UUID, cache_key: str
    ) -> FileRenderJob | None:
        with self._session.read() as connection:
            row = (
                connection.execute(
                    select(file_render_jobs).where(
                        file_render_jobs.c.tenant_id == self._tenant_id,
                        file_render_jobs.c.workspace_id == str(workspace_id),
                        file_render_jobs.c.version_id == str(version_id),
                        file_render_jobs.c.cache_key == cache_key,
                    )
                )
                .mappings()
                .first()
            )
        return self._job(row) if row else None

    def save_job(self, job: FileRenderJob) -> None:
        values = {
            "tenant_id": self._tenant_id,
            "id": str(job.id),
            "workspace_id": str(job.workspace_id),
            "version_id": str(job.version_id),
            "file_set_id": str(job.file_set_id),
            "source_path": job.source_path,
            "source_hash": job.source_hash,
            "cache_key": job.cache_key,
            "requested_mode": job.requested_mode,
            "renderer_pack_id": job.renderer_pack_id,
            "renderer_pack_version": job.renderer_pack_version,
            "status": job.status.value,
            "progress": job.progress,
            "error_code": job.error_code,
            "public_summary": job.public_summary,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
        }
        with self._session.write() as connection:
            changed = connection.execute(
                update(file_render_jobs)
                .where(
                    file_render_jobs.c.tenant_id == self._tenant_id,
                    file_render_jobs.c.id == str(job.id),
                )
                .values(
                    **{
                        key: value
                        for key, value in values.items()
                        if key not in {"tenant_id", "id", "created_at"}
                    }
                )
            ).rowcount
            if not changed:
                connection.execute(insert(file_render_jobs).values(**values))

    def get_presentation_for_job(self, job_id: UUID) -> FilePresentation | None:
        with self._session.read() as connection:
            row = (
                connection.execute(
                    select(file_presentations).where(
                        file_presentations.c.tenant_id == self._tenant_id,
                        file_presentations.c.job_id == str(job_id),
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            asset_rows = (
                connection.execute(
                    select(derived_assets).where(
                        derived_assets.c.tenant_id == self._tenant_id,
                        derived_assets.c.presentation_id == row["id"],
                    )
                )
                .mappings()
                .all()
            )
        return self._presentation(row, asset_rows)

    def save_presentation(self, presentation: FilePresentation) -> None:
        with self._session.write() as connection:
            connection.execute(
                insert(file_presentations).values(
                    tenant_id=self._tenant_id,
                    id=str(presentation.id),
                    job_id=str(presentation.job_id),
                    workspace_id=str(presentation.workspace_id),
                    version_id=str(presentation.version_id),
                    file_set_id=str(presentation.file_set_id),
                    source_path=presentation.source_path,
                    source_hash=presentation.source_hash,
                    renderer=presentation.renderer,
                    fidelity=presentation.fidelity.value,
                    status=presentation.status.value,
                    capabilities=list(presentation.capabilities),
                    created_at=presentation.created_at,
                )
            )
            for asset in presentation.assets:
                connection.execute(
                    insert(derived_assets).values(
                        tenant_id=self._tenant_id,
                        id=str(asset.id),
                        presentation_id=str(presentation.id),
                        role=asset.role,
                        media_type=asset.media_type,
                        content_hash=asset.content_hash,
                        byte_length=asset.byte_length,
                        storage_key=asset.storage_key,
                        metadata=dict(asset.metadata),
                    )
                )

    def list_packs(self) -> tuple[RendererPackRecord, ...]:
        with self._session.read() as connection:
            rows = (
                connection.execute(
                    select(renderer_packs)
                    .where(renderer_packs.c.tenant_id == self._tenant_id)
                    .order_by(renderer_packs.c.id, renderer_packs.c.version)
                )
                .mappings()
                .all()
            )
        return tuple(self._pack(row) for row in rows)

    def get_pack(self, pack_id: str, version: str) -> RendererPackRecord | None:
        with self._session.read() as connection:
            row = (
                connection.execute(
                    select(renderer_packs).where(
                        renderer_packs.c.tenant_id == self._tenant_id,
                        renderer_packs.c.id == pack_id,
                        renderer_packs.c.version == version,
                    )
                )
                .mappings()
                .first()
            )
        return self._pack(row) if row else None

    def save_pack(self, pack: RendererPackRecord) -> None:
        values = {
            "tenant_id": self._tenant_id,
            "id": pack.manifest.id,
            "version": pack.manifest.version,
            "platform": pack.manifest.platform,
            "manifest": {
                "id": pack.manifest.id,
                "version": pack.manifest.version,
                "platform": pack.manifest.platform,
                "input_media_types": list(pack.manifest.input_media_types),
                "output_media_types": list(pack.manifest.output_media_types),
                "features": list(pack.manifest.features),
                "limits": dict(pack.manifest.limits),
                "license": pack.manifest.license,
                "sandbox": pack.manifest.sandbox,
                "reproducible": pack.manifest.reproducible,
                "entrypoint": pack.manifest.entrypoint,
                "payload_sha256": pack.manifest.payload_sha256,
            },
            "payload_hash": pack.manifest.payload_sha256,
            "install_path": str(pack.install_path),
            "health": pack.health,
            "installed_at": datetime.fromisoformat(pack.installed_at),
        }
        with self._session.write() as connection:
            connection.execute(insert(renderer_packs).values(**values))

    def delete_pack(self, pack_id: str, version: str) -> bool:
        with self._session.write() as connection:
            return bool(
                connection.execute(
                    delete(renderer_packs).where(
                        renderer_packs.c.tenant_id == self._tenant_id,
                        renderer_packs.c.id == pack_id,
                        renderer_packs.c.version == version,
                    )
                ).rowcount
            )

    def list_annotations(
        self, *, workspace_id: UUID, version_id: UUID, file_set_id: UUID
    ) -> AnnotationDocument | None:
        with self._session.read() as connection:
            row = (
                connection.execute(
                    select(annotation_documents).where(
                        annotation_documents.c.tenant_id == self._tenant_id,
                        annotation_documents.c.workspace_id == str(workspace_id),
                        annotation_documents.c.version_id == str(version_id),
                        annotation_documents.c.file_set_id == str(file_set_id),
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        return AnnotationDocument(
            id=UUID(row["id"]),
            workspace_id=UUID(row["workspace_id"]),
            version_id=UUID(row["version_id"]),
            file_set_id=UUID(row["file_set_id"]),
            source_hash=row["source_hash"],
            revision=int(row["revision"]),
            annotations=tuple(dict(item) for item in row["annotations"]),
            created_at=_datetime(row["created_at"]),
            updated_at=_datetime(row["updated_at"]),
        )

    def save_annotations(self, document: AnnotationDocument) -> None:
        values = {
            "tenant_id": self._tenant_id,
            "id": str(document.id),
            "workspace_id": str(document.workspace_id),
            "version_id": str(document.version_id),
            "file_set_id": str(document.file_set_id),
            "source_hash": document.source_hash,
            "revision": document.revision,
            "annotations": list(document.annotations),
            "created_at": document.created_at,
            "updated_at": document.updated_at,
        }
        with self._session.write() as connection:
            changed = connection.execute(
                update(annotation_documents)
                .where(
                    annotation_documents.c.tenant_id == self._tenant_id,
                    annotation_documents.c.id == str(document.id),
                    annotation_documents.c.revision == document.revision - 1,
                )
                .values(
                    revision=document.revision,
                    annotations=list(document.annotations),
                    updated_at=document.updated_at,
                )
            ).rowcount
            if not changed:
                existing = connection.execute(
                    select(annotation_documents.c.id).where(
                        annotation_documents.c.tenant_id == self._tenant_id,
                        annotation_documents.c.id == str(document.id),
                    )
                ).first()
                if existing is not None:
                    raise VersionConflictError("Annotation revision changed concurrently")
                try:
                    connection.execute(insert(annotation_documents).values(**values))
                except IntegrityError as error:
                    raise VersionConflictError(
                        "Annotation revision changed concurrently"
                    ) from error

    def get_edit_recipe(self, recipe_id: UUID) -> EditRecipe | None:
        with self._session.read() as connection:
            row = (
                connection.execute(
                    select(edit_recipes).where(
                        edit_recipes.c.tenant_id == self._tenant_id,
                        edit_recipes.c.id == str(recipe_id),
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        return EditRecipe(
            id=UUID(row["id"]),
            workspace_id=UUID(row["workspace_id"]),
            version_id=UUID(row["version_id"]),
            file_set_id=UUID(row["file_set_id"]),
            source_hash=row["source_hash"],
            kind=row["kind"],
            operations=tuple(dict(item) for item in row["operations"]),
            status=EditRecipeStatus(row["status"]),
            revision=int(row["revision"]),
            created_at=_datetime(row["created_at"]),
            updated_at=_datetime(row["updated_at"]),
        )

    def save_edit_recipe(self, recipe: EditRecipe) -> None:
        values = {
            "tenant_id": self._tenant_id,
            "id": str(recipe.id),
            "workspace_id": str(recipe.workspace_id),
            "version_id": str(recipe.version_id),
            "file_set_id": str(recipe.file_set_id),
            "source_hash": recipe.source_hash,
            "kind": recipe.kind,
            "operations": list(recipe.operations),
            "status": recipe.status.value,
            "revision": recipe.revision,
            "created_at": recipe.created_at,
            "updated_at": recipe.updated_at,
        }
        with self._session.write() as connection:
            changed = connection.execute(
                update(edit_recipes)
                .where(
                    edit_recipes.c.tenant_id == self._tenant_id,
                    edit_recipes.c.id == str(recipe.id),
                    edit_recipes.c.revision == recipe.revision - 1,
                )
                .values(
                    operations=list(recipe.operations),
                    status=recipe.status.value,
                    revision=recipe.revision,
                    updated_at=recipe.updated_at,
                )
            ).rowcount
            if not changed:
                existing = connection.execute(
                    select(edit_recipes.c.id).where(
                        edit_recipes.c.tenant_id == self._tenant_id,
                        edit_recipes.c.id == str(recipe.id),
                    )
                ).first()
                if existing is not None:
                    raise VersionConflictError("Edit Recipe revision changed concurrently")
                connection.execute(insert(edit_recipes).values(**values))

    def save_selection(self, selection: SelectionReference) -> None:
        with self._session.write() as connection:
            connection.execute(
                insert(selection_references).values(
                    tenant_id=self._tenant_id,
                    id=str(selection.id),
                    workspace_id=str(selection.workspace_id),
                    version_id=str(selection.version_id),
                    file_set_id=str(selection.file_set_id),
                    source_path=selection.source_path,
                    source_hash=selection.source_hash,
                    viewer_kind=selection.viewer_kind,
                    locator_kind=selection.locator_kind,
                    locator=selection.locator,
                    created_at=selection.created_at,
                )
            )

    @staticmethod
    def _job(row: RowMapping) -> FileRenderJob:
        return FileRenderJob(
            id=UUID(row["id"]),
            workspace_id=UUID(row["workspace_id"]),
            version_id=UUID(row["version_id"]),
            file_set_id=UUID(row["file_set_id"]),
            source_path=row["source_path"],
            source_hash=row["source_hash"],
            cache_key=row["cache_key"],
            requested_mode=row["requested_mode"],
            renderer_pack_id=row["renderer_pack_id"],
            renderer_pack_version=row["renderer_pack_version"],
            status=RenderJobStatus(row["status"]),
            progress=int(row["progress"]),
            error_code=row["error_code"],
            public_summary=row["public_summary"],
            created_at=_datetime(row["created_at"]),
            updated_at=_datetime(row["updated_at"]),
        )

    @staticmethod
    def _presentation(row: RowMapping, asset_rows: list[RowMapping]) -> FilePresentation:
        assets = tuple(
            DerivedAsset(
                id=UUID(asset["id"]),
                presentation_id=UUID(asset["presentation_id"]),
                role=asset["role"],
                media_type=asset["media_type"],
                content_hash=asset["content_hash"],
                byte_length=int(asset["byte_length"]),
                storage_key=asset["storage_key"],
                metadata=MappingProxyType(dict(asset["metadata"])),
            )
            for asset in asset_rows
        )
        return FilePresentation(
            id=UUID(row["id"]),
            job_id=UUID(row["job_id"]),
            workspace_id=UUID(row["workspace_id"]),
            version_id=UUID(row["version_id"]),
            file_set_id=UUID(row["file_set_id"]),
            source_path=row["source_path"],
            source_hash=row["source_hash"],
            renderer=row["renderer"],
            fidelity=PresentationFidelity(row["fidelity"]),
            status=RenderJobStatus(row["status"]),
            capabilities=tuple(row["capabilities"]),
            assets=assets,
            created_at=_datetime(row["created_at"]),
        )

    @staticmethod
    def _pack(row: RowMapping) -> RendererPackRecord:
        payload = dict(row["manifest"])
        manifest = RendererPackManifest(
            id=payload["id"],
            version=payload["version"],
            platform=payload["platform"],
            input_media_types=tuple(payload["input_media_types"]),
            output_media_types=tuple(payload["output_media_types"]),
            features=tuple(payload["features"]),
            limits=dict(payload["limits"]),
            license=payload["license"],
            sandbox=payload["sandbox"],
            reproducible=bool(payload["reproducible"]),
            entrypoint=payload["entrypoint"],
            payload_sha256=payload["payload_sha256"],
        )
        return RendererPackRecord(
            manifest=manifest,
            install_path=Path(row["install_path"]),
            health=row["health"],
            installed_at=_datetime(row["installed_at"]).isoformat(),
        )


__all__ = ["SqlAlchemyPresentationRepository"]
