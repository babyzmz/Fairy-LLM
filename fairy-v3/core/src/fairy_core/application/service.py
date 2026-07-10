from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, cast
from weakref import finalize

from pydantic import BaseModel, ValidationError

from fairy_core.application.core import CoreApplication
from fairy_core.commanding import EventVisibility
from fairy_core.commanding.registry import ToolRegistry
from fairy_core.contracts.methods import CORE_METHODS, EventSubscribeInput
from fairy_core.contracts.models import (
    ApprovalDecisionInput,
    CapabilityRequest,
    ChangesetProposal,
    ConversationCreate,
    ProjectCreate,
    ProjectIdInput,
    ProjectImport,
    TaskCreate,
    TaskIdInput,
    VersionAcceptInput,
    VersionIdInput,
)
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory


class CoreMethodNotFoundError(LookupError):
    def __init__(self, method: str) -> None:
        self.method = method
        super().__init__(f"Core method not found: {method}")


class CoreResponseValidationError(RuntimeError):
    def __init__(self, method: str, error: ValidationError) -> None:
        self.method = method
        self.validation_error = error
        super().__init__(f"Core method returned an invalid response: {method}")


class CoreService:
    """Transport-independent validation and application facade."""

    def __init__(
        self,
        application: CoreApplication,
        *,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        registry: ToolRegistry,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        self._application = application
        self._unit_of_work_factory = unit_of_work_factory
        self._registry = registry
        self._finalizer = finalize(self, on_close) if on_close is not None else None
        self._handlers: Mapping[str, Callable[[BaseModel], Any]] = {
            "approvals.decide": self._decide_approval,
            "capabilities.get": self._get_capabilities,
            "changesets.propose": self._propose_changeset,
            "conversations.create": self._create_conversation,
            "events.subscribe": self._subscribe_events,
            "health": self._health,
            "projects.create": self._create_project,
            "projects.get": self._get_project,
            "projects.import": self._import_project,
            "tasks.create": self._create_task,
            "tasks.get": self._get_task,
            "tasks.review": self._review_task,
            "versions.accept": self._accept_version,
            "versions.discard": self._discard_version,
            "versions.get": self._get_version,
        }
        if self._handlers.keys() != CORE_METHODS.keys():
            raise RuntimeError("Core service handlers do not match the public method catalog")

    def close(self) -> None:
        if self._finalizer is not None:
            self._finalizer()

    def invoke(self, method: str, params: Mapping[str, Any]) -> Any:
        definition = CORE_METHODS.get(method)
        if definition is None:
            raise CoreMethodNotFoundError(method)
        request = definition.request_model.model_validate(dict(params))
        result = self._handlers[method](request)
        try:
            response = definition.response_model.model_validate(result)
        except ValidationError as error:
            raise CoreResponseValidationError(method, error) from error
        return response.model_dump(mode="json")

    @staticmethod
    def _health(_request: BaseModel) -> dict[str, str]:
        return {
            "status": "ok",
            "service": "fairy-core",
            "protocol": "core-service-v1",
        }

    def _create_project(self, request: BaseModel) -> Any:
        validated = cast(ProjectCreate, request)
        return self._application.create_project(
            name=validated.name,
            residency=validated.residency,
        )

    def _import_project(self, request: BaseModel) -> Any:
        validated = cast(ProjectImport, request)
        return self._application.create_project(
            name=validated.name,
            residency=validated.residency,
            source=validated.source_path,
        )

    def _get_project(self, request: BaseModel) -> Any:
        validated = cast(ProjectIdInput, request)
        return self._application.get_project(validated.project_id)

    def _create_conversation(self, request: BaseModel) -> Any:
        validated = cast(ConversationCreate, request)
        return self._application.create_conversation(
            project_id=validated.project_id,
            workspace_type=validated.workspace_type,
        )

    def _create_task(self, request: BaseModel) -> Any:
        return self._application.create_task(cast(TaskCreate, request))

    def _get_task(self, request: BaseModel) -> Any:
        return self._application.get_task(cast(TaskIdInput, request).task_id)

    def _review_task(self, request: BaseModel) -> Any:
        return self._application.review_task(cast(TaskIdInput, request).task_id)

    def _propose_changeset(self, request: BaseModel) -> Any:
        return self._application.propose_changeset(cast(ChangesetProposal, request))

    def _decide_approval(self, request: BaseModel) -> Any:
        validated = cast(ApprovalDecisionInput, request)
        return self._application.decide_approval(
            approval_id=validated.approval_id,
            approved=validated.approved,
            decided_by=validated.decided_by,
        )

    def _get_version(self, request: BaseModel) -> Any:
        return self._application.get_version(cast(VersionIdInput, request).version_id)

    def _accept_version(self, request: BaseModel) -> Any:
        validated = cast(VersionAcceptInput, request)
        return self._application.accept_task_version(
            task_id=validated.task_id,
            expected_project_revision=validated.expected_project_revision,
            user_confirmed=validated.user_confirmed,
        )

    def _discard_version(self, request: BaseModel) -> Any:
        return self._application.discard_task_version(cast(TaskIdInput, request).task_id)

    def _get_capabilities(self, request: BaseModel) -> dict[str, Any]:
        validated = cast(CapabilityRequest, request)
        return {
            "profile": validated.profile,
            "operations": self._registry.capability_manifest(
                profile=validated.profile,
                sandbox_healthy=validated.sandbox_healthy,
                overrides=validated.overrides,
            ),
            "sandbox_healthy": validated.sandbox_healthy,
            "command_metadata": self._registry.frontend_metadata(),
            "schema_version": 1,
        }

    def _subscribe_events(self, request: BaseModel) -> dict[str, Any]:
        validated = cast(EventSubscribeInput, request)
        with self._unit_of_work_factory() as unit_of_work:
            events = unit_of_work.commands.events_after(
                cursor=validated.cursor,
                allowed_visibilities={EventVisibility.USER, EventVisibility.DEVELOPER},
            )
        return {
            "items": events,
            "next_cursor": events[-1].cursor if events else validated.cursor,
        }


__all__ = ["CoreMethodNotFoundError", "CoreResponseValidationError", "CoreService"]
