from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy.engine import Engine

from fairy_core.domain.errors import IdempotencyConflictError, MemoryConflictError
from fairy_core.domain.ids import new_id
from fairy_core.memory.models import MemoryAuthority, MemoryNamespace
from fairy_core.memory.retrieval_models import (
    MemorySelectionReason,
    MemorySnapshot,
    MemorySnapshotItem,
    MemorySnapshotStatus,
    MemorySourceKind,
    ProjectionState,
)
from fairy_core.memory.snapshot_sqlalchemy import SqlAlchemyMemorySnapshotRepository
from fairy_core.persistence import SqlAlchemyUnitOfWorkFactory
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.storage.sqlalchemy import SqlAlchemyStateStore
from tests.memory_support import build_memory_domain_context


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    value = create_sqlite_core_engine(tmp_path / "snapshots.db")
    yield value
    value.dispose()


def _fingerprint(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _seed_task(engine: Engine, tmp_path: Path, *, tenant_id: str, name: str):
    project, base, conversation, task, draft, _scope = build_memory_domain_context(
        tmp_path,
        name,
    )
    state = SqlAlchemyStateStore(engine, tenant_id=tenant_id)
    state.save_project(project)
    state.save_version(base)
    state.save_conversation(conversation)
    state.save_task(task, idempotency_key=f"task:{name}")
    state.save_version(draft)
    return project, base, conversation, task, draft


def _snapshot(project, conversation, task, draft, *, text: str = "React is required"):
    item = MemorySnapshotItem.create(
        ordinal=0,
        source_kind=MemorySourceKind.CLAIM_REVISION,
        source_id=new_id(),
        source_revision=1,
        namespace=MemoryNamespace.PROJECT_CANONICAL,
        selection_reason=MemorySelectionReason.EXACT_CANONICAL,
        authority=MemoryAuthority.ACCEPTED_VERSION,
        score_components={"authority": 1.0, "exact": 1.0},
        rendered_text=text,
        token_count=len(text.encode("utf-8")),
    )
    return MemorySnapshot.create(
        project_id=project.id,
        conversation_id=conversation.id,
        task_id=task.id,
        base_version_id=task.base_version_id,
        target_version_id=draft.id,
        policy_version="hermes-lexical-v1",
        source_watermark_cursor=7,
        projection_generation=1,
        projection_watermark_cursor=7,
        projection_state=ProjectionState.READY,
        status=MemorySnapshotStatus.READY,
        degraded_reason=None,
        items=(item,),
    )


def test_snapshot_append_and_task_binding_are_recovered_in_item_order(
    engine: Engine,
    tmp_path: Path,
) -> None:
    project, _base, conversation, task, draft = _seed_task(
        engine,
        tmp_path,
        tenant_id="tenant-a",
        name="recover",
    )
    snapshot = _snapshot(project, conversation, task, draft)
    repository = SqlAlchemyMemorySnapshotRepository(engine, tenant_id="tenant-a")
    state = SqlAlchemyStateStore(engine, tenant_id="tenant-a")

    persisted = repository.append(snapshot, request_fingerprint=_fingerprint("recover"))
    task.bind_memory_snapshot(persisted.id, persisted.content_hash)
    state.save_task(task)

    recovered = repository.get(snapshot.id, task_id=task.id)
    assert recovered == persisted
    assert repository.get_for_task(task.id) == persisted
    assert [item.ordinal for item in recovered.items] == [0]
    assert state.get_task(task.id) == task


def test_snapshot_request_replay_is_idempotent_and_content_sensitive(
    engine: Engine,
    tmp_path: Path,
) -> None:
    project, _base, conversation, task, draft = _seed_task(
        engine,
        tmp_path,
        tenant_id="tenant-a",
        name="replay",
    )
    repository = SqlAlchemyMemorySnapshotRepository(engine, tenant_id="tenant-a")
    snapshot = _snapshot(project, conversation, task, draft)
    fingerprint = _fingerprint("snapshot:replay")

    first = repository.append(snapshot, request_fingerprint=fingerprint)
    replay = repository.append(
        replace(
            snapshot,
            id=new_id(),
            created_at=snapshot.created_at + timedelta(seconds=1),
        ),
        request_fingerprint=fingerprint,
    )

    assert replay.id == first.id
    with pytest.raises(IdempotencyConflictError):
        repository.append(
            _snapshot(project, conversation, task, draft, text="Vue is required"),
            request_fingerprint=fingerprint,
        )


def test_task_cannot_receive_a_second_snapshot(
    engine: Engine,
    tmp_path: Path,
) -> None:
    project, _base, conversation, task, draft = _seed_task(
        engine,
        tmp_path,
        tenant_id="tenant-a",
        name="one-per-task",
    )
    repository = SqlAlchemyMemorySnapshotRepository(engine, tenant_id="tenant-a")
    repository.append(
        _snapshot(project, conversation, task, draft),
        request_fingerprint=_fingerprint("snapshot:first"),
    )

    with pytest.raises(MemoryConflictError, match="already has"):
        repository.append(
            _snapshot(project, conversation, task, draft, text="Different"),
            request_fingerprint=_fingerprint("snapshot:second"),
        )


def test_snapshot_ids_and_fingerprints_are_tenant_scoped(
    engine: Engine,
    tmp_path: Path,
) -> None:
    project, base, conversation, task, draft = _seed_task(
        engine,
        tmp_path,
        tenant_id="tenant-a",
        name="tenant",
    )
    snapshot = _snapshot(project, conversation, task, draft)
    fingerprint = _fingerprint("same")
    tenant_a = SqlAlchemyMemorySnapshotRepository(engine, tenant_id="tenant-a")
    tenant_b = SqlAlchemyMemorySnapshotRepository(engine, tenant_id="tenant-b")
    state_b = SqlAlchemyStateStore(engine, tenant_id="tenant-b")
    state_b.save_project(project)
    state_b.save_version(base)
    state_b.save_conversation(conversation)
    state_b.save_task(task, idempotency_key="task:tenant")
    state_b.save_version(draft)

    tenant_a.append(snapshot, request_fingerprint=fingerprint)
    tenant_b.append(snapshot, request_fingerprint=fingerprint)

    assert tenant_a.get(snapshot.id, task_id=task.id) == snapshot
    assert tenant_b.get(snapshot.id, task_id=task.id) == snapshot


def test_snapshot_participates_in_unit_of_work_rollback(
    engine: Engine,
    tmp_path: Path,
) -> None:
    project, base, conversation, task, draft, _scope = build_memory_domain_context(
        tmp_path,
        "rollback-snapshot",
    )
    snapshot = _snapshot(project, conversation, task, draft)
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="tenant-a")

    with pytest.raises(RuntimeError, match="crash"), factory() as unit_of_work:
        unit_of_work.state.save_project(project)
        unit_of_work.state.save_version(base)
        unit_of_work.state.save_conversation(conversation)
        unit_of_work.state.save_task(task, idempotency_key="task:rollback-snapshot")
        unit_of_work.state.save_version(draft)
        unit_of_work.snapshots.append(
            snapshot,
            request_fingerprint=_fingerprint("snapshot:rollback"),
        )
        raise RuntimeError("crash")

    with factory() as unit_of_work:
        assert unit_of_work.state.get_task(task.id) is None
        assert unit_of_work.snapshots.get_for_task(task.id) is None
