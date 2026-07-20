from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, Engine, RowMapping

from fairy_core.commanding.schema import domain_events
from fairy_core.domain.errors import MemoryConflictError, MemoryForgottenError
from fairy_core.memory.models import (
    ClaimStatus,
    MemoryAuthority,
    MemoryClaim,
    MemoryClaimRevision,
    MemoryNamespace,
    MemoryObservation,
    MemoryScanResult,
    MemorySensitivity,
    MemorySourceType,
    MemoryTargetKind,
    MemoryTombstone,
    ObservationStatus,
)
from fairy_core.memory.schema import (
    memory_claim_revisions,
    memory_claims,
    memory_metadata,
    memory_observations,
    memory_tombstones,
)
from fairy_core.persistence.session import SqlAlchemySession
from fairy_core.persistence.tenant import normalize_tenant_id

_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_ACTIVE_CLAIM_STATUSES = (ClaimStatus.ACTIVE.value, ClaimStatus.CONFLICTED.value)
_VISIBLE_OBSERVATION_STATUSES = (
    ObservationStatus.PENDING.value,
    ObservationStatus.ACCEPTED.value,
    ObservationStatus.PROMOTED.value,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _datetime(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _uuid(value: str | None) -> UUID | None:
    return UUID(value) if value else None


def _fingerprint(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        dict(payload),
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _validate_request_fingerprint(value: str) -> str:
    if _SHA256_HEX.fullmatch(value) is None:
        raise ValueError("request_fingerprint must be a lowercase SHA-256 digest")
    return value


class SqlAlchemyMemoryRepository:
    """Tenant-scoped canonical Hermes storage for SQLite and PostgreSQL."""

    def __init__(
        self,
        bind: Engine | Connection,
        *,
        tenant_id: str,
        initialize_schema: bool = False,
        owns_engine: bool = False,
    ) -> None:
        dialect_name = bind.dialect.name
        if dialect_name not in {"postgresql", "sqlite"}:
            raise ValueError(f"unsupported memory repository dialect: {dialect_name}")
        if initialize_schema and dialect_name != "sqlite":
            raise ValueError("PostgreSQL schemas must be initialized through Alembic")
        self._tenant_id = normalize_tenant_id(tenant_id)
        self._session = SqlAlchemySession(bind, owns_engine=owns_engine)
        if initialize_schema:
            memory_metadata.create_all(bind)

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    def close(self) -> None:
        self._session.close()

    def append_observation(
        self,
        observation: MemoryObservation,
        *,
        request_fingerprint: str,
    ) -> MemoryObservation:
        request_fingerprint = _validate_request_fingerprint(request_fingerprint)
        content_fingerprint = self._observation_fingerprint(observation)
        values = {
            "tenant_id": self._tenant_id,
            "id": str(observation.id),
            "project_id": str(observation.project_id) if observation.project_id else None,
            "conversation_id": str(observation.conversation_id),
            "task_id": str(observation.task_id),
            "version_id": str(observation.version_id) if observation.version_id else None,
            "scope_digest": observation.scope_digest,
            "source_event_id": str(observation.source_event_id),
            "source_cursor": observation.source_cursor,
            "source_type": observation.source_type.value,
            "content": observation.content,
            "content_hash": observation.content_hash,
            "proposed_namespace": observation.proposed_namespace.value,
            "authority": observation.authority.value,
            "confidence": observation.confidence,
            "sensitivity": observation.sensitivity.value,
            "scan_result": observation.scan_result.value,
            "status": observation.status.value,
            "actor": observation.actor,
            "request_fingerprint": request_fingerprint,
            "content_fingerprint": content_fingerprint,
            "created_at": observation.created_at,
        }
        with self._session.write() as connection:
            self._verify_observation_source(connection, observation)
            connection.execute(
                self._insert(memory_observations).values(**values).on_conflict_do_nothing()
            )
            row = self._observation_by_request(connection, request_fingerprint)
            if row is None:
                if self._observation_row(connection, observation.id) is not None:
                    raise MemoryConflictError("memory Observation ID is already in use")
                raise MemoryConflictError("memory Observation could not be inserted")
            self._verify_content_fingerprint(row, content_fingerprint, "Observation")
        return self._observation_from_row(row)

    def get_observation(
        self,
        observation_id: UUID,
        *,
        include_forgotten: bool = False,
    ) -> MemoryObservation | None:
        with self._session.read() as connection:
            row = self._observation_row(connection, observation_id)
        if row is None:
            return None
        if row["status"] == ObservationStatus.FORGOTTEN.value and not include_forgotten:
            raise MemoryForgottenError(f"memory Observation has been forgotten: {observation_id}")
        return self._observation_from_row(row)

    def transition_observation(
        self,
        observation_id: UUID,
        *,
        expected_status: ObservationStatus,
        status: ObservationStatus,
    ) -> MemoryObservation:
        with self._session.write() as connection:
            row = self._observation_row(connection, observation_id)
            if row is None:
                raise KeyError(f"memory Observation not found: {observation_id}")
            current = self._observation_from_row(row)
            if current.status is status:
                return current
            changed = current.transition_to(status)
            result = connection.execute(
                update(memory_observations)
                .where(
                    memory_observations.c.tenant_id == self._tenant_id,
                    memory_observations.c.id == str(observation_id),
                    memory_observations.c.status == expected_status.value,
                )
                .values(status=changed.status.value)
            )
            if result.rowcount != 1:
                raise MemoryConflictError("memory Observation status changed")
            return changed

    def observations_for_scope(
        self,
        *,
        namespace: MemoryNamespace,
        project_id: UUID | None = None,
        conversation_id: UUID | None = None,
        task_id: UUID | None = None,
        limit: int | None = None,
        newest_first: bool = False,
        retrievable_only: bool = False,
    ) -> list[MemoryObservation]:
        if limit is not None and not 1 <= limit <= 1_000:
            raise ValueError("Observation read limit must be between 1 and 1,000")
        self._validate_read_scope(
            namespace=namespace,
            project_id=project_id,
            conversation_id=conversation_id,
            task_id=task_id,
            device_id=None,
        )
        statement = select(memory_observations).where(
            memory_observations.c.tenant_id == self._tenant_id,
            memory_observations.c.proposed_namespace == namespace.value,
            memory_observations.c.status.in_(_VISIBLE_OBSERVATION_STATUSES),
        )
        if retrievable_only:
            statement = statement.where(
                memory_observations.c.status.in_(
                    (ObservationStatus.ACCEPTED.value, ObservationStatus.PROMOTED.value)
                ),
                memory_observations.c.scan_result == MemoryScanResult.CLEAN.value,
                memory_observations.c.sensitivity != MemorySensitivity.SECRET.value,
            )
        statement = self._scope_statement(
            statement,
            memory_observations,
            project_id=project_id,
            conversation_id=conversation_id,
            task_id=task_id,
            device_id=None,
        )
        with self._session.read() as connection:
            order = (
                (
                    memory_observations.c.source_cursor.desc(),
                    memory_observations.c.id.desc(),
                )
                if newest_first
                else (memory_observations.c.source_cursor, memory_observations.c.id)
            )
            statement = statement.order_by(*order)
            if limit is not None:
                statement = statement.limit(limit)
            rows = connection.execute(statement).mappings()
            return [self._observation_from_row(row) for row in rows]

    def observations_for_projection(self) -> list[MemoryObservation]:
        with self._session.read() as connection:
            rows = (
                connection.execute(
                    select(memory_observations)
                    .where(memory_observations.c.tenant_id == self._tenant_id)
                    .order_by(
                        memory_observations.c.source_cursor,
                        memory_observations.c.id,
                    )
                )
                .mappings()
                .all()
            )
        return [self._observation_from_row(row) for row in rows]

    def create_claim(
        self,
        claim: MemoryClaim,
        *,
        request_fingerprint: str,
    ) -> MemoryClaim:
        request_fingerprint = _validate_request_fingerprint(request_fingerprint)
        content_fingerprint = self._claim_fingerprint(claim)
        values = {
            "tenant_id": self._tenant_id,
            "id": str(claim.id),
            "namespace": claim.namespace.value,
            "project_id": str(claim.project_id) if claim.project_id else None,
            "conversation_id": (str(claim.conversation_id) if claim.conversation_id else None),
            "task_id": str(claim.task_id) if claim.task_id else None,
            "version_id": str(claim.version_id) if claim.version_id else None,
            "device_id": claim.device_id,
            "subject": claim.subject,
            "predicate": claim.predicate,
            "current_revision": claim.current_revision,
            "conflict_set_id": str(claim.conflict_set_id) if claim.conflict_set_id else None,
            "status": claim.status.value,
            "request_fingerprint": request_fingerprint,
            "content_fingerprint": content_fingerprint,
            "created_at": claim.created_at,
            "updated_at": claim.updated_at,
        }
        with self._session.write() as connection:
            connection.execute(
                self._insert(memory_claims).values(**values).on_conflict_do_nothing()
            )
            row = self._claim_by_request(connection, request_fingerprint)
            if row is None:
                if self._claim_row(connection, claim.id) is not None:
                    raise MemoryConflictError("memory Claim ID is already in use")
                raise MemoryConflictError("memory Claim could not be inserted")
            self._verify_content_fingerprint(row, content_fingerprint, "Claim")
        return self._claim_from_row(row)

    def get_claim(
        self,
        claim_id: UUID,
        *,
        include_forgotten: bool = False,
    ) -> MemoryClaim | None:
        with self._session.read() as connection:
            row = self._claim_row(connection, claim_id)
        if row is None:
            return None
        if row["status"] == ClaimStatus.FORGOTTEN.value and not include_forgotten:
            raise MemoryForgottenError(f"memory Claim has been forgotten: {claim_id}")
        return self._claim_from_row(row)

    def append_revision(
        self,
        claim_id: UUID,
        *,
        expected_revision: int,
        revision: MemoryClaimRevision,
        request_fingerprint: str,
    ) -> MemoryClaim:
        with self._session.write() as connection:
            return self._append_revision(
                connection,
                claim_id=claim_id,
                expected_revision=expected_revision,
                revision=revision,
                resolved_claim_ids=(),
                request_fingerprint=request_fingerprint,
            )

    def revisions_for_claim(self, claim_id: UUID) -> list[MemoryClaimRevision]:
        with self._session.read() as connection:
            rows = (
                connection.execute(
                    select(memory_claim_revisions)
                    .where(
                        memory_claim_revisions.c.tenant_id == self._tenant_id,
                        memory_claim_revisions.c.claim_id == str(claim_id),
                    )
                    .order_by(memory_claim_revisions.c.revision)
                )
                .mappings()
                .all()
            )
        return [self._revision_from_row(row) for row in rows]

    def resolve_conflict(
        self,
        claim_id: UUID,
        *,
        expected_revision: int,
        revision: MemoryClaimRevision,
        resolved_claim_ids: tuple[UUID, ...],
        request_fingerprint: str,
    ) -> MemoryClaim:
        if not resolved_claim_ids:
            raise ValueError("conflict resolution requires resolved_claim_ids")
        with self._session.write() as connection:
            request_fingerprint = _validate_request_fingerprint(request_fingerprint)
            current = self._claim_row(connection, claim_id, for_update=True)
            if current is None:
                raise KeyError(f"memory Claim not found: {claim_id}")
            if current["status"] == ClaimStatus.FORGOTTEN.value:
                raise MemoryForgottenError(f"memory Claim has been forgotten: {claim_id}")
            replay = self._revision_by_request(connection, claim_id, request_fingerprint)
            if replay is not None:
                self._verify_content_fingerprint(
                    replay,
                    self._revision_fingerprint(revision, resolved_claim_ids),
                    "Claim revision",
                )
                return self._claim_from_row(current)
            if current["status"] != ClaimStatus.CONFLICTED.value:
                raise MemoryConflictError("memory Claim is not conflicted")
            conflict_set_id = current["conflict_set_id"]
            for resolved_id in resolved_claim_ids:
                resolved = self._claim_row(connection, resolved_id, for_update=True)
                if resolved is None or resolved["conflict_set_id"] != conflict_set_id:
                    raise MemoryConflictError(
                        "resolved Claims must belong to the same conflict set"
                    )
            updated = self._append_revision(
                connection,
                claim_id=claim_id,
                expected_revision=expected_revision,
                revision=revision,
                resolved_claim_ids=resolved_claim_ids,
                request_fingerprint=request_fingerprint,
            )
            target_status = (
                ClaimStatus.EXPIRED
                if revision.valid_to is not None and revision.valid_to <= _now()
                else ClaimStatus.ACTIVE
            )
            connection.execute(
                update(memory_claims)
                .where(
                    memory_claims.c.tenant_id == self._tenant_id,
                    memory_claims.c.id == str(claim_id),
                    memory_claims.c.current_revision == updated.current_revision,
                )
                .values(status=target_status.value, updated_at=_now())
            )
            row = self._claim_row(connection, claim_id)
            assert row is not None
            return self._claim_from_row(row)

    def forget(
        self,
        tombstone: MemoryTombstone,
        *,
        request_fingerprint: str,
    ) -> MemoryTombstone:
        request_fingerprint = _validate_request_fingerprint(request_fingerprint)
        content_fingerprint = self._tombstone_fingerprint(tombstone)
        if tombstone.target_kind not in {
            MemoryTargetKind.CLAIM,
            MemoryTargetKind.OBSERVATION,
        }:
            raise ValueError("target memory kind is not persisted in this delivery slice")
        with self._session.write() as connection:
            self._verify_source_event(connection, tombstone.source_event_id)
            target_table = (
                memory_claims
                if tombstone.target_kind is MemoryTargetKind.CLAIM
                else memory_observations
            )
            target = (
                connection.execute(
                    select(target_table).where(
                        target_table.c.tenant_id == self._tenant_id,
                        target_table.c.id == str(tombstone.target_id),
                    )
                )
                .mappings()
                .first()
            )
            if target is None:
                raise KeyError(f"memory target not found: {tombstone.target_id}")

            values = {
                "tenant_id": self._tenant_id,
                "id": str(tombstone.id),
                "target_kind": tombstone.target_kind.value,
                "target_id": str(tombstone.target_id),
                "reason": tombstone.reason,
                "actor": tombstone.actor,
                "source_event_id": str(tombstone.source_event_id),
                "request_fingerprint": request_fingerprint,
                "content_fingerprint": content_fingerprint,
                "created_at": tombstone.created_at,
            }
            connection.execute(
                self._insert(memory_tombstones).values(**values).on_conflict_do_nothing()
            )
            row = self._tombstone_by_target(
                connection,
                target_kind=tombstone.target_kind,
                target_id=tombstone.target_id,
            )
            if row is None:
                raise MemoryConflictError("memory Tombstone could not be inserted")
            self._verify_content_fingerprint(row, content_fingerprint, "Tombstone")
            if row["request_fingerprint"] != request_fingerprint:
                raise MemoryConflictError("memory target was forgotten by another request")

            connection.execute(
                update(target_table)
                .where(
                    target_table.c.tenant_id == self._tenant_id,
                    target_table.c.id == str(tombstone.target_id),
                )
                .values(
                    status=(
                        ClaimStatus.FORGOTTEN.value
                        if tombstone.target_kind is MemoryTargetKind.CLAIM
                        else ObservationStatus.FORGOTTEN.value
                    )
                )
            )
            if tombstone.target_kind is MemoryTargetKind.CLAIM:
                connection.execute(
                    update(memory_claim_revisions)
                    .where(
                        memory_claim_revisions.c.tenant_id == self._tenant_id,
                        memory_claim_revisions.c.claim_id == str(tombstone.target_id),
                        memory_claim_revisions.c.is_current.is_(True),
                    )
                    .values(is_current=False)
                )
        return self._tombstone_from_row(row)

    def get_tombstone(
        self,
        *,
        target_kind: MemoryTargetKind,
        target_id: UUID,
    ) -> MemoryTombstone | None:
        with self._session.read() as connection:
            row = self._tombstone_by_target(
                connection,
                target_kind=target_kind,
                target_id=target_id,
            )
        return self._tombstone_from_row(row) if row is not None else None

    def claims_for_scope(
        self,
        *,
        namespace: MemoryNamespace,
        project_id: UUID | None = None,
        conversation_id: UUID | None = None,
        task_id: UUID | None = None,
        device_id: str | None = None,
    ) -> list[MemoryClaim]:
        self._validate_read_scope(
            namespace=namespace,
            project_id=project_id,
            conversation_id=conversation_id,
            task_id=task_id,
            device_id=device_id,
        )
        now = _now()
        statement = (
            select(memory_claims)
            .join(
                memory_claim_revisions,
                and_(
                    memory_claim_revisions.c.tenant_id == memory_claims.c.tenant_id,
                    memory_claim_revisions.c.claim_id == memory_claims.c.id,
                    memory_claim_revisions.c.revision == memory_claims.c.current_revision,
                    memory_claim_revisions.c.is_current.is_(True),
                ),
            )
            .where(
                memory_claims.c.tenant_id == self._tenant_id,
                memory_claims.c.namespace == namespace.value,
                memory_claims.c.status.in_(_ACTIVE_CLAIM_STATUSES),
                or_(
                    memory_claim_revisions.c.valid_from.is_(None),
                    memory_claim_revisions.c.valid_from <= now,
                ),
                or_(
                    memory_claim_revisions.c.valid_to.is_(None),
                    memory_claim_revisions.c.valid_to > now,
                ),
            )
        )
        statement = self._scope_statement(
            statement,
            memory_claims,
            project_id=project_id,
            conversation_id=conversation_id,
            task_id=task_id,
            device_id=device_id,
        )
        with self._session.read() as connection:
            rows = connection.execute(
                statement.order_by(
                    memory_claims.c.subject,
                    memory_claims.c.predicate,
                    memory_claims.c.id,
                )
            ).mappings()
            return [self._claim_from_row(row) for row in rows]

    def claims_for_projection(self) -> list[MemoryClaim]:
        with self._session.read() as connection:
            rows = (
                connection.execute(
                    select(memory_claims)
                    .where(memory_claims.c.tenant_id == self._tenant_id)
                    .order_by(memory_claims.c.id)
                )
                .mappings()
                .all()
            )
        return [self._claim_from_row(row) for row in rows]

    def _append_revision(
        self,
        connection: Connection,
        *,
        claim_id: UUID,
        expected_revision: int,
        revision: MemoryClaimRevision,
        resolved_claim_ids: tuple[UUID, ...],
        request_fingerprint: str,
    ) -> MemoryClaim:
        request_fingerprint = _validate_request_fingerprint(request_fingerprint)
        if revision.claim_id != claim_id or revision.revision != expected_revision + 1:
            raise MemoryConflictError("Claim revision does not match the expected revision")
        expected_superseded = expected_revision or None
        if revision.supersedes_revision != expected_superseded:
            raise MemoryConflictError("Claim revision does not supersede the expected revision")
        content_fingerprint = self._revision_fingerprint(revision, resolved_claim_ids)
        claim_row = self._claim_row(connection, claim_id, for_update=True)
        if claim_row is None:
            raise KeyError(f"memory Claim not found: {claim_id}")
        if claim_row["status"] == ClaimStatus.FORGOTTEN.value:
            raise MemoryForgottenError(f"memory Claim has been forgotten: {claim_id}")
        if claim_row["status"] not in {
            ClaimStatus.CANDIDATE.value,
            ClaimStatus.ACTIVE.value,
            ClaimStatus.CONFLICTED.value,
            ClaimStatus.EXPIRED.value,
        }:
            raise MemoryConflictError(f"cannot append a revision to a {claim_row['status']} Claim")
        replay = self._revision_by_request(connection, claim_id, request_fingerprint)
        if replay is not None:
            self._verify_content_fingerprint(replay, content_fingerprint, "Claim revision")
            return self._claim_from_row(claim_row)
        if int(claim_row["current_revision"]) != expected_revision:
            raise MemoryConflictError(
                f"expected Claim revision {expected_revision}, current revision is "
                f"{claim_row['current_revision']}"
            )
        self._verify_revision_sources(connection, claim_row, revision)
        now = _now()
        target_status = claim_row["status"]
        if revision.valid_to is not None and revision.valid_to <= now:
            target_status = ClaimStatus.EXPIRED.value
        elif target_status in {ClaimStatus.CANDIDATE.value, ClaimStatus.EXPIRED.value}:
            target_status = ClaimStatus.ACTIVE.value
        result = connection.execute(
            update(memory_claims)
            .where(
                memory_claims.c.tenant_id == self._tenant_id,
                memory_claims.c.id == str(claim_id),
                memory_claims.c.current_revision == expected_revision,
                memory_claims.c.status != ClaimStatus.FORGOTTEN.value,
            )
            .values(
                current_revision=revision.revision,
                status=target_status,
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            raise MemoryConflictError("Claim revision changed concurrently")
        if expected_revision:
            connection.execute(
                update(memory_claim_revisions)
                .where(
                    memory_claim_revisions.c.tenant_id == self._tenant_id,
                    memory_claim_revisions.c.claim_id == str(claim_id),
                    memory_claim_revisions.c.is_current.is_(True),
                )
                .values(is_current=False)
            )
        connection.execute(
            self._insert(memory_claim_revisions).values(
                tenant_id=self._tenant_id,
                claim_id=str(claim_id),
                revision=revision.revision,
                value=revision.value,
                normalized_text=revision.normalized_text,
                source_observation_ids=[str(value) for value in revision.source_observation_ids],
                source_event_ids=[str(value) for value in revision.source_event_ids],
                authority=revision.authority.value,
                confidence=revision.confidence,
                valid_from=revision.valid_from,
                valid_to=revision.valid_to,
                recorded_at=revision.recorded_at,
                actor=revision.actor,
                supersedes_revision=revision.supersedes_revision,
                resolved_claim_ids=[str(value) for value in resolved_claim_ids],
                is_current=True,
                request_fingerprint=request_fingerprint,
                content_fingerprint=content_fingerprint,
            )
        )
        updated = self._claim_row(connection, claim_id)
        assert updated is not None
        return self._claim_from_row(updated)

    def _verify_observation_source(
        self,
        connection: Connection,
        observation: MemoryObservation,
    ) -> None:
        event = self._verify_source_event(connection, observation.source_event_id)
        expected = {
            "cursor": observation.source_cursor,
            "project_id": str(observation.project_id) if observation.project_id else None,
            "conversation_id": str(observation.conversation_id),
            "task_id": str(observation.task_id),
            "version_id": str(observation.version_id) if observation.version_id else None,
        }
        if any(event[key] != value for key, value in expected.items()):
            raise MemoryConflictError("Observation provenance does not match its source event")

    def _verify_source_event(self, connection: Connection, event_id: UUID) -> RowMapping:
        event = (
            connection.execute(
                select(domain_events).where(
                    domain_events.c.tenant_id == self._tenant_id,
                    domain_events.c.event_id == str(event_id),
                )
            )
            .mappings()
            .first()
        )
        if event is None:
            raise MemoryConflictError(f"memory source event not found: {event_id}")
        if event["visibility"] == "internal":
            raise MemoryConflictError("internal events cannot become memory provenance")
        return event

    def _verify_revision_sources(
        self,
        connection: Connection,
        claim: RowMapping,
        revision: MemoryClaimRevision,
    ) -> None:
        for observation_id in revision.source_observation_ids:
            observation = self._observation_row(connection, observation_id)
            if observation is None or observation["status"] == ObservationStatus.FORGOTTEN.value:
                raise MemoryConflictError(f"source Observation is unavailable: {observation_id}")
            if not self._observation_matches_claim(observation, claim):
                raise MemoryConflictError("source Observation is outside the Claim Scope")
        for event_id in revision.source_event_ids:
            self._verify_source_event(connection, event_id)

    @staticmethod
    def _observation_matches_claim(observation: RowMapping, claim: RowMapping) -> bool:
        namespace = MemoryNamespace(claim["namespace"])
        if namespace is MemoryNamespace.PROJECT_CANONICAL:
            return observation["project_id"] == claim["project_id"]
        if namespace is MemoryNamespace.CONVERSATION_DRAFT:
            return observation["conversation_id"] == claim["conversation_id"]
        if namespace is MemoryNamespace.TASK_EPISODE:
            return observation["task_id"] == claim["task_id"]
        return True

    def _insert(self, table: Any):
        return (
            postgresql_insert(table)
            if self._session.dialect_name == "postgresql"
            else sqlite_insert(table)
        )

    def _observation_row(
        self,
        connection: Connection,
        observation_id: UUID,
    ) -> RowMapping | None:
        return (
            connection.execute(
                select(memory_observations).where(
                    memory_observations.c.tenant_id == self._tenant_id,
                    memory_observations.c.id == str(observation_id),
                )
            )
            .mappings()
            .first()
        )

    def _observation_by_request(
        self,
        connection: Connection,
        request_fingerprint: str,
    ) -> RowMapping | None:
        return (
            connection.execute(
                select(memory_observations).where(
                    memory_observations.c.tenant_id == self._tenant_id,
                    memory_observations.c.request_fingerprint == request_fingerprint,
                )
            )
            .mappings()
            .first()
        )

    def _claim_row(
        self,
        connection: Connection,
        claim_id: UUID,
        *,
        for_update: bool = False,
    ) -> RowMapping | None:
        statement = select(memory_claims).where(
            memory_claims.c.tenant_id == self._tenant_id,
            memory_claims.c.id == str(claim_id),
        )
        if for_update and self._session.dialect_name == "postgresql":
            statement = statement.with_for_update()
        return connection.execute(statement).mappings().first()

    def _claim_by_request(
        self,
        connection: Connection,
        request_fingerprint: str,
    ) -> RowMapping | None:
        return (
            connection.execute(
                select(memory_claims).where(
                    memory_claims.c.tenant_id == self._tenant_id,
                    memory_claims.c.request_fingerprint == request_fingerprint,
                )
            )
            .mappings()
            .first()
        )

    def _revision_by_request(
        self,
        connection: Connection,
        claim_id: UUID,
        request_fingerprint: str,
    ) -> RowMapping | None:
        return (
            connection.execute(
                select(memory_claim_revisions).where(
                    memory_claim_revisions.c.tenant_id == self._tenant_id,
                    memory_claim_revisions.c.claim_id == str(claim_id),
                    memory_claim_revisions.c.request_fingerprint == request_fingerprint,
                )
            )
            .mappings()
            .first()
        )

    def _tombstone_by_target(
        self,
        connection: Connection,
        *,
        target_kind: MemoryTargetKind,
        target_id: UUID,
    ) -> RowMapping | None:
        return (
            connection.execute(
                select(memory_tombstones).where(
                    memory_tombstones.c.tenant_id == self._tenant_id,
                    memory_tombstones.c.target_kind == target_kind.value,
                    memory_tombstones.c.target_id == str(target_id),
                )
            )
            .mappings()
            .first()
        )

    @staticmethod
    def _verify_content_fingerprint(
        row: Mapping[str, Any],
        expected: str,
        kind: str,
    ) -> None:
        if row["content_fingerprint"] != expected:
            raise MemoryConflictError(f"{kind} replay content does not match")

    @staticmethod
    def _scope_statement(
        statement: Any,
        table: Any,
        *,
        project_id: UUID | None,
        conversation_id: UUID | None,
        task_id: UUID | None,
        device_id: str | None,
    ) -> Any:
        values = {
            "project_id": project_id,
            "conversation_id": conversation_id,
            "task_id": task_id,
            "device_id": device_id,
        }
        for name, value in values.items():
            if value is not None and name in table.c:
                statement = statement.where(table.c[name] == str(value))
        return statement

    @staticmethod
    def _validate_read_scope(
        *,
        namespace: MemoryNamespace,
        project_id: UUID | None,
        conversation_id: UUID | None,
        task_id: UUID | None,
        device_id: str | None,
    ) -> None:
        required = {
            MemoryNamespace.PROJECT_CANONICAL: ("project_id", project_id),
            MemoryNamespace.CONVERSATION_DRAFT: ("conversation_id", conversation_id),
            MemoryNamespace.DEVICE_LOCAL: ("device_id", device_id),
            MemoryNamespace.TASK_EPISODE: ("task_id", task_id),
        }.get(namespace)
        if required is not None and required[1] is None:
            raise ValueError(f"{namespace.value} read requires {required[0]}")
        if namespace is MemoryNamespace.USER_PROFILE and any(
            value is not None for value in (project_id, conversation_id, task_id, device_id)
        ):
            raise ValueError("user_profile read cannot bind project, conversation, task, or device")

    @staticmethod
    def _observation_fingerprint(observation: MemoryObservation) -> str:
        return _fingerprint(
            {
                "project_id": str(observation.project_id) if observation.project_id else None,
                "conversation_id": str(observation.conversation_id),
                "task_id": str(observation.task_id),
                "version_id": str(observation.version_id) if observation.version_id else None,
                "scope_digest": observation.scope_digest,
                "source_event_id": str(observation.source_event_id),
                "source_cursor": observation.source_cursor,
                "source_type": observation.source_type.value,
                "content": observation.content,
                "content_hash": observation.content_hash,
                "proposed_namespace": observation.proposed_namespace.value,
                "authority": observation.authority.value,
                "confidence": observation.confidence,
                "sensitivity": observation.sensitivity.value,
                "actor": observation.actor,
            }
        )

    @staticmethod
    def _claim_fingerprint(claim: MemoryClaim) -> str:
        return _fingerprint(
            {
                "namespace": claim.namespace.value,
                "project_id": str(claim.project_id) if claim.project_id else None,
                "conversation_id": str(claim.conversation_id) if claim.conversation_id else None,
                "task_id": str(claim.task_id) if claim.task_id else None,
                "version_id": str(claim.version_id) if claim.version_id else None,
                "device_id": claim.device_id,
                "subject": claim.subject,
                "predicate": claim.predicate,
                "current_revision": claim.current_revision,
                "status": claim.status.value,
            }
        )

    @staticmethod
    def _revision_fingerprint(
        revision: MemoryClaimRevision,
        resolved_claim_ids: tuple[UUID, ...],
    ) -> str:
        return _fingerprint(
            {
                "claim_id": str(revision.claim_id),
                "revision": revision.revision,
                "value": revision.value,
                "normalized_text": revision.normalized_text,
                "source_observation_ids": [str(value) for value in revision.source_observation_ids],
                "source_event_ids": [str(value) for value in revision.source_event_ids],
                "authority": revision.authority.value,
                "confidence": revision.confidence,
                "valid_from": (revision.valid_from.isoformat() if revision.valid_from else None),
                "valid_to": revision.valid_to.isoformat() if revision.valid_to else None,
                "actor": revision.actor,
                "supersedes_revision": revision.supersedes_revision,
                "resolved_claim_ids": [str(value) for value in resolved_claim_ids],
            }
        )

    @staticmethod
    def _tombstone_fingerprint(tombstone: MemoryTombstone) -> str:
        return _fingerprint(
            {
                "target_kind": tombstone.target_kind.value,
                "target_id": str(tombstone.target_id),
                "reason": tombstone.reason,
                "actor": tombstone.actor,
                "source_event_id": str(tombstone.source_event_id),
            }
        )

    @staticmethod
    def _observation_from_row(row: Mapping[str, Any]) -> MemoryObservation:
        created_at = _datetime(row["created_at"])
        assert created_at is not None
        return MemoryObservation(
            id=UUID(row["id"]),
            project_id=_uuid(row["project_id"]),
            conversation_id=UUID(row["conversation_id"]),
            task_id=UUID(row["task_id"]),
            version_id=_uuid(row["version_id"]),
            scope_digest=row["scope_digest"],
            source_event_id=UUID(row["source_event_id"]),
            source_cursor=int(row["source_cursor"]),
            source_type=MemorySourceType(row["source_type"]),
            content=row["content"],
            content_hash=row["content_hash"],
            proposed_namespace=MemoryNamespace(row["proposed_namespace"]),
            authority=MemoryAuthority(row["authority"]),
            confidence=float(row["confidence"]),
            sensitivity=MemorySensitivity(row["sensitivity"]),
            scan_result=MemoryScanResult(row["scan_result"]),
            status=ObservationStatus(row["status"]),
            actor=row["actor"],
            created_at=created_at,
        )

    @staticmethod
    def _claim_from_row(row: Mapping[str, Any]) -> MemoryClaim:
        created_at = _datetime(row["created_at"])
        updated_at = _datetime(row["updated_at"])
        assert created_at is not None and updated_at is not None
        return MemoryClaim.restore(
            id=UUID(row["id"]),
            namespace=MemoryNamespace(row["namespace"]),
            project_id=_uuid(row["project_id"]),
            conversation_id=_uuid(row["conversation_id"]),
            task_id=_uuid(row["task_id"]),
            version_id=_uuid(row["version_id"]),
            device_id=row["device_id"],
            subject=row["subject"],
            predicate=row["predicate"],
            current_revision=int(row["current_revision"]),
            conflict_set_id=_uuid(row["conflict_set_id"]),
            status=ClaimStatus(row["status"]),
            created_at=created_at,
            updated_at=updated_at,
        )

    @staticmethod
    def _revision_from_row(row: Mapping[str, Any]) -> MemoryClaimRevision:
        valid_from = _datetime(row["valid_from"])
        valid_to = _datetime(row["valid_to"])
        recorded_at = _datetime(row["recorded_at"])
        assert recorded_at is not None
        return MemoryClaimRevision.create(
            claim_id=UUID(row["claim_id"]),
            revision=int(row["revision"]),
            value=row["value"],
            normalized_text=row["normalized_text"],
            source_observation_ids=tuple(UUID(value) for value in row["source_observation_ids"]),
            source_event_ids=tuple(UUID(value) for value in row["source_event_ids"]),
            authority=MemoryAuthority(row["authority"]),
            confidence=float(row["confidence"]),
            valid_from=valid_from,
            valid_to=valid_to,
            recorded_at=recorded_at,
            actor=row["actor"],
            supersedes_revision=(
                int(row["supersedes_revision"]) if row["supersedes_revision"] is not None else None
            ),
            resolved_claim_ids=tuple(UUID(value) for value in row["resolved_claim_ids"]),
        )

    @staticmethod
    def _tombstone_from_row(row: Mapping[str, Any]) -> MemoryTombstone:
        created_at = _datetime(row["created_at"])
        assert created_at is not None
        return MemoryTombstone(
            id=UUID(row["id"]),
            target_kind=MemoryTargetKind(row["target_kind"]),
            target_id=UUID(row["target_id"]),
            reason=row["reason"],
            actor=row["actor"],
            source_event_id=UUID(row["source_event_id"]),
            created_at=created_at,
        )


__all__ = ["SqlAlchemyMemoryRepository"]
