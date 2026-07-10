from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from fairy_core.commanding.registry import RiskLevel
from fairy_core.domain.models import Task
from fairy_core.memory.models import (
    MemoryAuthority,
    MemoryNamespace,
    MemoryObservation,
    MemoryScanResult,
    MemorySensitivity,
    ObservationStatus,
)
from fairy_core.memory.projection import LexicalProjectionRefresher
from fairy_core.memory.retrieval_models import (
    MemorySnapshot,
    MemorySnapshotStatus,
    ProjectionState,
)
from fairy_core.persistence import SqlAlchemyUnitOfWorkFactory
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from tests.memory_support import build_memory_domain_context


def _fingerprint(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _seed(tmp_path: Path):
    engine = create_sqlite_core_engine(tmp_path / "recovery.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    project, base, conversation, task, draft, scope = build_memory_domain_context(
        tmp_path,
        "retrieval-recovery",
    )
    with factory() as unit_of_work:
        unit_of_work.state.save_project(project)
        unit_of_work.state.save_version(base)
        unit_of_work.state.save_conversation(conversation)
        unit_of_work.state.save_task(task, idempotency_key="retrieval-recovery:task")
        unit_of_work.state.save_version(draft)
        run = unit_of_work.commands.create_run(
            command_name="memory.observe",
            actor="user:test",
            scope=scope,
            input_payload={},
            risk_level=RiskLevel.LOW,
            idempotency_key="retrieval-recovery:event",
        )
        event = unit_of_work.commands.events_for_run(run.id)[0]
        observation = replace(
            MemoryObservation.create(
                scope=scope,
                source_event_id=event.id,
                source_cursor=event.cursor,
                source_type="user_message",
                content="Recover compact navigation memory.",
                proposed_namespace=MemoryNamespace.CONVERSATION_DRAFT,
                authority=MemoryAuthority.EXPLICIT_USER,
                confidence=1.0,
                sensitivity=MemorySensitivity.PRIVATE,
                actor="user:test",
            ),
            status=ObservationStatus.ACCEPTED,
            scan_result=MemoryScanResult.CLEAN,
        )
        unit_of_work.memory.append_observation(
            observation,
            request_fingerprint=_fingerprint("retrieval-recovery:observation"),
        )
        unit_of_work.commit()
    return engine, factory, task, scope, observation


def test_projection_upsert_rolls_back_when_checkpoint_crashes(tmp_path: Path) -> None:
    engine, factory, _task, scope, observation = _seed(tmp_path)

    class FailingCheckpointWriter:
        def __init__(self, delegate) -> None:
            self.delegate = delegate

        def upsert_documents(self, documents) -> None:
            self.delegate.upsert_documents(documents)

        def remove_source(self, **values) -> None:
            self.delegate.remove_source(**values)

        def advance_checkpoint(self, **_values):
            raise RuntimeError("checkpoint crash")

    with (
        pytest.raises(RuntimeError, match="checkpoint crash"),
        factory() as unit_of_work,
    ):
        LexicalProjectionRefresher(
            memory_repository=unit_of_work.memory,
            projection_writer=FailingCheckpointWriter(unit_of_work.memory_projections),
            command_ledger=unit_of_work.commands,
        ).refresh()
        unit_of_work.commit()

    with factory() as unit_of_work:
        assert (
            unit_of_work.memory_search.search(
                scope=scope,
                query="compact navigation",
                generation=1,
                limit=10,
            )
            == ()
        )
        assert (
            unit_of_work.memory_search.health(
                generation=1,
                source_watermark_cursor=unit_of_work.commands.current_cursor(),
            ).state
            is ProjectionState.UNAVAILABLE
        )

    with factory() as unit_of_work:
        LexicalProjectionRefresher(
            memory_repository=unit_of_work.memory,
            projection_writer=unit_of_work.memory_projections,
            command_ledger=unit_of_work.commands,
        ).refresh()
        unit_of_work.commit()
    with factory() as unit_of_work:
        hits = unit_of_work.memory_search.search(
            scope=scope,
            query="compact navigation",
            generation=1,
            limit=10,
        )
        assert [hit.document.source_id for hit in hits] == [observation.id]
    engine.dispose()


def test_snapshot_insert_and_task_binding_recover_atomically(tmp_path: Path) -> None:
    engine, factory, task, scope, _observation = _seed(tmp_path)
    snapshot = MemorySnapshot.create(
        project_id=scope.project_id,
        conversation_id=scope.conversation_id,
        task_id=scope.task_id,
        base_version_id=scope.base_version_id,
        target_version_id=scope.target_version_id,
        policy_version="hermes-lexical-v1",
        source_watermark_cursor=0,
        projection_generation=1,
        projection_watermark_cursor=0,
        projection_state=ProjectionState.UNAVAILABLE,
        status=MemorySnapshotStatus.DEGRADED,
        degraded_reason="MEMORY_PROJECTION_UNAVAILABLE",
        items=(),
    )
    fingerprint = _fingerprint("retrieval-recovery:snapshot")

    with (
        pytest.raises(RuntimeError, match="binding crash"),
        factory() as unit_of_work,
    ):
        unit_of_work.snapshots.append(
            snapshot,
            request_fingerprint=fingerprint,
        )
        raise RuntimeError("binding crash")

    with factory() as unit_of_work:
        assert unit_of_work.snapshots.get_for_task(task.id) is None
        unbound = unit_of_work.state.get_task(task.id)
        assert unbound is not None
        assert unbound.memory_snapshot_id is None

    with factory() as unit_of_work:
        persisted = unit_of_work.snapshots.append(
            snapshot,
            request_fingerprint=fingerprint,
        )
        recovered_task = unit_of_work.state.get_task(task.id)
        assert isinstance(recovered_task, Task)
        recovered_task.bind_memory_snapshot(persisted.id, persisted.content_hash)
        unit_of_work.state.save_task(recovered_task)
        unit_of_work.commit()
    with factory() as unit_of_work:
        replayed = unit_of_work.snapshots.append(
            snapshot,
            request_fingerprint=fingerprint,
        )
        recovered_task = unit_of_work.state.get_task(task.id)
        assert recovered_task is not None
        assert replayed.id == snapshot.id
        assert recovered_task.memory_snapshot_id == snapshot.id
        unit_of_work.commit()
    engine.dispose()
