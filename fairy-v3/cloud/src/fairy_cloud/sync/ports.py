from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from fairy_cloud.sync.models import ProjectRevisionState, SyncedEvent


class SyncStore(Protocol):
    async def register_project(
        self,
        *,
        project_id: str,
        user_id: str,
        active_version_id: str | None = None,
    ) -> ProjectRevisionState: ...

    async def append_event(
        self,
        *,
        event_id: str,
        run_id: str | None = None,
        user_id: str,
        device_id: str,
        project_id: str | None,
        conversation_id: str | None = None,
        task_id: str | None = None,
        version_id: str | None = None,
        task_sequence: int | None = None,
        schema_version: int,
        event_type: str,
        visibility: str = "user",
        message: str | None = None,
        payload: Mapping[str, Any],
    ) -> int: ...

    async def events_after(
        self,
        *,
        user_id: str,
        cursor: int,
        limit: int = 500,
        visibilities: frozenset[str] = frozenset({"user", "developer"}),
    ) -> list[SyncedEvent]: ...

    async def promote_version(
        self,
        *,
        user_id: str,
        project_id: str,
        version_id: str,
        expected_revision: int,
        manifest: Mapping[str, Any],
        decision_event_id: str | None = None,
        device_id: str | None = None,
        conversation_id: str | None = None,
        task_id: str | None = None,
        task_sequence: int | None = None,
    ) -> ProjectRevisionState: ...
