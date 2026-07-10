from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from fairy_core.domain.errors import VersionConflictError

from fairy_cloud.sync.models import ProjectRevisionState, SyncedEvent


class MemorySyncStore:
    """Deterministic contract adapter used without external infrastructure."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._projects: dict[str, tuple[str, ProjectRevisionState]] = {}
        self._events: list[SyncedEvent] = []
        self._event_cursors: dict[str, tuple[str, int]] = {}
        self._manifests: dict[tuple[str, str], dict[str, Any]] = {}

    async def register_project(
        self,
        *,
        project_id: str,
        user_id: str,
        active_version_id: str | None = None,
    ) -> ProjectRevisionState:
        async with self._lock:
            existing = self._projects.get(project_id)
            if existing is not None:
                owner, state = existing
                if owner != user_id:
                    raise PermissionError("project belongs to a different user")
                return state
            state = ProjectRevisionState(
                project_id=project_id,
                revision=0,
                active_version_id=active_version_id,
            )
            self._projects[project_id] = (user_id, state)
            return state

    async def append_event(
        self,
        *,
        event_id: str,
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
        payload: Mapping[str, Any],
    ) -> int:
        async with self._lock:
            return self._append_event_unlocked(
                event_id=event_id,
                user_id=user_id,
                device_id=device_id,
                project_id=project_id,
                conversation_id=conversation_id,
                task_id=task_id,
                version_id=version_id,
                task_sequence=task_sequence,
                schema_version=schema_version,
                event_type=event_type,
                visibility=visibility,
                payload=payload,
            )

    async def events_after(
        self,
        *,
        user_id: str,
        cursor: int,
        limit: int = 500,
        visibilities: frozenset[str] = frozenset({"user", "developer"}),
    ) -> list[SyncedEvent]:
        return [
            event
            for event in self._events
            if event.user_id == user_id
            and event.cursor > cursor
            and event.visibility in visibilities
        ][:limit]

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
    ) -> ProjectRevisionState:
        async with self._lock:
            owner, state = self._projects[project_id]
            if owner != user_id:
                raise PermissionError("project belongs to a different user")
            self._manifests.setdefault((project_id, version_id), dict(manifest))
            conflict: VersionConflictError | None = None
            try:
                state.promote(version_id=version_id, expected_revision=expected_revision)
            except VersionConflictError as error:
                conflict = error

            if decision_event_id is not None:
                if not all((device_id, conversation_id, task_id, task_sequence)):
                    raise ValueError("version decision event requires complete scope")
                outcome = "candidate_retained" if conflict is not None else "promoted"
                event_type = f"version.{outcome}"
                created_at = datetime.now(UTC)
                event_payload = {
                    "id": decision_event_id,
                    "run_id": None,
                    "project_id": project_id,
                    "conversation_id": conversation_id,
                    "task_id": task_id,
                    "version_id": version_id,
                    "task_sequence": task_sequence,
                    "event_type": event_type,
                    "visibility": "user",
                    "message": (
                        "Version retained as a conflict candidate"
                        if conflict is not None
                        else "Version promoted"
                    ),
                    "payload": {
                        "expected_revision": expected_revision,
                        "project_revision": state.revision,
                        "manifest": dict(manifest),
                    },
                    "schema_version": 1,
                    "created_at": created_at.isoformat(),
                }
                self._append_event_unlocked(
                    event_id=decision_event_id,
                    user_id=user_id,
                    device_id=str(device_id),
                    project_id=project_id,
                    conversation_id=str(conversation_id),
                    task_id=str(task_id),
                    version_id=version_id,
                    task_sequence=int(task_sequence),
                    schema_version=1,
                    event_type=event_type,
                    visibility="user",
                    payload=event_payload,
                )

            if conflict is not None:
                raise conflict
            return state

    def project_state(self, project_id: str) -> ProjectRevisionState:
        return self._projects[project_id][1]

    def _append_event_unlocked(
        self,
        *,
        event_id: str,
        user_id: str,
        device_id: str,
        project_id: str | None,
        conversation_id: str | None,
        task_id: str | None,
        version_id: str | None,
        task_sequence: int | None,
        schema_version: int,
        event_type: str,
        visibility: str,
        payload: Mapping[str, Any],
    ) -> int:
        existing = self._event_cursors.get(event_id)
        if existing is not None:
            owner, cursor = existing
            if owner != user_id:
                raise PermissionError("event id belongs to a different user")
            return cursor
        cursor = len(self._events) + 1
        self._events.append(
            SyncedEvent(
                cursor=cursor,
                event_id=event_id,
                user_id=user_id,
                device_id=device_id,
                project_id=project_id,
                conversation_id=conversation_id,
                task_id=task_id,
                version_id=version_id,
                task_sequence=task_sequence,
                schema_version=schema_version,
                event_type=event_type,
                visibility=visibility,
                payload=dict(payload),
                created_at=datetime.now(UTC),
            )
        )
        self._event_cursors[event_id] = (user_id, cursor)
        return cursor
