from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fairy_core.commanding.registry import RiskLevel
from fairy_core.domain.models import (
    Conversation,
    OperationMode,
    Project,
    ProjectResidency,
    ScopeContract,
    Task,
    Version,
    VersionVisibility,
    WorkspaceType,
)
from fairy_core.memory.models import (
    MemoryAuthority,
    MemoryClaim,
    MemoryClaimRevision,
    MemoryNamespace,
    MemoryObservation,
    MemorySensitivity,
)
from fairy_core.persistence import SqlAlchemyUnitOfWorkFactory
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine

ADMIN_DSN = os.environ.get("FAIRY_TEST_POSTGRES_DSN")
CORE_DSN = os.environ.get("FAIRY_TEST_CORE_POSTGRES_DSN")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not all((ADMIN_DSN, CORE_DSN)),
        reason=("FAIRY_TEST_POSTGRES_DSN and FAIRY_TEST_CORE_POSTGRES_DSN are required"),
    ),
]


def _fingerprint(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


@pytest.mark.asyncio
async def test_postgres_memory_repository_uses_rls_and_shared_uow(tmp_path: Path) -> None:
    assert ADMIN_DSN is not None
    assert CORE_DSN is not None
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    old_dsn = os.environ.get("FAIRY_POSTGRES_DSN")
    os.environ["FAIRY_POSTGRES_DSN"] = ADMIN_DSN
    try:
        await asyncio.to_thread(command.upgrade, config, "head")
    finally:
        if old_dsn is None:
            os.environ.pop("FAIRY_POSTGRES_DSN", None)
        else:
            os.environ["FAIRY_POSTGRES_DSN"] = old_dsn

    tenant_id = f"memory-{uuid4().hex}"
    other_tenant_id = f"memory-other-{uuid4().hex}"
    core_engine = create_engine(CORE_DSN, pool_pre_ping=True)
    admin_engine = create_async_engine(ADMIN_DSN, pool_pre_ping=True)
    factory = SqlAlchemyUnitOfWorkFactory(core_engine, tenant_id=tenant_id)
    project = Project.create(name="PostgreSQL Memory", residency=ProjectResidency.SYNCED)
    base = Version.create(
        project_id=project.id,
        source_conversation_id=None,
        source_task_id=None,
        parent_version_id=None,
        project_root=tmp_path / "base",
        visibility=VersionVisibility.PROJECT_ACTIVE,
    )
    project.accept_version(base.id, expected_revision=0)
    conversation = Conversation.create(
        project_id=project.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
        base_version_id=base.id,
    )
    task = Task.create(
        project_id=project.id,
        conversation_id=conversation.id,
        user_request="Remember the accessibility framework",
        operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
        base_version_id=base.id,
        execution_target="cloud",
    )
    draft = Version.create(
        project_id=project.id,
        source_conversation_id=conversation.id,
        source_task_id=task.id,
        parent_version_id=base.id,
        project_root=tmp_path / "draft",
        visibility=VersionVisibility.CHAT_DRAFT,
    )
    task.bind_target_version(draft.id)
    scope = ScopeContract.create(
        workspace_type=WorkspaceType.PROJECT_CHAT,
        project_id=project.id,
        conversation_id=conversation.id,
        task_id=task.id,
        operation_mode=task.operation_mode,
        base_version_id=base.id,
        target_version_id=draft.id,
        project_root=draft.project_root,
        allowed_write_paths=(draft.project_root,),
        forbidden_write_paths=(),
        execution_target="cloud",
        network_policy="off",
        memory_read_scope=("project_canonical",),
        memory_write_scope=("project_canonical",),
    )
    try:
        with factory() as unit_of_work:
            unit_of_work.state.save_project(project)
            unit_of_work.state.save_conversation(conversation)
            unit_of_work.state.save_version(base)
            unit_of_work.state.save_task(task, idempotency_key="task:postgres-memory")
            unit_of_work.state.save_version(draft)
            run = unit_of_work.commands.create_run(
                command_name="memory.observe",
                actor="user:integration",
                scope=scope,
                input_payload={"content": "React Aria"},
                risk_level=RiskLevel.LOW,
                idempotency_key="command:postgres-memory",
            )
            event = next(
                item
                for item in unit_of_work.commands.events_after(cursor=0)
                if item.run_id == run.id
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
                actor="user:integration",
            )
            unit_of_work.memory.append_observation(
                observation,
                request_fingerprint=_fingerprint("observe:postgres"),
            )
            claim = MemoryClaim.create(
                namespace=MemoryNamespace.PROJECT_CANONICAL,
                project_id=project.id,
                subject="project",
                predicate="accessibility_framework",
            )
            unit_of_work.memory.create_claim(
                claim,
                request_fingerprint=_fingerprint("claim:postgres"),
            )
            revision = MemoryClaimRevision.create(
                claim_id=claim.id,
                revision=1,
                value="React Aria",
                normalized_text="react aria",
                source_observation_ids=(observation.id,),
                source_event_ids=(event.id,),
                authority=MemoryAuthority.EXPLICIT_USER,
                confidence=1.0,
                actor="user:integration",
            )
            unit_of_work.memory.append_revision(
                claim.id,
                expected_revision=0,
                revision=revision,
                request_fingerprint=_fingerprint("revision:postgres"),
            )
            unit_of_work.commit()

        with factory() as unit_of_work:
            recovered = unit_of_work.memory.get_claim(claim.id)
            assert recovered is not None
            assert recovered.current_revision == 1
            assert unit_of_work.memory.revisions_for_claim(claim.id) == [revision]

        with SqlAlchemyUnitOfWorkFactory(
            core_engine,
            tenant_id=other_tenant_id,
        )() as unit_of_work:
            assert unit_of_work.memory.get_claim(claim.id) is None
    finally:
        core_engine.dispose()
        await _delete_tenants(admin_engine, (tenant_id, other_tenant_id))
        await admin_engine.dispose()


async def _delete_tenants(admin_engine, tenant_ids: tuple[str, ...]) -> None:
    tables = (
        "memory_tombstones",
        "memory_claim_revisions",
        "memory_claims",
        "memory_observations",
        "outbox",
        "core_checkpoints",
        "core_approvals",
        "core_changesets",
        "domain_events",
        "task_event_sequences",
        "command_runs",
        "core_tasks",
        "core_versions",
        "core_conversations",
        "version_candidates",
        "worker_leases",
        "core_projects",
        "core_tenants",
    )
    async with admin_engine.begin() as connection:
        for table_name in tables:
            await connection.execute(
                text(f"DELETE FROM {table_name} WHERE tenant_id IN (:tenant_a, :tenant_b)"),
                {"tenant_a": tenant_ids[0], "tenant_b": tenant_ids[1]},
            )
