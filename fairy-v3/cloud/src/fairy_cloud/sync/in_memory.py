from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fairy_core.commanding.models import EventStreamState
from fairy_core.domain.errors import IdempotencyConflictError, VersionConflictError
from fairy_core.domain.ids import new_id

from fairy_cloud.sync.fingerprints import canonical_payload_fingerprint
from fairy_cloud.sync.models import ProjectRevisionState, SyncedEvent


class InMemorySyncStore:
    """Deterministic contract adapter used without external infrastructure."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._projects: dict[tuple[str, str], ProjectRevisionState] = {}
        self._events: list[SyncedEvent] = []
        self._event_cursors: dict[tuple[str, str], int] = {}
        self._ledger_ids: dict[str, str] = {}
        self._task_sequences: dict[tuple[str, str, int], str] = {}
        self._manifests: dict[tuple[str, str, str], dict[str, Any]] = {}

    async def register_project(
        self,
        *,
        project_id: str,
        user_id: str,
        active_version_id: str | None = None,
    ) -> ProjectRevisionState:
        async with self._lock:
            project_key = (user_id, project_id)
            existing = self._projects.get(project_key)
            if existing is not None:
                return existing
            state = ProjectRevisionState(
                project_id=project_id,
                revision=0,
                active_version_id=active_version_id,
            )
            self._projects[project_key] = state
            return state

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
    ) -> int:
        async with self._lock:
            return self._append_event_unlocked(
                event_id=event_id,
                run_id=run_id,
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
                message=message or event_type,
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

    async def event_stream_state(self, *, user_id: str) -> EventStreamState:
        async with self._lock:
            ledger_id = self._ledger_ids.get(user_id)
            if ledger_id is None:
                ledger_id = str(new_id())
                self._ledger_ids[user_id] = ledger_id
            cursors = [
                event.cursor
                for event in self._events
                if event.user_id == user_id and event.visibility in {"user", "developer"}
            ]
            return EventStreamState(
                ledger_id=UUID(ledger_id),
                oldest_cursor=min(cursors, default=0),
                latest_cursor=max(cursors, default=0),
            )

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
            state = self._projects[(user_id, project_id)]
            self._manifests.setdefault((user_id, project_id, version_id), dict(manifest))
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
                message = (
                    "Version retained as a conflict candidate"
                    if conflict is not None
                    else "Version promoted"
                )
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
                    "message": message,
                    "payload": {
                        "expected_revision": expected_revision,
                        "project_revision": state.revision,
                        "manifest": dict(manifest),
                    },
                    "schema_version": 1,
                }
                self._append_event_unlocked(
                    event_id=decision_event_id,
                    run_id=None,
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
                    message=message,
                    payload=event_payload,
                )

            if conflict is not None:
                raise conflict
            return state

    def project_state(self, project_id: str) -> ProjectRevisionState:
        matches = [
            state
            for (_user_id, stored_project_id), state in self._projects.items()
            if stored_project_id == project_id
        ]
        if len(matches) != 1:
            raise KeyError(f"project id is missing or ambiguous: {project_id}")
        return matches[0]

    def _append_event_unlocked(
        self,
        *,
        event_id: str,
        run_id: str | None,
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
        message: str,
        payload: Mapping[str, Any],
    ) -> int:
        event_key = (user_id, event_id)
        existing = self._event_cursors.get(event_key)
        if existing is not None:
            cursor = existing
            stored = self._events[cursor - 1]
            immutable = {
                "run_id": (stored.run_id, run_id),
                "device_id": (stored.device_id, device_id),
                "project_id": (stored.project_id, project_id),
                "conversation_id": (stored.conversation_id, conversation_id),
                "task_id": (stored.task_id, task_id),
                "version_id": (stored.version_id, version_id),
                "task_sequence": (stored.task_sequence, task_sequence),
                "schema_version": (stored.schema_version, schema_version),
                "event_type": (stored.event_type, event_type),
                "visibility": (stored.visibility, visibility),
                "message": (stored.message, message),
            }
            mismatched = [
                field for field, (saved, requested) in immutable.items() if saved != requested
            ]
            if canonical_payload_fingerprint(stored.payload) != canonical_payload_fingerprint(
                payload
            ):
                mismatched.append("payload")
            if mismatched:
                raise IdempotencyConflictError(
                    f"event replay changed immutable fields: {', '.join(mismatched)}"
                )
            return cursor
        if task_id is not None and task_sequence is not None:
            sequence_key = (user_id, task_id, task_sequence)
            sequence_owner = self._task_sequences.get(sequence_key)
            if sequence_owner is not None:
                raise IdempotencyConflictError(
                    f"task sequence {task_sequence} is already owned by event {sequence_owner}"
                )
        cursor = len(self._events) + 1
        self._events.append(
            SyncedEvent(
                cursor=cursor,
                event_id=event_id,
                run_id=run_id,
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
                message=message,
                payload=dict(payload),
                created_at=datetime.now(UTC),
            )
        )
        self._event_cursors[event_key] = cursor
        if task_id is not None and task_sequence is not None:
            self._task_sequences[(user_id, task_id, task_sequence)] = event_id
        return cursor


__all__ = ["InMemorySyncStore"]
