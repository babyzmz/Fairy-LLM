from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.engine import Connection, Engine

from fairy_core.memory.models import ClaimStatus, MemoryTargetKind, ObservationStatus
from fairy_core.memory.retention_models import (
    MemoryPayloadPurgeResult,
    MemoryRetentionCandidate,
)
from fairy_core.memory.schema import (
    memory_claim_revisions,
    memory_claims,
    memory_observations,
    memory_search_documents,
    memory_snapshot_items,
    memory_tombstones,
)
from fairy_core.persistence.session import SqlAlchemySession
from fairy_core.persistence.tenant import normalize_tenant_id

_PURGED_TEXT = "[purged]"
_PURGED_HASH = hashlib.sha256(_PURGED_TEXT.encode()).hexdigest()
_PURGED_FINGERPRINT = hashlib.sha256(b"fairy-memory-payload-purged").hexdigest()


class SqlAlchemyMemoryRetentionRepository:
    def __init__(self, bind: Engine | Connection, *, tenant_id: str) -> None:
        if bind.dialect.name not in {"postgresql", "sqlite"}:
            raise ValueError(f"unsupported memory retention dialect: {bind.dialect.name}")
        self._tenant_id = normalize_tenant_id(tenant_id)
        self._session = SqlAlchemySession(bind)

    def retention_candidates(
        self,
        *,
        before: datetime,
        limit: int = 1_000,
    ) -> tuple[MemoryRetentionCandidate, ...]:
        before = _retention_boundary(before)
        _retention_limit(limit)
        with self._session.read() as connection:
            observations = connection.execute(
                select(memory_observations).where(
                    memory_observations.c.tenant_id == self._tenant_id,
                    memory_observations.c.status != ObservationStatus.FORGOTTEN.value,
                    memory_observations.c.created_at <= before,
                )
            ).mappings()
            claims = connection.execute(
                select(memory_claims, memory_claim_revisions.c.source_event_ids)
                .join(
                    memory_claim_revisions,
                    and_(
                        memory_claim_revisions.c.tenant_id == memory_claims.c.tenant_id,
                        memory_claim_revisions.c.claim_id == memory_claims.c.id,
                        memory_claim_revisions.c.revision == memory_claims.c.current_revision,
                    ),
                )
                .where(
                    memory_claims.c.tenant_id == self._tenant_id,
                    memory_claims.c.status != ClaimStatus.FORGOTTEN.value,
                    memory_claims.c.updated_at <= before,
                )
            ).mappings()
            candidates = [
                _retention_candidate(row, MemoryTargetKind.OBSERVATION, "created_at")
                for row in observations
            ]
            candidates.extend(
                _retention_candidate(row, MemoryTargetKind.CLAIM, "updated_at") for row in claims
            )
        candidates.sort(
            key=lambda item: (item.stale_at, item.target_kind.value, str(item.target_id))
        )
        return tuple(candidates[:limit])

    def purge_forgotten_payloads(
        self,
        *,
        before: datetime,
        limit: int = 1_000,
    ) -> MemoryPayloadPurgeResult:
        before = _retention_boundary(before)
        _retention_limit(limit)
        with self._session.write() as connection:
            rows = connection.execute(
                select(memory_tombstones.c.target_kind, memory_tombstones.c.target_id)
                .where(
                    memory_tombstones.c.tenant_id == self._tenant_id,
                    memory_tombstones.c.created_at <= before,
                    memory_tombstones.c.target_kind.in_(
                        (MemoryTargetKind.OBSERVATION.value, MemoryTargetKind.CLAIM.value)
                    ),
                )
                .order_by(memory_tombstones.c.created_at, memory_tombstones.c.id)
                .limit(limit)
            ).all()
            observation_ids = {
                row.target_id
                for row in rows
                if row.target_kind == MemoryTargetKind.OBSERVATION.value
            }
            claim_ids = {
                row.target_id for row in rows if row.target_kind == MemoryTargetKind.CLAIM.value
            }
            blocked = self._snapshot_bound_sources(
                connection,
                observation_ids=observation_ids,
                claim_ids=claim_ids,
            )
            observation_ids.difference_update(blocked[MemoryTargetKind.OBSERVATION])
            claim_ids.difference_update(blocked[MemoryTargetKind.CLAIM])
            purged_observations = self._purge_observations(connection, observation_ids)
            purged_claims = self._purge_claims(connection, claim_ids)
        return MemoryPayloadPurgeResult(
            observations=purged_observations,
            claims=purged_claims,
        )

    def _snapshot_bound_sources(
        self,
        connection: Connection,
        *,
        observation_ids: set[str],
        claim_ids: set[str],
    ) -> dict[MemoryTargetKind, set[str]]:
        predicates = []
        if observation_ids:
            predicates.append(
                and_(
                    memory_snapshot_items.c.source_kind == "observation",
                    memory_snapshot_items.c.source_id.in_(observation_ids),
                )
            )
        if claim_ids:
            predicates.append(
                and_(
                    memory_snapshot_items.c.source_kind == "claim_revision",
                    memory_snapshot_items.c.source_id.in_(claim_ids),
                )
            )
        blocked = {
            MemoryTargetKind.OBSERVATION: set(),
            MemoryTargetKind.CLAIM: set(),
        }
        if not predicates:
            return blocked
        rows = connection.execute(
            select(memory_snapshot_items.c.source_kind, memory_snapshot_items.c.source_id).where(
                memory_snapshot_items.c.tenant_id == self._tenant_id,
                or_(*predicates),
            )
        )
        for row in rows:
            kind = (
                MemoryTargetKind.OBSERVATION
                if row.source_kind == "observation"
                else MemoryTargetKind.CLAIM
            )
            blocked[kind].add(row.source_id)
        return blocked

    def _purge_observations(self, connection: Connection, ids: set[str]) -> int:
        if not ids:
            return 0
        connection.execute(
            delete(memory_search_documents).where(
                memory_search_documents.c.tenant_id == self._tenant_id,
                memory_search_documents.c.source_kind == "observation",
                memory_search_documents.c.source_id.in_(ids),
            )
        )
        result = connection.execute(
            update(memory_observations)
            .where(
                memory_observations.c.tenant_id == self._tenant_id,
                memory_observations.c.id.in_(ids),
                memory_observations.c.status == ObservationStatus.FORGOTTEN.value,
                memory_observations.c.content != _PURGED_TEXT,
            )
            .values(
                content=_PURGED_TEXT,
                content_hash=_PURGED_HASH,
                content_fingerprint=_PURGED_FINGERPRINT,
            )
        )
        return int(result.rowcount or 0)

    def _purge_claims(self, connection: Connection, ids: set[str]) -> int:
        if not ids:
            return 0
        connection.execute(
            delete(memory_search_documents).where(
                memory_search_documents.c.tenant_id == self._tenant_id,
                memory_search_documents.c.source_kind == "claim_revision",
                memory_search_documents.c.source_id.in_(ids),
            )
        )
        connection.execute(
            update(memory_claim_revisions)
            .where(
                memory_claim_revisions.c.tenant_id == self._tenant_id,
                memory_claim_revisions.c.claim_id.in_(ids),
            )
            .values(
                value={"purged": True},
                normalized_text=_PURGED_TEXT,
                content_fingerprint=_PURGED_FINGERPRINT,
            )
        )
        result = connection.execute(
            update(memory_claims)
            .where(
                memory_claims.c.tenant_id == self._tenant_id,
                memory_claims.c.id.in_(ids),
                memory_claims.c.status == ClaimStatus.FORGOTTEN.value,
                memory_claims.c.subject != _PURGED_TEXT,
            )
            .values(
                subject=_PURGED_TEXT,
                predicate=_PURGED_TEXT,
                content_fingerprint=_PURGED_FINGERPRINT,
            )
        )
        return int(result.rowcount or 0)


def _retention_boundary(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("memory retention boundary must be timezone-aware")
    return value.astimezone(UTC)


def _retention_limit(value: int) -> None:
    if not 1 <= value <= 10_000:
        raise ValueError("memory retention limit must be between 1 and 10,000")


def _retention_candidate(
    row: Mapping[str, Any],
    target_kind: MemoryTargetKind,
    timestamp_column: str,
) -> MemoryRetentionCandidate:
    stale_at = _datetime(row[timestamp_column])
    assert stale_at is not None
    return MemoryRetentionCandidate(
        target_kind=target_kind,
        target_id=UUID(row["id"]),
        source_event_id=(
            UUID(row["source_event_id"])
            if target_kind is MemoryTargetKind.OBSERVATION
            else UUID(row["source_event_ids"][0])
        ),
        project_id=_uuid(row["project_id"]),
        conversation_id=_uuid(row["conversation_id"]),
        task_id=_uuid(row["task_id"]),
        version_id=_uuid(row["version_id"]),
        stale_at=stale_at,
    )


def _datetime(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _uuid(value: str | None) -> UUID | None:
    return UUID(value) if value else None


__all__ = ["SqlAlchemyMemoryRetentionRepository"]
