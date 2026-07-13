from __future__ import annotations

from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, model_validator

from fairy_core.contracts.common import ContractModel, ExecutionTarget
from fairy_core.domain.execution import (
    PreviewHealth,
    PreviewStatus,
    PreviewVisibility,
    RuntimeHealth,
    RuntimeKind,
    RuntimeStatus,
)


class RuntimeModel(ContractModel):
    id: UUID
    project_id: UUID | None
    workspace_id: UUID
    conversation_id: UUID
    task_id: UUID
    version_id: UUID
    project_root: Path
    execution_target: ExecutionTarget
    kind: RuntimeKind
    executor: str = Field(min_length=1, max_length=128)
    executor_handle: str | None
    port: int | None = Field(default=None, ge=1, le=65_535)
    status: RuntimeStatus
    health: RuntimeHealth
    error_code: str | None
    idempotency_key: str = Field(min_length=1, max_length=512)
    revision: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def require_consistent_runtime_state(self) -> RuntimeModel:
        active = self.status in {RuntimeStatus.RUNNING, RuntimeStatus.STOPPING}
        paired_endpoint = self.executor_handle is not None and self.port is not None
        mismatched_endpoint = (self.executor_handle is None) != (self.port is None)
        inactive = self.status in {
            RuntimeStatus.CREATED,
            RuntimeStatus.STARTING,
            RuntimeStatus.STOPPED,
        }
        if (
            mismatched_endpoint
            or (active and not paired_endpoint)
            or (inactive and paired_endpoint)
        ):
            raise ValueError("active Runtime requires executor handle and port")
        if self.kind is RuntimeKind.STATIC_SITE and (
            self.execution_target is not ExecutionTarget.LOCAL or self.project_id is None
        ):
            raise ValueError("static Runtime requires a local Project Version")
        return self


class PreviewModel(ContractModel):
    id: UUID
    project_id: UUID | None
    workspace_id: UUID
    conversation_id: UUID
    task_id: UUID
    version_id: UUID
    runtime_id: UUID
    project_root: Path
    execution_target: ExecutionTarget
    url: str | None
    visibility: PreviewVisibility
    status: PreviewStatus
    health: PreviewHealth
    error_code: str | None
    idempotency_key: str = Field(min_length=1, max_length=512)
    revision: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def require_scoped_preview_url(self) -> PreviewModel:
        requires_url = self.status in {PreviewStatus.READY, PreviewStatus.STOPPING}
        forbids_url = self.status in {
            PreviewStatus.CREATED,
            PreviewStatus.STARTING,
            PreviewStatus.STOPPED,
            PreviewStatus.FAILED,
        }
        if requires_url and self.url is None:
            raise ValueError("active Preview requires a URL")
        if forbids_url and self.url is not None:
            raise ValueError("inactive Preview cannot retain a URL")
        if self.url is None:
            return self
        try:
            parsed = urlsplit(self.url)
            port = parsed.port
        except ValueError as error:
            raise ValueError("Preview URL is invalid") from error
        common_invalid = (
            parsed.username is not None
            or parsed.password is not None
            or parsed.query != ""
            or parsed.fragment != ""
            or not parsed.path.startswith("/")
        )
        if self.execution_target is ExecutionTarget.LOCAL:
            invalid = (
                parsed.scheme != "http"
                or parsed.hostname != "127.0.0.1"
                or port is None
                or common_invalid
            )
        else:
            invalid = parsed.scheme != "https" or parsed.hostname is None or common_invalid
        if invalid:
            raise ValueError("Preview URL does not match its execution target")
        return self


__all__ = ["PreviewModel", "RuntimeModel"]
