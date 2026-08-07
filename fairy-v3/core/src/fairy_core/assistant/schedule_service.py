from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, cast

from pydantic import BaseModel

from fairy_core.application.core import CoreApplication
from fairy_core.assistant.background_tasks import AssistantBackgroundTaskProjection
from fairy_core.assistant.ledger import AssistantLedgerApplication
from fairy_core.assistant.schedule_application import AssistantScheduleApplication
from fairy_core.assistant.schedule_models import AssistantSchedule
from fairy_core.assistant.schedule_trigger import (
    AssistantScheduleAttentionRequired,
    AssistantScheduleTriggerService,
)
from fairy_core.assistant.schedule_turn_dispatcher import AssistantScheduledTurnDispatcher
from fairy_core.assistant.turn_scheduler import AssistantTurnScheduler
from fairy_core.assistant.turn_selection import resolve_turn_model_source
from fairy_core.contracts.assistant_schedules import (
    AssistantBackgroundTaskListInput,
    AssistantScheduleCreateInput,
    AssistantScheduleIdInput,
    AssistantScheduleListInput,
    AssistantScheduleRevisionInput,
    AssistantScheduleRunNowInput,
    AssistantScheduleUpdateInput,
)
from fairy_core.domain.errors import VersionConflictError
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.providers import ProviderRegistry
from fairy_core.providers.models import ProviderHealthStatus
from fairy_core.providers.ports import ProviderUnavailableError


class AssistantScheduleService:
    """Own the local Schedule RPC surface and its trigger lifecycle."""

    def __init__(
        self,
        *,
        application: CoreApplication,
        ledger: AssistantLedgerApplication,
        scheduler: AssistantTurnScheduler,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        providers: ProviderRegistry,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._providers = providers
        dispatcher = AssistantScheduledTurnDispatcher(
            application=application,
            ledger=ledger,
            scheduler=scheduler,
            unit_of_work_factory=unit_of_work_factory,
            model_resolver=self._resolve_model,
        )
        self._trigger = AssistantScheduleTriggerService(
            unit_of_work_factory=unit_of_work_factory,
            runner=dispatcher,
            autostart=False,
        )
        self._application = AssistantScheduleApplication(
            unit_of_work_factory=unit_of_work_factory,
            trigger=self._trigger,
        )
        self._background_tasks = AssistantBackgroundTaskProjection(unit_of_work_factory)

    @property
    def handlers(self) -> Mapping[str, Callable[[BaseModel], Any]]:
        return {
            "assistant.background_tasks.list": lambda request: self._background_tasks.list(
                cast(AssistantBackgroundTaskListInput, request)
            ),
            "assistant.schedules.cancel": lambda request: self._application.cancel(
                cast(AssistantScheduleRevisionInput, request).schedule_id,
                expected_revision=cast(AssistantScheduleRevisionInput, request).expected_revision,
            ),
            "assistant.schedules.create": self._create,
            "assistant.schedules.get": lambda request: self._application.get(
                cast(AssistantScheduleIdInput, request).schedule_id
            ),
            "assistant.schedules.list": lambda request: self._application.list(
                cast(AssistantScheduleListInput, request)
            ),
            "assistant.schedules.pause": lambda request: self._application.pause(
                cast(AssistantScheduleRevisionInput, request).schedule_id,
                expected_revision=cast(AssistantScheduleRevisionInput, request).expected_revision,
            ),
            "assistant.schedules.resume": lambda request: self._application.resume(
                cast(AssistantScheduleRevisionInput, request).schedule_id,
                expected_revision=cast(AssistantScheduleRevisionInput, request).expected_revision,
            ),
            "assistant.schedules.run_now": lambda request: self._application.run_now(
                cast(AssistantScheduleRunNowInput, request)
            ),
            "assistant.schedules.update": lambda request: self._application.update(
                cast(AssistantScheduleUpdateInput, request)
            ),
        }

    def start(self) -> None:
        self._trigger.start()

    def close(self) -> None:
        self._trigger.close()

    def _create(self, request: BaseModel) -> AssistantSchedule:
        validated = cast(AssistantScheduleCreateInput, request)
        requested = validated.model_selection
        profile_id, model_selection = resolve_turn_model_source(
            requested_mode=requested.mode if requested is not None else None,
            requested_model_id=requested.model_id if requested is not None else None,
            requested_revision=requested.revision if requested is not None else None,
            legacy_profile_id=validated.profile_id,
            has_attachments=False,
            unit_of_work_factory=self._unit_of_work_factory,
            providers=self._providers,
        )
        return self._application.create(
            validated,
            profile_id=profile_id,
            model_selection=model_selection,
        )

    def _resolve_model(self, schedule: AssistantSchedule) -> str:
        try:
            requested = schedule.model_selection
            profile_id, selection = resolve_turn_model_source(
                requested_mode=requested.mode if requested is not None else None,
                requested_model_id=requested.model_id if requested is not None else None,
                requested_revision=requested.revision if requested is not None else None,
                legacy_profile_id=schedule.profile_id,
                has_attachments=False,
                unit_of_work_factory=self._unit_of_work_factory,
                providers=self._providers,
            )
            if selection != schedule.model_selection:
                raise ProviderUnavailableError("scheduled model selection changed")
            health = self._providers.health(profile_id)[0]
            if health.status is ProviderHealthStatus.UNAVAILABLE:
                raise ProviderUnavailableError(
                    health.error_code or "scheduled provider unavailable"
                )
            return profile_id
        except (ProviderUnavailableError, VersionConflictError, KeyError) as error:
            raise AssistantScheduleAttentionRequired(
                "PROVIDER_OR_CREDENTIAL_INVALID",
                "The scheduled model provider or credential needs confirmation.",
            ) from error


__all__ = ["AssistantScheduleService"]
