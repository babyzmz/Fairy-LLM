from __future__ import annotations

from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from fairy_core.commanding import CommandStatus


class _ActionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NotificationLevel(StrEnum):
    INFO = "info"
    WARNING = "warning"


class SystemSettings(StrEnum):
    DISPLAY = "display"
    MICROPHONE = "microphone"
    NOTIFICATIONS = "notifications"
    SOUND = "sound"


class OpenUrlAction(_ActionModel):
    type: Literal["open_url"]
    url: str = Field(min_length=1, max_length=2_048)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        if value != value.strip() or any(ord(character) < 32 for character in value):
            raise ValueError("system URL must be canonical text")
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("system URL must use HTTPS with a host")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("system URL cannot contain credentials")
        try:
            _ = parsed.port
        except ValueError as error:
            raise ValueError("system URL port is invalid") from error
        return value


class RevealPathAction(_ActionModel):
    type: Literal["reveal_path"]
    relative_path: str = Field(min_length=1, max_length=1_024)

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        if value != value.strip() or "\\" in value or "\x00" in value or ":" in value:
            raise ValueError("reveal path must use canonical relative POSIX syntax")
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("reveal path escapes the managed version")
        if value != "." and any(part in {"", "."} for part in value.split("/")):
            raise ValueError("reveal path must be normalized")
        return value


class CopyTextAction(_ActionModel):
    type: Literal["copy_text"]
    text: str = Field(min_length=1, max_length=32_768)

    @field_validator("text")
    @classmethod
    def reject_nul(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("clipboard text cannot contain NUL")
        return value


class NotifyAction(_ActionModel):
    type: Literal["notify"]
    title: str = Field(min_length=1, max_length=80)
    body: str = Field(min_length=1, max_length=240)
    level: NotificationLevel = NotificationLevel.INFO

    @field_validator("title", "body")
    @classmethod
    def reject_control_text(cls, value: str) -> str:
        if value != value.strip() or "\x00" in value:
            raise ValueError("notification text must be bounded canonical text")
        return value


class OpenSettingsAction(_ActionModel):
    type: Literal["open_settings"]
    page: SystemSettings


SystemAction = Annotated[
    OpenUrlAction | RevealPathAction | CopyTextAction | NotifyAction | OpenSettingsAction,
    Field(discriminator="type"),
]


class SystemActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: UUID
    action: SystemAction
    idempotency_key: str = Field(min_length=1, max_length=255)
    user_confirmed: bool = False

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        if value != value.strip() or any(ord(character) < 32 for character in value):
            raise ValueError("idempotency key must be canonical text")
        return value


class SystemActionWorkerResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action_type: Literal[
        "open_url",
        "reveal_path",
        "copy_text",
        "notify",
        "open_settings",
    ]
    completed: bool
    replayed: bool


class SystemActionExecution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)

    run_id: UUID
    tool_name: Literal[
        "system.open_url",
        "system.reveal_path",
        "system.copy_text",
        "system.notify",
        "system.open_settings",
    ]
    status: CommandStatus
    requires_approval: bool
    completed: bool
    replayed: bool


def tool_name_for_action(action: SystemAction) -> str:
    return f"system.{action.type}"


__all__ = [
    "CopyTextAction",
    "NotificationLevel",
    "NotifyAction",
    "OpenSettingsAction",
    "OpenUrlAction",
    "RevealPathAction",
    "SystemAction",
    "SystemActionExecution",
    "SystemActionRequest",
    "SystemActionWorkerResult",
    "SystemSettings",
    "tool_name_for_action",
]
