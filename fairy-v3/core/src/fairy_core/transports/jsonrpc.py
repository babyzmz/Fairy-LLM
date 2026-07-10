from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from fairy_core.application.core import CoreApplication
from fairy_core.commanding import CommandLedger, EventVisibility
from fairy_core.commanding.registry import ToolRegistry
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
from fairy_core.domain.errors import DomainError
from fairy_core.workspace.worker_transport import WorkerRpcError

_PUBLIC_METHOD_NAMES = frozenset(
    {
        "approvals.decide",
        "capabilities.get",
        "changesets.propose",
        "conversations.create",
        "events.subscribe",
        "health",
        "projects.create",
        "projects.get",
        "projects.import",
        "tasks.create",
        "tasks.get",
        "tasks.review",
        "versions.accept",
        "versions.discard",
        "versions.get",
    }
)


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _EventSubscribeParams(_Params):
    cursor: int = Field(default=0, ge=0)


class JsonRpcDispatcher:
    def __init__(
        self,
        application: CoreApplication,
        *,
        ledger: CommandLedger,
        registry: ToolRegistry,
    ) -> None:
        self._application = application
        self._ledger = ledger
        self._registry = registry
        self._methods: dict[str, Callable[[dict[str, Any]], Any]] = {
            "health": self._health,
            "projects.create": self._create_project,
            "projects.import": self._import_project,
            "projects.get": self._get_project,
            "conversations.create": self._create_conversation,
            "tasks.create": self._create_task,
            "tasks.get": self._get_task,
            "tasks.review": self._review_task,
            "changesets.propose": self._propose_changeset,
            "approvals.decide": self._decide_approval,
            "versions.get": self._get_version,
            "versions.accept": self._accept_version,
            "versions.discard": self._discard_version,
            "capabilities.get": self._get_capabilities,
            "events.subscribe": self._subscribe_events,
        }
        if self._methods.keys() != _PUBLIC_METHOD_NAMES:
            raise RuntimeError("JSON-RPC handlers do not match the public method contract")

    @classmethod
    def method_names(cls) -> frozenset[str]:
        return _PUBLIC_METHOD_NAMES

    def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        request_id = request.get("id")
        method_name = str(request.get("method") or "")
        method = self._methods.get(method_name)
        if method is None:
            return self._error(
                request_id,
                code=-32601,
                message="Method not found",
                data={"method": method_name},
            )
        params = request.get("params", {})
        if not isinstance(params, dict):
            return self._error(
                request_id,
                code=-32602,
                message="Invalid params",
                data={"error_code": "INVALID_PARAMS"},
            )
        try:
            result = method(params)
        except ValidationError as exc:
            return self._error(
                request_id,
                code=-32602,
                message="Invalid params",
                data={"error_code": "INVALID_PARAMS", "details": exc.errors(include_url=False)},
            )
        except KeyError as exc:
            return self._error(
                request_id,
                code=-32004,
                message="Resource not found",
                data={"error_code": "NOT_FOUND", "details": str(exc)},
            )
        except DomainError as exc:
            return self._error(
                request_id,
                code=-32000,
                message=str(exc),
                data={"error_code": getattr(exc, "code", "DOMAIN_ERROR")},
            )
        except WorkerRpcError as exc:
            return self._error(
                request_id,
                code=-32050,
                message=str(exc),
                data={"error_code": exc.error_code},
            )
        except ValueError as exc:
            return self._error(
                request_id,
                code=-32602,
                message="Invalid params",
                data={"error_code": "INVALID_PARAMS", "details": str(exc)},
            )
        return {"jsonrpc": "2.0", "id": request_id, "result": _json_value(result)}

    @staticmethod
    def _error(
        request_id: object,
        *,
        code: int,
        message: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message, "data": data},
        }

    @staticmethod
    def _health(_params: dict[str, Any]) -> dict[str, Any]:
        return {"status": "ok", "service": "fairy-core", "protocol": "jsonrpc-2.0"}

    def _create_project(self, params: dict[str, Any]) -> Any:
        validated = ProjectCreate.model_validate(params)
        return self._application.create_project(
            name=validated.name,
            residency=validated.residency,
        )

    def _import_project(self, params: dict[str, Any]) -> Any:
        validated = ProjectImport.model_validate(params)
        return self._application.create_project(
            name=validated.name,
            residency=validated.residency,
            source=validated.source_path,
        )

    def _get_project(self, params: dict[str, Any]) -> Any:
        validated = ProjectIdInput.model_validate(params)
        return self._application.get_project(validated.project_id)

    def _create_conversation(self, params: dict[str, Any]) -> Any:
        validated = ConversationCreate.model_validate(params)
        return self._application.create_conversation(
            project_id=validated.project_id,
            workspace_type=validated.workspace_type,
        )

    def _create_task(self, params: dict[str, Any]) -> Any:
        return self._application.create_task(TaskCreate.model_validate(params))

    def _get_task(self, params: dict[str, Any]) -> Any:
        validated = TaskIdInput.model_validate(params)
        return self._application.get_task(validated.task_id)

    def _review_task(self, params: dict[str, Any]) -> Any:
        validated = TaskIdInput.model_validate(params)
        return self._application.review_task(validated.task_id)

    def _propose_changeset(self, params: dict[str, Any]) -> Any:
        return self._application.propose_changeset(ChangesetProposal.model_validate(params))

    def _decide_approval(self, params: dict[str, Any]) -> Any:
        validated = ApprovalDecisionInput.model_validate(params)
        return self._application.decide_approval(
            approval_id=validated.approval_id,
            approved=validated.approved,
            decided_by=validated.decided_by,
        )

    def _get_version(self, params: dict[str, Any]) -> Any:
        validated = VersionIdInput.model_validate(params)
        return self._application.get_version(validated.version_id)

    def _accept_version(self, params: dict[str, Any]) -> Any:
        validated = VersionAcceptInput.model_validate(params)
        return self._application.accept_task_version(
            task_id=validated.task_id,
            expected_project_revision=validated.expected_project_revision,
            user_confirmed=validated.user_confirmed,
        )

    def _discard_version(self, params: dict[str, Any]) -> Any:
        validated = TaskIdInput.model_validate(params)
        return self._application.discard_task_version(validated.task_id)

    def _get_capabilities(self, params: dict[str, Any]) -> Any:
        validated = CapabilityRequest.model_validate(params)
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

    def _subscribe_events(self, params: dict[str, Any]) -> Any:
        validated = _EventSubscribeParams.model_validate(params)
        events = self._ledger.events_after(
            cursor=validated.cursor,
            allowed_visibilities={EventVisibility.USER, EventVisibility.DEVELOPER},
        )
        return {
            "items": events,
            "next_cursor": events[-1].cursor if events else validated.cursor,
        }


def _json_value(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(item) for item in value]
    if isinstance(value, (UUID, Path)):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value
