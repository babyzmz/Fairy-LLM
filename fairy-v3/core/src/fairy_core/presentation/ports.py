from __future__ import annotations

from typing import Protocol
from uuid import UUID

from fairy_core.presentation.models import FilePresentation, FileRenderJob
from fairy_core.presentation.packs import RendererPackRecord


class PresentationRepository(Protocol):
    def get_job(self, job_id: UUID) -> FileRenderJob | None: ...

    def find_job_by_cache_key(
        self, *, workspace_id: UUID, version_id: UUID, cache_key: str
    ) -> FileRenderJob | None: ...

    def save_job(self, job: FileRenderJob) -> None: ...

    def get_presentation_for_job(self, job_id: UUID) -> FilePresentation | None: ...

    def save_presentation(self, presentation: FilePresentation) -> None: ...

    def list_packs(self) -> tuple[RendererPackRecord, ...]: ...

    def get_pack(self, pack_id: str, version: str) -> RendererPackRecord | None: ...

    def save_pack(self, pack: RendererPackRecord) -> None: ...

    def delete_pack(self, pack_id: str, version: str) -> bool: ...


__all__ = ["PresentationRepository"]
