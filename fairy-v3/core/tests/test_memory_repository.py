from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from fairy_core.commanding.registry import RiskLevel
from fairy_core.commanding.schema import command_metadata
from fairy_core.commanding.sqlalchemy import SqlAlchemyCommandLedger
from fairy_core.domain.errors import MemoryConflictError, MemoryForgottenError
from fairy_core.domain.ids import new_id
from fairy_core.domain.models import ScopeContract
from fairy_core.memory.models import (
    ClaimStatus,
    MemoryAuthority,
    MemoryClaim,
    MemoryClaimRevision,
    MemoryNamespace,
    MemoryObservation,
    MemoryScanResult,
    MemorySensitivity,
    MemoryTargetKind,
    MemoryTombstone,
    ObservationStatus,
    memory_content_hash,
)
from fairy_core.memory.schema import memory_claim_revisions, memory_metadata
from fairy_core.memory.sqlalchemy import SqlAlchemyMemoryRepository
from fairy_core.storage.schema import state_metadata
from fairy_core.storage.sqlalchemy import SqlAlchemyStateStore
from tests.memory_support import build_memory_domain_context


@pytest.fixture
def engine() -> Engine:
    value = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    state_metadata.create_all(value)
    command_metadata.create_all(value)
    memory_metadata.create_all(value)
    yield value
    value.dispose()


def _fingerprint(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _seed_scope_and_event(engine: Engine, tenant_id: str, tmp_path: Path, name: str):
    project, base, conversation, task, draft, scope = build_memory_domain_context(tmp_path, name)
    state = SqlAlchemyStateStore(engine, tenant_id=tenant_id)
    state.save_project(project)
    state.save_conversation(conversation)
    state.save_version(base)
    state.save_task(task, idempotency_key=f"task:{name}")
    state.save_version(draft)
    ledger = SqlAlchemyCommandLedger(engine, tenant_id=tenant_id)
    run = ledger.create_run(
        command_name="memory.observe",
        actor="user:test",
        scope=scope,
        input_payload={"content": "React Aria is the accessibility layer."},
        risk_level=RiskLevel.LOW,
        idempotency_key=f"command:{name}",
    )
    event = next(event for event in ledger.events_after(cursor=0) if event.run_id == run.id)
    return scope, event


def _observation(scope: ScopeContract, event) -> MemoryObservation:
    return MemoryObservation.create(
        scope=scope,
        source_event_id=event.id,
        source_cursor=event.cursor,
        source_type="user_message",
        content="React Aria is the accessibility layer.",
        proposed_namespace=MemoryNamespace.PROJECT_CANONICAL,
        authority=MemoryAuthority.EXPLICIT_USER,
        confidence=1.0,
        sensitivity=MemorySensitivity.PRIVATE,
        actor="user:test",
    )


def _claim(scope: ScopeContract, *, status: ClaimStatus = ClaimStatus.CANDIDATE) -> MemoryClaim:
    claim = MemoryClaim.create(
        namespace=MemoryNamespace.PROJECT_CANONICAL,
        project_id=scope.project_id,
        subject="project",
        predicate="accessibility_framework",
    )
    if status is not ClaimStatus.CANDIDATE:
        claim.transition_to(status)
    return claim


def _revision(
    claim: MemoryClaim,
    observation: MemoryObservation,
    event,
    revision: int,
    *,
    value: str = "React Aria",
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
) -> MemoryClaimRevision:
    return MemoryClaimRevision.create(
        claim_id=claim.id,
        revision=revision,
        value=value,
        normalized_text=value.casefold(),
        source_observation_ids=(observation.id,),
        source_event_ids=(event.id,),
        authority=MemoryAuthority.EXPLICIT_USER,
        confidence=1.0,
        actor="user:test",
        valid_from=valid_from,
        valid_to=valid_to,
        supersedes_revision=revision - 1 if revision > 1 else None,
    )


def _clone_claim(claim: MemoryClaim) -> MemoryClaim:
    return MemoryClaim.restore(
        id=claim.id,
        namespace=claim.namespace,
        project_id=claim.project_id,
        conversation_id=claim.conversation_id,
        task_id=claim.task_id,
        version_id=claim.version_id,
        device_id=claim.device_id,
        subject=claim.subject,
        predicate=claim.predicate,
        current_revision=claim.current_revision,
        conflict_set_id=claim.conflict_set_id,
        status=claim.status,
        created_at=claim.created_at,
        updated_at=claim.updated_at,
    )


def test_repository_scopes_identical_claim_ids_and_fingerprints_by_tenant(
    engine: Engine,
) -> None:
    claim = MemoryClaim.create(
        namespace=MemoryNamespace.USER_PROFILE,
        subject="user",
        predicate="interface_density",
    )
    fingerprint = _fingerprint("same-request")
    tenant_a = SqlAlchemyMemoryRepository(engine, tenant_id="tenant-a")
    tenant_b = SqlAlchemyMemoryRepository(engine, tenant_id="tenant-b")

    tenant_a.create_claim(claim, request_fingerprint=fingerprint)
    tenant_b.create_claim(_clone_claim(claim), request_fingerprint=fingerprint)

    assert tenant_a.get_claim(claim.id) is not None
    assert tenant_b.get_claim(claim.id) is not None


def test_observation_replay_is_idempotent_and_detects_changed_content(
    engine: Engine,
    tmp_path: Path,
) -> None:
    scope, event = _seed_scope_and_event(engine, "tenant-a", tmp_path, "replay")
    repository = SqlAlchemyMemoryRepository(engine, tenant_id="tenant-a")
    observation = _observation(scope, event)
    fingerprint = _fingerprint("observe:replay")

    first = repository.append_observation(
        observation,
        request_fingerprint=fingerprint,
    )
    replay = repository.append_observation(
        replace(observation, id=new_id()),
        request_fingerprint=fingerprint,
    )
    changed_content = "Use a different accessibility layer."

    assert replay.id == first.id
    with pytest.raises(MemoryConflictError):
        repository.append_observation(
            replace(
                observation,
                id=new_id(),
                content=changed_content,
                content_hash=memory_content_hash(changed_content),
            ),
            request_fingerprint=fingerprint,
        )


def test_bounded_retrieval_filters_unsafe_statuses_before_limit(
    engine: Engine,
    tmp_path: Path,
) -> None:
    scope, event = _seed_scope_and_event(engine, "tenant-a", tmp_path, "bounded-read")
    repository = SqlAlchemyMemoryRepository(engine, tenant_id="tenant-a")
    base = _observation(scope, event)
    accepted = replace(
        base,
        id=UUID(int=1),
        status=ObservationStatus.ACCEPTED,
        scan_result=MemoryScanResult.CLEAN,
    )
    pending = replace(base, id=UUID(int=2))
    secret = replace(
        base,
        id=UUID(int=3),
        status=ObservationStatus.ACCEPTED,
        sensitivity=MemorySensitivity.SECRET,
        scan_result=MemoryScanResult.SECRET_BLOCKED,
    )
    for index, observation in enumerate((accepted, pending, secret)):
        repository.append_observation(
            observation,
            request_fingerprint=_fingerprint(f"bounded-read:{index}"),
        )

    values = repository.observations_for_scope(
        namespace=MemoryNamespace.PROJECT_CANONICAL,
        project_id=scope.project_id,
        limit=1,
        newest_first=True,
        retrievable_only=True,
    )

    assert [observation.id for observation in values] == [accepted.id]


def test_observation_rejects_forged_source_cursor(
    engine: Engine,
    tmp_path: Path,
) -> None:
    scope, event = _seed_scope_and_event(engine, "tenant-a", tmp_path, "provenance")
    repository = SqlAlchemyMemoryRepository(engine, tenant_id="tenant-a")
    observation = replace(_observation(scope, event), source_cursor=event.cursor + 1)

    with pytest.raises(MemoryConflictError, match="provenance"):
        repository.append_observation(
            observation,
            request_fingerprint=_fingerprint("observe:forged-cursor"),
        )

    assert repository.get_observation(observation.id) is None


def test_revision_append_uses_compare_and_swap_and_replay_fingerprint(
    engine: Engine,
    tmp_path: Path,
) -> None:
    scope, event = _seed_scope_and_event(engine, "tenant-a", tmp_path, "revisions")
    repository = SqlAlchemyMemoryRepository(engine, tenant_id="tenant-a")
    observation = repository.append_observation(
        _observation(scope, event),
        request_fingerprint=_fingerprint("observe:revisions"),
    )
    claim = _claim(scope)
    repository.create_claim(claim, request_fingerprint=_fingerprint("claim:revisions"))
    first_revision = _revision(claim, observation, event, 1)

    first = repository.append_revision(
        claim.id,
        expected_revision=0,
        revision=first_revision,
        request_fingerprint=_fingerprint("revision:1"),
    )
    replay = repository.append_revision(
        claim.id,
        expected_revision=0,
        revision=first_revision,
        request_fingerprint=_fingerprint("revision:1"),
    )
    reconstructed_replay = repository.append_revision(
        claim.id,
        expected_revision=0,
        revision=replace(
            first_revision,
            recorded_at=first_revision.recorded_at + timedelta(seconds=1),
        ),
        request_fingerprint=_fingerprint("revision:1"),
    )

    assert first.current_revision == 1
    assert first.status is ClaimStatus.ACTIVE
    assert replay.current_revision == 1
    assert reconstructed_replay.current_revision == 1
    with pytest.raises(MemoryConflictError):
        repository.append_revision(
            claim.id,
            expected_revision=0,
            revision=_revision(claim, observation, event, 2),
            request_fingerprint=_fingerprint("revision:stale"),
        )

    updated = repository.append_revision(
        claim.id,
        expected_revision=1,
        revision=_revision(claim, observation, event, 2),
        request_fingerprint=_fingerprint("revision:2"),
    )
    revisions = repository.revisions_for_claim(claim.id)
    with engine.connect() as connection:
        current_count = connection.execute(
            select(func.count())
            .select_from(memory_claim_revisions)
            .where(
                memory_claim_revisions.c.tenant_id == "tenant-a",
                memory_claim_revisions.c.claim_id == str(claim.id),
                memory_claim_revisions.c.is_current.is_(True),
            )
        ).scalar_one()

    assert updated.current_revision == 2
    assert [item.revision for item in revisions] == [1, 2]
    assert current_count == 1


def test_conflict_resolution_retains_conflict_set(
    engine: Engine,
    tmp_path: Path,
) -> None:
    scope, event = _seed_scope_and_event(engine, "tenant-a", tmp_path, "conflict")
    repository = SqlAlchemyMemoryRepository(engine, tenant_id="tenant-a")
    observation = repository.append_observation(
        _observation(scope, event),
        request_fingerprint=_fingerprint("observe:conflict"),
    )
    claim = _claim(scope, status=ClaimStatus.CONFLICTED)
    repository.create_claim(claim, request_fingerprint=_fingerprint("claim:conflict"))
    repository.append_revision(
        claim.id,
        expected_revision=0,
        revision=_revision(claim, observation, event, 1),
        request_fingerprint=_fingerprint("conflict:revision:1"),
    )

    resolved = repository.resolve_conflict(
        claim.id,
        expected_revision=1,
        revision=_revision(claim, observation, event, 2, value="React Aria 4"),
        resolved_claim_ids=(claim.id,),
        request_fingerprint=_fingerprint("conflict:resolve"),
    )
    replay = repository.resolve_conflict(
        claim.id,
        expected_revision=1,
        revision=_revision(claim, observation, event, 2, value="React Aria 4"),
        resolved_claim_ids=(claim.id,),
        request_fingerprint=_fingerprint("conflict:resolve"),
    )
    revisions = repository.revisions_for_claim(claim.id)

    assert resolved.status is ClaimStatus.ACTIVE
    assert replay.status is ClaimStatus.ACTIVE
    assert resolved.conflict_set_id == claim.conflict_set_id
    assert revisions[-1].resolved_claim_ids == (claim.id,)


def test_scoped_reads_exclude_expired_and_unrelated_claims(
    engine: Engine,
    tmp_path: Path,
) -> None:
    scope, event = _seed_scope_and_event(engine, "tenant-a", tmp_path, "scope")
    repository = SqlAlchemyMemoryRepository(engine, tenant_id="tenant-a")
    observation = repository.append_observation(
        _observation(scope, event),
        request_fingerprint=_fingerprint("observe:scope"),
    )
    claim = _claim(scope)
    repository.create_claim(claim, request_fingerprint=_fingerprint("claim:scope"))
    now = datetime.now(UTC)
    expired = repository.append_revision(
        claim.id,
        expected_revision=0,
        revision=_revision(
            claim,
            observation,
            event,
            1,
            valid_from=now - timedelta(days=2),
            valid_to=now - timedelta(days=1),
        ),
        request_fingerprint=_fingerprint("revision:expired"),
    )

    exact_scope = repository.claims_for_scope(
        namespace=MemoryNamespace.PROJECT_CANONICAL,
        project_id=scope.project_id,
    )
    wrong_scope = repository.claims_for_scope(
        namespace=MemoryNamespace.PROJECT_CANONICAL,
        project_id=new_id(),
    )

    assert expired.status is ClaimStatus.EXPIRED
    assert exact_scope == []
    assert wrong_scope == []


def test_scoped_reads_exclude_claims_that_are_not_yet_valid(
    engine: Engine,
    tmp_path: Path,
) -> None:
    scope, event = _seed_scope_and_event(engine, "tenant-a", tmp_path, "future")
    repository = SqlAlchemyMemoryRepository(engine, tenant_id="tenant-a")
    observation = repository.append_observation(
        _observation(scope, event),
        request_fingerprint=_fingerprint("observe:future"),
    )
    claim = _claim(scope)
    repository.create_claim(claim, request_fingerprint=_fingerprint("claim:future"))
    repository.append_revision(
        claim.id,
        expected_revision=0,
        revision=_revision(
            claim,
            observation,
            event,
            1,
            valid_from=datetime.now(UTC) + timedelta(days=1),
        ),
        request_fingerprint=_fingerprint("revision:future"),
    )

    assert (
        repository.claims_for_scope(
            namespace=MemoryNamespace.PROJECT_CANONICAL,
            project_id=scope.project_id,
        )
        == []
    )


def test_forget_writes_tombstone_before_suppressing_claim(
    engine: Engine,
    tmp_path: Path,
) -> None:
    scope, event = _seed_scope_and_event(engine, "tenant-a", tmp_path, "forget")
    repository = SqlAlchemyMemoryRepository(engine, tenant_id="tenant-a")
    observation = repository.append_observation(
        _observation(scope, event),
        request_fingerprint=_fingerprint("observe:forget"),
    )
    claim = _claim(scope)
    repository.create_claim(claim, request_fingerprint=_fingerprint("claim:forget"))
    repository.append_revision(
        claim.id,
        expected_revision=0,
        revision=_revision(claim, observation, event, 1),
        request_fingerprint=_fingerprint("revision:forget"),
    )
    tombstone = MemoryTombstone.create(
        target_kind=MemoryTargetKind.CLAIM,
        target_id=claim.id,
        reason="User requested deletion",
        actor="user:test",
        source_event_id=event.id,
    )

    forgotten = repository.forget(
        tombstone,
        request_fingerprint=_fingerprint("forget:claim"),
    )
    replay = repository.forget(
        replace(tombstone, id=new_id(), created_at=tombstone.created_at + timedelta(seconds=1)),
        request_fingerprint=_fingerprint("forget:claim"),
    )

    assert forgotten == tombstone
    assert replay == tombstone
    assert (
        repository.get_tombstone(
            target_kind=MemoryTargetKind.CLAIM,
            target_id=claim.id,
        )
        == tombstone
    )
    with pytest.raises(MemoryForgottenError):
        repository.get_claim(claim.id)
    assert (
        repository.claims_for_scope(
            namespace=MemoryNamespace.PROJECT_CANONICAL,
            project_id=scope.project_id,
        )
        == []
    )
