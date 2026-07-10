from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fairy_core.contracts.models import EventEnvelopeModel
from pydantic import BaseModel, ConfigDict, Field

Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class SyncContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SyncEventBatch(SyncContract):
    items: tuple[EventEnvelopeModel, ...] = Field(min_length=1, max_length=500)


class SyncProjectRegistration(SyncContract):
    project_id: UUID
    active_version_id: UUID | None = None


class VersionManifestInput(SyncContract):
    expected_revision: int = Field(ge=0)
    base_version_id: UUID | None = None
    snapshot_key: str = Field(min_length=1, max_length=1_024)
    snapshot_sha256: Sha256
    snapshot_size: int = Field(ge=0, le=536_870_912)
    files_digest: Sha256
    decision_event_id: UUID
    conversation_id: UUID
    task_id: UUID
    task_sequence: int = Field(ge=1)
