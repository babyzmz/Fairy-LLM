from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.presentation.packs import (
    RendererPackInstaller,
    RendererPackRecord,
)


class RendererPackApplication:
    def __init__(
        self,
        *,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        installer: RendererPackInstaller | None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._installer = installer

    def list(self) -> tuple[RendererPackRecord, ...]:
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.presentations.list_packs()

    def install(self, bundle_path: Path, *, user_confirmed: bool) -> RendererPackRecord:
        if self._installer is None:
            raise RuntimeError("RENDERER_UNAVAILABLE: Renderer Pack trust store is not configured")
        installed = self._installer.verify_and_install(
            bundle_path,
            user_confirmed=user_confirmed,
        )
        record = RendererPackRecord(
            manifest=installed.manifest,
            install_path=installed.install_path,
            health="ready",
            installed_at=datetime.now(UTC).isoformat(),
        )
        with self._unit_of_work_factory() as unit_of_work:
            existing = unit_of_work.presentations.get_pack(
                record.manifest.id,
                record.manifest.version,
            )
            if existing is not None:
                return existing
            unit_of_work.presentations.save_pack(record)
            unit_of_work.commit()
        return record

    def remove(self, pack_id: str, version: str, *, user_confirmed: bool) -> bool:
        if not user_confirmed:
            raise PermissionError("Renderer Pack removal requires explicit user confirmation")
        with self._unit_of_work_factory() as unit_of_work:
            existing = unit_of_work.presentations.get_pack(pack_id, version)
            if existing is None:
                return False
            deleted = unit_of_work.presentations.delete_pack(pack_id, version)
            unit_of_work.commit()
        if deleted:
            shutil.rmtree(existing.install_path, ignore_errors=True)
        return deleted


__all__ = ["RendererPackApplication"]
