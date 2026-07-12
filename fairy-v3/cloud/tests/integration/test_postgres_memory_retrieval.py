from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from fairy_core.application.core import CoreApplication
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import build_default_registry
from fairy_core.contracts.models import (
    ExecutionTarget,
    MemoryClaimPromoteInput,
    MemoryObserveInput,
    TaskCreate,
)
from fairy_core.domain.models import (
    OperationMode,
    ProjectResidency,
    WorkspaceType,
)
from fairy_core.memory.application import MemoryApplication
from fairy_core.memory.policy import MemoryPolicy
from fairy_core.memory.retrieval_models import MemorySnapshotStatus, ProjectionState
from fairy_core.persistence import SqlAlchemyUnitOfWorkFactory
from fairy_core.workspace.filesystem import FileSystemWorkspaceProvisioner
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.integration


def test_postgres_fts_gin_and_task_snapshot_retrieval(
    tmp_path: Path,
    postgres_test_context,
) -> None:
    tenant_id = postgres_test_context.track_tenant(f"memory-retrieval-{uuid4().hex}")
    other_tenant_id = postgres_test_context.track_tenant(f"memory-retrieval-other-{uuid4().hex}")
    engine = create_engine(postgres_test_context.core_sync_dsn, pool_pre_ping=True)
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id=tenant_id)
    registry = build_default_registry()
    policy = PolicyEngine(registry)
    core = CoreApplication(
        unit_of_work_factory=factory,
        workspace_provisioner=FileSystemWorkspaceProvisioner(tmp_path / tenant_id),
        registry=registry,
        policy=policy,
    )
    memory = MemoryApplication(
        unit_of_work_factory=factory,
        registry=registry,
        command_policy=policy,
        memory_policy=MemoryPolicy(),
        scope_resolver=core.scope_for_task,
    )
    try:
        project = core.create_project(
            name="PostgreSQL retrieval",
            residency=ProjectResidency.SYNCED,
        )
        conversation = core.create_conversation(
            project_id=project.project.id,
            workspace_type=WorkspaceType.PROJECT_CHAT,
        )
        source_task = core.create_task(
            TaskCreate(
                conversation_id=conversation.id,
                user_request="Remember the accessibility framework",
                operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
                execution_target=ExecutionTarget.CLOUD,
                idempotency_key="postgres:retrieval:source-task",
            )
        )
        observation = memory.observe(
            MemoryObserveInput(
                task_id=source_task.task.id,
                content="React Aria is the accessibility framework.",
                idempotency_key="postgres:retrieval:observe",
            )
        )
        claim = memory.promote_claim(
            MemoryClaimPromoteInput(
                task_id=source_task.task.id,
                observation_id=observation.id,
                subject="project",
                predicate="accessibility_framework",
                value="React Aria",
                normalized_text="react aria accessibility framework",
                user_confirmed=True,
                idempotency_key="postgres:retrieval:promote",
            )
        )
        retrieval_task = core.create_task(
            TaskCreate(
                conversation_id=conversation.id,
                user_request="react aria",
                operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
                execution_target=ExecutionTarget.CLOUD,
                idempotency_key="postgres:retrieval:task",
            )
        )

        with factory() as unit_of_work:
            hits = unit_of_work.memory_search.search(
                scope=retrieval_task.scope,
                query="react aria",
                generation=1,
                limit=10,
            )
            snapshot = unit_of_work.snapshots.get_for_task(retrieval_task.task.id)
            assert snapshot is not None
            health = unit_of_work.memory_search.health(
                generation=1,
                source_watermark_cursor=snapshot.source_watermark_cursor,
            )

        assert claim.claim.id in {hit.document.source_id for hit in hits}
        assert snapshot.status is MemorySnapshotStatus.READY
        assert snapshot.projection_state is ProjectionState.READY
        assert [item.ordinal for item in snapshot.items] == list(range(len(snapshot.items)))
        assert claim.claim.id in {item.source_id for item in snapshot.items}
        assert health.state is ProjectionState.READY

        with engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_id},
            )
            vector = connection.execute(
                text(
                    "SELECT search_vector::text FROM memory_search_documents "
                    "WHERE tenant_id = :tenant_id AND source_id = :source_id"
                ),
                {"tenant_id": tenant_id, "source_id": str(claim.claim.id)},
            ).scalar_one()
            index_definition = connection.execute(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE schemaname = 'public' "
                    "AND indexname = 'ix_memory_search_documents_vector'"
                )
            ).scalar_one()
        assert "'react'" in vector and "'aria'" in vector
        assert "USING gin (search_vector)" in index_definition

        with SqlAlchemyUnitOfWorkFactory(
            engine,
            tenant_id=other_tenant_id,
        )() as unit_of_work:
            assert unit_of_work.state.get_task(retrieval_task.task.id) is None
            assert unit_of_work.snapshots.get_for_task(retrieval_task.task.id) is None
    finally:
        engine.dispose()
