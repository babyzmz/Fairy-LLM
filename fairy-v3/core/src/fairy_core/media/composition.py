from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fairy_core.commanding.registry import ToolRegistry
from fairy_core.commanding.settings import ExecutionPolicyResolver
from fairy_core.media.application import MediaApplication
from fairy_core.media.ports import MediaProvider
from fairy_core.media.service import MediaService, UnavailableMediaService
from fairy_core.media.staging import MediaStagingStore
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.workspace.ports import WorkspaceProvisioner


@dataclass(frozen=True, slots=True)
class MediaComposition:
    provider: MediaProvider | None
    application: MediaApplication | None
    service: MediaService | UnavailableMediaService


def build_media_composition(
    *,
    unit_of_work_factory: CoreUnitOfWorkFactory,
    scope_resolver: Any,
    registry: ToolRegistry,
    execution_policy: ExecutionPolicyResolver,
    provider: MediaProvider | None,
    staging: MediaStagingStore | None,
    workspaces: WorkspaceProvisioner | None,
) -> MediaComposition:
    dependencies = (provider, staging, workspaces)
    if any(value is not None for value in dependencies) and not all(
        value is not None for value in dependencies
    ):
        raise ValueError(
            "media provider, staging store, and workspace provisioner must be configured together"
        )
    if provider is None or staging is None or workspaces is None:
        return MediaComposition(
            provider=None,
            application=None,
            service=UnavailableMediaService(),
        )
    application = MediaApplication(
        unit_of_work_factory=unit_of_work_factory,
        scope_resolver=scope_resolver,
        provider=provider,
        workspaces=workspaces,
        staging=staging,
    )
    return MediaComposition(
        provider=provider,
        application=application,
        service=MediaService(
            application=application,
            unit_of_work_factory=unit_of_work_factory,
            registry=registry,
            execution_policy=execution_policy,
            scope_resolver=scope_resolver,
        ),
    )


__all__ = ["MediaComposition", "build_media_composition"]
