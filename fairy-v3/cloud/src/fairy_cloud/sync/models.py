from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fairy_core.domain.errors import VersionConflictError


@dataclass(slots=True)
class ProjectRevisionState:
    project_id: str
    revision: int
    active_version_id: str | None
    candidate_version_ids: list[str] = field(default_factory=list)

    def promote(self, *, version_id: str, expected_revision: int) -> None:
        if expected_revision != self.revision:
            if self.active_version_id == version_id and self.revision == expected_revision + 1:
                return
            if version_id not in self.candidate_version_ids:
                self.candidate_version_ids.append(version_id)
            raise VersionConflictError(
                f"expected project revision {expected_revision}, "
                f"current revision is {self.revision}"
            )

        self.active_version_id = version_id
        self.revision += 1
        if version_id in self.candidate_version_ids:
            self.candidate_version_ids.remove(version_id)


@dataclass(frozen=True, slots=True)
class SyncedEvent:
    cursor: int
    event_id: str
    run_id: str | None
    user_id: str
    device_id: str
    project_id: str | None
    conversation_id: str | None
    task_id: str | None
    version_id: str | None
    task_sequence: int | None
    schema_version: int
    event_type: str
    visibility: str
    message: str
    payload: dict[str, Any]
    created_at: datetime
