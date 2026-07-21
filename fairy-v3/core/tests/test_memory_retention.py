from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import update

from fairy_core.commanding.models import EventVisibility
from fairy_core.commanding.registry import RiskLevel
from fairy_core.commanding.sqlalchemy import SqlAlchemyCommandLedger
from fairy_core.memory.models import (
    ClaimStatus,
    MemoryAuthority,
    MemoryClaim,
    MemoryClaimRevision,
    MemoryNamespace,
    MemoryObservation,
    MemoryScanResult,
    MemorySensitivity,
    ObservationStatus,
)
from fairy_core.memory.retention import MemoryRetentionCoordinator
from fairy_core.memory.retrieval_models import (
    MemorySelectionReason,
    MemorySnapshot,
    MemorySnapshotItem,
    MemorySnapshotStatus,
    MemorySourceKind,
    ProjectionState,
)
from fairy_core.memory.schema import memory_claims
from fairy_core.memory.snapshot_sqlalchemy import SqlAlchemyMemorySnapshotRepository
from fairy_core.memory.sqlalchemy import SqlAlchemyMemoryRepository
from fairy_core.persistence import SqlAlchemyUnitOfWorkFactory, create_sqlite_core_engine
from fairy_core.storage.sqlalchemy import SqlAlchemyStateStore
from tests.memory_support import build_memory_domain_context

NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
OLD = NOW - timedelta(days=366)


def _fingerprint(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _seed_old_memory(tmp_path: Path, *, label: str):
    engine = create_sqlite_core_engine(tmp_path / f"{label}.db")
    project, base, conversation, task, draft, scope = build_memory_domain_context(tmp_path, label)
    state = SqlAlchemyStateStore(engine, tenant_id="local")
    state.save_project(project)
    state.save_version(base)
    state.save_conversation(conversation)
    state.save_task(task, idempotency_key=f"{label}:task")
    state.save_version(draft)
    ledger = SqlAlchemyCommandLedger(engine, tenant_id="local")
    run = ledger.create_run(
        command_name="memory.observe",
        actor="user:test",
        scope=scope,
        input_payload={},
        risk_level=RiskLevel.LOW,
        idempotency_key=f"{label}:event",
    )
    event = ledger.events_for_run(run.id)[0]
    observation = replace(
        MemoryObservation.create(
            scope=scope,
            source_event_id=event.id,
            source_cursor=event.cursor,
            source_type="user_message",
            content="The retained project uses PostgreSQL.",
            proposed_namespace=MemoryNamespace.PROJECT_CANONICAL,
            authority=MemoryAuthority.EXPLICIT_USER,
            confidence=1.0,
            sensitivity=MemorySensitivity.PRIVATE,
            actor="user:test",
        ),
        status=ObservationStatus.PROMOTED,
        scan_result=MemoryScanResult.CLEAN,
        created_at=OLD,
    )
    repository = SqlAlchemyMemoryRepository(engine, tenant_id="local")
    repository.append_observation(
        observation,
        request_fingerprint=_fingerprint(f"{label}:observation"),
    )
    claim = MemoryClaim.create(
        namespace=MemoryNamespace.PROJECT_CANONICAL,
        project_id=project.id,
        subject="project",
        predicate="database",
    )
    repository.create_claim(claim, request_fingerprint=_fingerprint(f"{label}:claim"))
    repository.append_revision(
        claim.id,
        expected_revision=0,
        revision=MemoryClaimRevision.create(
            claim_id=claim.id,
            revision=1,
            value="PostgreSQL",
            normalized_text="PostgreSQL project database",
            source_observation_ids=(observation.id,),
            source_event_ids=(event.id,),
            authority=MemoryAuthority.EXPLICIT_USER,
            confidence=1.0,
            actor="user:test",
        ),
        request_fingerprint=_fingerprint(f"{label}:revision"),
    )
    with engine.begin() as connection:
        connection.execute(
            update(memory_claims)
            .where(memory_claims.c.tenant_id == "local", memory_claims.c.id == str(claim.id))
            .values(created_at=OLD, updated_at=OLD)
        )
    return engine, scope, observation, claim, repository, ledger


def test_retention_tombstones_then_purges_unreferenced_payloads(tmp_path: Path) -> None:
    engine, _scope, observation, claim, repository, ledger = _seed_old_memory(
        tmp_path, label="retention-purge"
    )
    coordinator = MemoryRetentionCoordinator(
        SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local"),
        clock=lambda: NOW,
        purge_grace=timedelta(0),
    )

    result = coordinator.run_if_due(force=True)

    assert result is not None
    assert (result.expired, result.purged_observations, result.purged_claims) == (2, 1, 1)
    retained_observation = repository.get_observation(observation.id, include_forgotten=True)
    retained_claim = repository.get_claim(claim.id, include_forgotten=True)
    revisions = repository.revisions_for_claim(claim.id)
    assert retained_observation is not None
    assert retained_observation.status is ObservationStatus.FORGOTTEN
    assert retained_observation.content == "[purged]"
    assert retained_claim is not None
    assert retained_claim.status is ClaimStatus.FORGOTTEN
    assert (retained_claim.subject, retained_claim.predicate) == ("[purged]", "[purged]")
    assert revisions[0].value == {"purged": True}
    events = ledger.events_after(
        cursor=0,
        allowed_visibilities={EventVisibility.INTERNAL},
    )
    assert [event.event_type for event in events].count("memory.retention.expired") == 2
    replay = coordinator.run_if_due(force=True)
    assert replay is not None and (replay.expired, replay.purged) == (0, 0)
    engine.dispose()


def test_retention_keeps_payload_while_an_immutable_snapshot_references_it(
    tmp_path: Path,
) -> None:
    engine, scope, observation, _claim, repository, _ledger = _seed_old_memory(
        tmp_path, label="retention-snapshot"
    )
    item = MemorySnapshotItem.create(
        ordinal=0,
        source_kind=MemorySourceKind.OBSERVATION,
        source_id=observation.id,
        source_revision=None,
        namespace=MemoryNamespace.PROJECT_CANONICAL,
        selection_reason=MemorySelectionReason.CANONICAL,
        authority=MemoryAuthority.EXPLICIT_USER,
        score_components={"scope": 1.0},
        rendered_text="The retained project uses PostgreSQL.",
        token_count=7,
    )
    snapshot = MemorySnapshot.create(
        project_id=scope.project_id,
        conversation_id=scope.conversation_id,
        task_id=scope.task_id,
        base_version_id=scope.base_version_id,
        target_version_id=scope.target_version_id,
        policy_version="retention-test-v1",
        source_watermark_cursor=observation.source_cursor,
        projection_generation=1,
        projection_watermark_cursor=observation.source_cursor,
        projection_state=ProjectionState.READY,
        status=MemorySnapshotStatus.READY,
        degraded_reason=None,
        items=(item,),
    )
    SqlAlchemyMemorySnapshotRepository(engine, tenant_id="local").append(
        snapshot,
        request_fingerprint=_fingerprint("retention-snapshot:snapshot"),
    )
    coordinator = MemoryRetentionCoordinator(
        SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local"),
        clock=lambda: NOW,
        purge_grace=timedelta(0),
    )

    result = coordinator.run_if_due(force=True)

    assert result is not None
    assert result.expired == 2
    assert result.purged_observations == 0
    retained = repository.get_observation(observation.id, include_forgotten=True)
    assert retained is not None
    assert retained.status is ObservationStatus.FORGOTTEN
    assert retained.content == "The retained project uses PostgreSQL."
    engine.dispose()
