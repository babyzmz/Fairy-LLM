from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from fairy_core.commanding.registry import RiskLevel
from fairy_core.commanding.schema import command_metadata
from fairy_core.memory.models import (
    MemoryAuthority,
    MemoryNamespace,
    MemoryObservation,
    MemorySensitivity,
)
from fairy_core.memory.schema import memory_metadata
from fairy_core.persistence import SqlAlchemyUnitOfWorkFactory
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.storage.schema import state_metadata
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


def test_memory_uses_same_transaction_as_state_and_command_ledger(
    engine: Engine,
    tmp_path: Path,
) -> None:
    project, base, conversation, task, draft, scope = build_memory_domain_context(
        tmp_path, "rollback"
    )
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="tenant-a")
    observation_id = None

    with pytest.raises(RuntimeError, match="crash"), factory() as unit_of_work:
        unit_of_work.state.save_project(project)
        unit_of_work.state.save_conversation(conversation)
        unit_of_work.state.save_version(base)
        unit_of_work.state.save_task(task, idempotency_key="task:rollback")
        unit_of_work.state.save_version(draft)
        run = unit_of_work.commands.create_run(
            command_name="memory.observe",
            actor="user:test",
            scope=scope,
            input_payload={"content": "React Aria"},
            risk_level=RiskLevel.LOW,
            idempotency_key="command:rollback",
        )
        event = next(
            event
            for event in unit_of_work.commands.events_after(cursor=0)
            if event.run_id == run.id
        )
        observation = MemoryObservation.create(
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
        observation_id = observation.id
        unit_of_work.memory.append_observation(
            observation,
            request_fingerprint=hashlib.sha256(b"rollback").hexdigest(),
        )
        raise RuntimeError("crash")

    assert observation_id is not None
    with factory() as unit_of_work:
        assert unit_of_work.state.get_project(project.id) is None
        assert unit_of_work.commands.events_after(cursor=0) == []
        assert unit_of_work.memory.get_observation(observation_id) is None


def test_local_core_engine_initializes_all_memory_tables(tmp_path: Path) -> None:
    engine = create_sqlite_core_engine(tmp_path / "core.db")
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    assert {
        "memory_observations",
        "memory_claims",
        "memory_claim_revisions",
        "memory_tombstones",
        "memory_snapshots",
        "memory_snapshot_items",
        "memory_search_documents",
        "memory_access_log",
        "memory_projection_checkpoints",
    } <= tables
