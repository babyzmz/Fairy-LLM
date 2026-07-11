from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from fairy_core.domain.execution import Approval
from fairy_core.domain.models import (
    Conversation,
    OperationMode,
    Project,
    ProjectResidency,
    Task,
    Version,
    VersionVisibility,
    WorkspaceType,
)
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.storage.sqlalchemy import SqlAlchemyStateStore

_CREATED_AT = datetime(2026, 7, 11, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class _Graph:
    project: Project
    conversation: Conversation
    task: Task
    version: Version
    approval: Approval


def _uuid(value: int) -> UUID:
    return UUID(int=value)


def _seed_graph(
    store: SqlAlchemyStateStore,
    root: Path,
    *,
    identity_offset: int,
    label: str,
) -> _Graph:
    project = replace(
        Project.create(name=label, residency=ProjectResidency.SYNCED),
        id=_uuid(identity_offset + 1),
        created_at=_CREATED_AT,
        updated_at=_CREATED_AT,
    )
    conversation = replace(
        Conversation.create(
            project_id=project.id,
            workspace_type=WorkspaceType.PROJECT_CHAT,
            base_version_id=None,
        ),
        id=_uuid(identity_offset + 2),
        created_at=_CREATED_AT,
        updated_at=_CREATED_AT,
    )
    task = replace(
        Task.create(
            project_id=project.id,
            conversation_id=conversation.id,
            user_request=label,
            operation_mode=OperationMode.CREATE_NEW_VERSION,
            base_version_id=None,
            execution_target="cloud",
        ),
        id=_uuid(identity_offset + 3),
        created_at=_CREATED_AT,
        updated_at=_CREATED_AT,
    )
    version = replace(
        Version.create(
            project_id=project.id,
            source_conversation_id=conversation.id,
            source_task_id=task.id,
            parent_version_id=None,
            project_root=root / label,
            visibility=VersionVisibility.CHAT_DRAFT,
        ),
        id=_uuid(identity_offset + 4),
        created_at=_CREATED_AT,
    )
    approval = replace(
        Approval.create(
            task_id=task.id,
            command_run_id=_uuid(identity_offset + 6),
            requested_by="agent",
            reason=label,
        ),
        id=_uuid(identity_offset + 5),
        created_at=_CREATED_AT,
    )
    store.save_project(project)
    store.save_conversation(conversation)
    store.save_task(task, idempotency_key=f"{label}:task")
    store.save_version(version)
    store.save_approval(approval)
    return _Graph(project, conversation, task, version, approval)


def test_collection_queries_are_tenant_scoped_filtered_and_stably_paged(
    tmp_path: Path,
) -> None:
    engine = create_sqlite_core_engine(tmp_path / "collections.db")
    tenant_a = SqlAlchemyStateStore(engine, tenant_id="tenant-a")
    tenant_b = SqlAlchemyStateStore(engine, tenant_id="tenant-b")
    graph_a1 = _seed_graph(
        tenant_a,
        tmp_path,
        identity_offset=0,
        label="tenant-a-one",
    )
    graph_a2 = _seed_graph(
        tenant_a,
        tmp_path,
        identity_offset=10,
        label="tenant-a-two",
    )
    graph_a3 = _seed_graph(
        tenant_a,
        tmp_path,
        identity_offset=20,
        label="tenant-a-three",
    )
    graph_b1 = _seed_graph(
        tenant_b,
        tmp_path,
        identity_offset=0,
        label="tenant-b-one",
    )

    first = tenant_a.list_projects(limit=2, cursor=None)
    second = tenant_a.list_projects(limit=2, cursor=first.next_cursor)
    empty = tenant_a.list_conversations(
        project_id=_uuid(999),
        limit=2,
        cursor=None,
    )

    assert [item.id for item in first.items] == [graph_a1.project.id, graph_a2.project.id]
    assert first.next_cursor is not None
    assert [item.id for item in second.items] == [graph_a3.project.id]
    assert second.next_cursor is None
    assert [item.name for item in tenant_b.list_projects(limit=100, cursor=None).items] == [
        graph_b1.project.name
    ]
    assert empty.items == ()
    assert empty.next_cursor is None
    assert tenant_a.list_conversations(
        project_id=graph_a1.project.id,
        limit=100,
        cursor=None,
    ).items == (graph_a1.conversation,)
    assert tenant_a.list_tasks(
        project_id=graph_a1.project.id,
        conversation_id=graph_a1.conversation.id,
        limit=100,
        cursor=None,
    ).items == (graph_a1.task,)
    assert tenant_a.list_versions(
        project_id=graph_a1.project.id,
        conversation_id=graph_a1.conversation.id,
        task_id=graph_a1.task.id,
        limit=100,
        cursor=None,
    ).items == (graph_a1.version,)
    assert tenant_a.list_approvals(
        project_id=graph_a1.project.id,
        conversation_id=graph_a1.conversation.id,
        task_id=graph_a1.task.id,
        limit=100,
        cursor=None,
    ).items == (graph_a1.approval,)


def test_collection_queries_validate_limits_and_bind_cursor_to_scope(tmp_path: Path) -> None:
    engine = create_sqlite_core_engine(tmp_path / "cursor.db")
    store = SqlAlchemyStateStore(engine, tenant_id="tenant-a")
    graph = _seed_graph(store, tmp_path, identity_offset=0, label="one")
    _seed_graph(store, tmp_path, identity_offset=10, label="two")
    cursor = store.list_projects(limit=1, cursor=None).next_cursor
    assert cursor is not None

    with pytest.raises(ValueError, match="limit"):
        store.list_projects(limit=0, cursor=None)
    with pytest.raises(ValueError, match="limit"):
        store.list_projects(limit=101, cursor=None)
    with pytest.raises(ValueError, match="cursor"):
        store.list_conversations(
            project_id=graph.project.id,
            limit=1,
            cursor=cursor,
        )
    with pytest.raises(ValueError, match="cursor"):
        store.list_projects(limit=1, cursor="not-an-opaque-cursor")
