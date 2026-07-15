from __future__ import annotations

from fairy_core.assistant.routing import DEEPSEEK_MODEL_ID
from fairy_core.domain.errors import CapabilityUnavailableError, VersionConflictError
from fairy_core.model_catalog.models import (
    ModelAvailability,
    ModelEndpointKind,
    ModelSelectionMode,
    ModelSelectionSnapshot,
)
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.providers import ProviderCapability, ProviderRegistry


def resolve_turn_model_source(
    *,
    requested_mode: ModelSelectionMode | None,
    requested_model_id: str | None,
    requested_revision: int | None,
    legacy_profile_id: str | None,
    has_attachments: bool,
    unit_of_work_factory: CoreUnitOfWorkFactory,
    providers: ProviderRegistry,
) -> tuple[str, ModelSelectionSnapshot | None]:
    selection: ModelSelectionSnapshot | None = None
    profile = None
    if requested_mode is not None:
        if requested_revision is None:
            raise ValueError("model selection revision is required")
        with unit_of_work_factory() as unit_of_work:
            current = unit_of_work.model_catalog.get_selection()
            catalog = unit_of_work.model_catalog.get_catalog()
        if (
            current.mode is not requested_mode
            or current.model_id != requested_model_id
            or current.revision != requested_revision
        ):
            raise VersionConflictError("model selection changed before Turn creation")
        selection = ModelSelectionSnapshot.from_preference(current)
        selected_model_id = (
            DEEPSEEK_MODEL_ID if current.mode is ModelSelectionMode.AUTO else current.model_id
        )
        assert selected_model_id is not None
        if catalog is not None:
            entry = next(
                (item for item in catalog.entries if item.model_id == selected_model_id),
                None,
            )
            if entry is None or entry.endpoint_kind is not ModelEndpointKind.CHAT:
                raise CapabilityUnavailableError(
                    "selected model requires a specialized media endpoint"
                )
            if entry.availability is ModelAvailability.UNAVAILABLE:
                raise CapabilityUnavailableError("selected model is unavailable")
        profile = providers.profile_for_model(selected_model_id)
        profile_id = profile.id
    else:
        if legacy_profile_id is None:
            raise ValueError("legacy model profile is required")
        profile_id = legacy_profile_id
        if has_attachments:
            profile = providers.profile(profile_id)

    if has_attachments and (selection is None or selection.mode is ModelSelectionMode.MANUAL):
        assert profile is not None
        if ProviderCapability.VISION not in profile.capabilities:
            raise CapabilityUnavailableError(
                f"provider profile {profile.id!r} lacks vision capability"
            )
    return profile_id, selection


__all__ = ["resolve_turn_model_source"]
