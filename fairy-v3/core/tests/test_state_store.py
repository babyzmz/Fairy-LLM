from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, create_mock_engine
from sqlalchemy.pool import StaticPool

from fairy_core.domain.errors import (
    IdempotencyConflictError,
    InvalidTransitionError,
    VersionConflictError,
)
from fairy_core.domain.execution import (
    Approval,
    ApprovalDecision,
    Artifact,
    ArtifactType,
    ArtifactVisibility,
    Changeset,
    ChangesetStatus,
    Checkpoint,
    PreviewSession,
    PreviewVisibility,
    RuntimeKind,
    RuntimeSession,
)
from fairy_core.domain.ids import new_id
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
from fairy_core.storage import SqlAlchemyStateStore, SqliteStateStore


def test_sqlite_state_store_migrates_the_pre_tenant_v3_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "state.db"
    project = Project.create(name="Pre-tenant V3", residency=ProjectResidency.LOCAL_ONLY)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                residency TEXT NOT NULL,
                active_version_id TEXT,
                active_preview_id TEXT,
                revision INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO projects (
                id, name, residency, active_version_id, active_preview_id,
                revision, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(project.id),
                project.name,
                project.residency.value,
                None,
                None,
                project.revision,
                project.created_at.isoformat(),
                project.updated_at.isoformat(),
            ),
        )

    store = SqliteStateStore(database_path)
    with sqlite3.connect(database_path) as connection:
        applied_at = connection.execute("SELECT applied_at FROM core_local_migrations").fetchone()[
            0
        ]

    assert store.get_project(project.id) == project
    assert datetime.fromisoformat(applied_at).utcoffset() == timedelta(0)


def test_sqlite_state_store_preserves_non_utc_datetime_instants(tmp_path: Path) -> None:
    store = SqliteStateStore(tmp_path / "state.db")
    offset_time = datetime(2026, 1, 2, 10, tzinfo=timezone(timedelta(hours=10)))
    project = Project.create(name="Timezone", residency=ProjectResidency.LOCAL_ONLY)
    project = replace(project, created_at=offset_time, updated_at=offset_time)

    store.save_project(project)
    recovered = store.get_project(project.id)

    assert recovered is not None
    assert recovered.created_at == datetime(2026, 1, 2, 0, tzinfo=UTC)
    assert recovered.updated_at == datetime(2026, 1, 2, 0, tzinfo=UTC)


def test_postgres_schema_initialization_requires_alembic() -> None:
    engine = create_mock_engine("postgresql+psycopg://", lambda *_args, **_kwargs: None)

    with pytest.raises(ValueError, match="Alembic"):
        SqlAlchemyStateStore(engine, tenant_id="tenant-a", initialize_schema=True)


def test_sqlalchemy_state_store_isolates_tenants_and_idempotency_keys(
    tmp_path: Path,
) -> None:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tenant_a = SqlAlchemyStateStore(
        engine,
        tenant_id="tenant-a",
        initialize_schema=True,
    )
    tenant_b = SqlAlchemyStateStore(engine, tenant_id="tenant-b")
    project_a = Project.create(name="Project A", residency=ProjectResidency.SYNCED)
    project_b = replace(project_a, name="Project B")
    conversation_a = Conversation.create(
        project_id=project_a.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
        base_version_id=None,
    )
    conversation_b = Conversation.create(
        project_id=project_b.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
        base_version_id=None,
    )
    task_a = Task.create(
        project_id=project_a.id,
        conversation_id=conversation_a.id,
        user_request="Tenant A",
        operation_mode=OperationMode.CREATE_NEW_VERSION,
        base_version_id=None,
        execution_target="cloud",
    )
    task_b = Task.create(
        project_id=project_b.id,
        conversation_id=conversation_b.id,
        user_request="Tenant B",
        operation_mode=OperationMode.CREATE_NEW_VERSION,
        base_version_id=None,
        execution_target="cloud",
    )

    tenant_a.save_project(project_a)
    tenant_a.save_conversation(conversation_a)
    tenant_a.save_task(task_a, idempotency_key="device:request-1")

    assert tenant_b.get_project(project_a.id) is None
    assert tenant_b.find_task_by_idempotency_key("device:request-1") is None

    tenant_b.save_project(project_b)
    tenant_b.save_conversation(conversation_b)
    tenant_b.save_task(task_b, idempotency_key="device:request-1")

    assert tenant_a.get_project(project_a.id).name == "Project A"
    assert tenant_b.get_project(project_b.id).name == "Project B"
    assert tenant_a.find_task_by_idempotency_key("device:request-1") == task_a
    assert tenant_b.find_task_by_idempotency_key("device:request-1") == task_b


def test_state_store_recovers_project_graph_after_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "state.db"
    store = SqliteStateStore(database_path)
    project = Project.create(name="Example", residency=ProjectResidency.LOCAL_ONLY)
    version = Version.create(
        project_id=project.id,
        source_conversation_id=None,
        source_task_id=None,
        parent_version_id=None,
        project_root=tmp_path / "versions" / "base",
        visibility=VersionVisibility.PROJECT_ACTIVE,
    )
    project.accept_version(version.id, expected_revision=0)
    conversation = Conversation.create(
        project_id=project.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
        base_version_id=version.id,
    )
    task = Task.create(
        project_id=project.id,
        conversation_id=conversation.id,
        user_request="Add pricing",
        operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
        base_version_id=version.id,
        execution_target="local",
    )
    task.bind_memory_snapshot(new_id(), "a" * 64)
    store.save_project(project)
    store.save_version(version)
    store.save_conversation(conversation)
    store.save_task(task, idempotency_key="device:request")
    store.close()

    restarted = SqliteStateStore(database_path)

    assert restarted.get_project(project.id) == project
    assert restarted.get_version(version.id) == version
    assert restarted.get_conversation(conversation.id) == conversation
    assert restarted.get_task(task.id) == task
    assert restarted.find_task_by_idempotency_key("device:request") == task


def test_local_core_engine_adds_snapshot_columns_to_existing_tasks(tmp_path: Path) -> None:
    from fairy_core.persistence.sqlite import create_sqlite_core_engine

    database_path = tmp_path / "existing-core.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE core_tasks (
                tenant_id TEXT NOT NULL,
                id TEXT NOT NULL,
                project_id TEXT,
                conversation_id TEXT NOT NULL,
                user_request TEXT NOT NULL,
                operation_mode TEXT NOT NULL,
                base_version_id TEXT,
                target_version_id TEXT,
                execution_target TEXT NOT NULL,
                status TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, id),
                UNIQUE (tenant_id, idempotency_key)
            )
            """
        )

    engine = create_sqlite_core_engine(database_path)
    try:
        with sqlite3.connect(database_path) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(core_tasks)")}
    finally:
        engine.dispose()

    assert {"memory_snapshot_id", "memory_snapshot_hash"} <= columns


def test_local_core_engine_migrates_early_mcp_request_results(tmp_path: Path) -> None:
    from fairy_core.persistence.sqlite import create_sqlite_core_engine

    database_path = tmp_path / "early-mcp.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE core_mcp_server_updates (
                tenant_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_fingerprint TEXT NOT NULL,
                server_id TEXT NOT NULL,
                result_record JSON NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, idempotency_key)
            )
            """
        )

    engine = create_sqlite_core_engine(database_path)
    try:
        with sqlite3.connect(database_path) as connection:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(core_mcp_server_updates)")
            }
            foreign_keys = list(
                connection.execute("PRAGMA foreign_key_list(core_mcp_server_updates)")
            )
    finally:
        engine.dispose()

    assert {"result_record", "result_deleted", "result_error_code"} <= columns
    assert foreign_keys == []


def test_accept_version_is_atomic_compare_and_swap(tmp_path: Path) -> None:
    store = SqliteStateStore(tmp_path / "state.db")
    project = Project.create(name="Synced", residency=ProjectResidency.SYNCED)
    store.save_project(project)
    first_version = Version.create(
        project_id=project.id,
        source_conversation_id=None,
        source_task_id=None,
        parent_version_id=None,
        project_root=tmp_path / "first",
        visibility=VersionVisibility.PROJECT_CANDIDATE,
    )
    second_version = Version.create(
        project_id=project.id,
        source_conversation_id=None,
        source_task_id=None,
        parent_version_id=None,
        project_root=tmp_path / "second",
        visibility=VersionVisibility.PROJECT_CANDIDATE,
    )
    store.save_version(first_version)
    store.save_version(second_version)

    accepted = store.accept_version(
        project_id=project.id,
        version_id=first_version.id,
        expected_revision=0,
    )
    with pytest.raises(VersionConflictError):
        store.accept_version(
            project_id=project.id,
            version_id=second_version.id,
            expected_revision=0,
        )

    assert accepted.active_version_id == first_version.id
    assert store.get_project(project.id).active_version_id == first_version.id
    assert store.get_project(project.id).revision == 1


def test_new_project_conversation_never_inherits_another_draft(tmp_path: Path) -> None:
    store = SqliteStateStore(tmp_path / "state.db")
    project = Project.create(name="Example", residency=ProjectResidency.LOCAL_ONLY)
    base = Version.create(
        project_id=project.id,
        source_conversation_id=None,
        source_task_id=None,
        parent_version_id=None,
        project_root=tmp_path / "base",
        visibility=VersionVisibility.PROJECT_ACTIVE,
    )
    project.accept_version(base.id, expected_revision=0)
    first = Conversation.create(
        project_id=project.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
        base_version_id=base.id,
    )
    first.active_draft_version_id = base.id
    second = Conversation.create(
        project_id=project.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
        base_version_id=project.active_version_id,
    )
    store.save_project(project)
    store.save_version(base)
    store.save_conversation(first)
    store.save_conversation(second)

    recovered = store.get_conversation(second.id)

    assert recovered.base_version_id == base.id
    assert recovered.active_draft_version_id is None
    assert recovered.active_preview_id is None


def test_execution_records_survive_restart_with_explicit_ownership(tmp_path: Path) -> None:
    database_path = tmp_path / "state.db"
    store = SqliteStateStore(database_path)
    project = Project.create(name="State", residency=ProjectResidency.LOCAL_ONLY)
    conversation = Conversation.create(
        project_id=project.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
        base_version_id=None,
    )
    task = Task.create(
        project_id=project.id,
        conversation_id=conversation.id,
        user_request="Update readme",
        operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
        base_version_id=None,
        execution_target="local",
    )
    version = Version.create(
        version_id=new_id(),
        project_id=project.id,
        source_conversation_id=conversation.id,
        source_task_id=task.id,
        parent_version_id=None,
        project_root=tmp_path / "version",
        visibility=VersionVisibility.CHAT_DRAFT,
    )
    task.bind_target_version(version.id)
    changeset = Changeset.create(
        project_id=project.id,
        conversation_id=conversation.id,
        task_id=task.id,
        version_id=version.id,
        files=("README.md",),
        patches=("updated",),
        reason="Update readme",
        risk_level="medium",
        idempotency_key="changeset:state",
    )
    changeset.transition_to(ChangesetStatus.AWAITING_APPROVAL)
    approval = Approval.create(
        task_id=task.id,
        command_run_id=new_id(),
        changeset_id=changeset.id,
        requested_by="agent",
        reason="Write README.md",
    )
    approval.decide(decision=ApprovalDecision.APPROVED, decided_by="user")
    checkpoint = Checkpoint.create(
        task_id=task.id,
        version_id=version.id,
        changed_files=("README.md",),
        command_run_ids=(approval.command_run_id,),
        preview_artifact_id=None,
        evidence_artifact_ids=(),
    )

    store.save_project(project)
    store.save_conversation(conversation)
    store.save_version(version)
    store.save_task(task, idempotency_key="task:state")
    store.save_changeset(changeset)
    store.save_approval(approval)
    store.save_checkpoint(checkpoint)
    store.close()
    restarted = SqliteStateStore(database_path)

    assert restarted.get_changeset(changeset.id) == changeset
    assert restarted.find_changeset_by_idempotency_key("changeset:state") == changeset
    assert restarted.get_approval(approval.id) == approval
    assert restarted.get_checkpoint(checkpoint.id) == checkpoint


def test_runtime_preview_and_artifact_round_trip_after_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "state.db"
    store = SqliteStateStore(database_path)
    scope = _save_runtime_graph(store, tmp_path)
    runtime = RuntimeSession.create(
        scope=scope,
        kind=RuntimeKind.STATIC_SITE,
        executor="rust_local_worker",
        idempotency_key="runtime:round-trip",
    )
    assert store.append_runtime(runtime) == runtime
    runtime.begin_start()
    assert store.save_runtime(runtime, expected_revision=0) == runtime
    runtime.mark_running(executor_handle="static:round-trip", port=43125)
    assert store.save_runtime(runtime, expected_revision=1) == runtime

    preview = PreviewSession.create(
        scope=scope,
        runtime_id=runtime.id,
        visibility=PreviewVisibility.CHAT_DRAFT,
        idempotency_key="preview:round-trip",
    )
    store.append_preview(preview)
    preview.begin_start()
    store.save_preview(preview, expected_revision=0)
    preview.mark_ready("http://127.0.0.1:43125/round-trip/")
    store.save_preview(preview, expected_revision=1)
    artifact = Artifact.create(
        project_id=scope.project_id,
        conversation_id=scope.conversation_id,
        task_id=scope.task_id,
        version_id=scope.target_version_id,
        artifact_type=ArtifactType.PREVIEW_MANIFEST,
        visibility=ArtifactVisibility.CONVERSATION,
        storage_location="previews/round-trip.json",
        media_type="application/json",
        byte_length=73,
        content_hash="a" * 64,
        metadata={"entry": "index.html", "assets": ["app.js"]},
    )
    assert store.append_artifact(artifact) == artifact
    store.close()

    restarted = SqliteStateStore(database_path)

    assert restarted.get_runtime(runtime.id) == runtime
    assert restarted.find_runtime_by_idempotency_key("runtime:round-trip") == runtime
    assert restarted.runtimes_for_task(scope.task_id) == [runtime]
    assert restarted.get_preview(preview.id) == preview
    assert restarted.find_preview_by_idempotency_key("preview:round-trip") == preview
    assert restarted.preview_for_task(scope.task_id) == preview
    assert restarted.previews_for_conversation(scope.conversation_id) == [preview]
    assert restarted.get_artifact(artifact.id) == artifact
    assert restarted.artifacts_for_task(scope.task_id) == [artifact]
    assert restarted.get_artifact(artifact.id).metadata["assets"] == ("app.js",)


def test_runtime_and_preview_updates_use_revision_compare_and_swap(tmp_path: Path) -> None:
    store = SqliteStateStore(tmp_path / "state.db")
    scope = _save_runtime_graph(store, tmp_path)
    runtime = RuntimeSession.create(
        scope=scope,
        kind=RuntimeKind.STATIC_SITE,
        executor="rust_local_worker",
        idempotency_key="runtime:cas",
    )
    store.append_runtime(runtime)
    stale_runtime = replace(runtime)
    runtime.begin_start()
    stale_runtime.begin_start()
    store.save_runtime(runtime, expected_revision=0)
    with pytest.raises(VersionConflictError):
        store.save_runtime(stale_runtime, expected_revision=0)

    preview = PreviewSession.create(
        scope=scope,
        runtime_id=runtime.id,
        visibility=PreviewVisibility.CHAT_DRAFT,
        idempotency_key="preview:cas",
    )
    store.append_preview(preview)
    stale_preview = replace(preview)
    preview.begin_start()
    stale_preview.begin_start()
    store.save_preview(preview, expected_revision=0)
    with pytest.raises(VersionConflictError):
        store.save_preview(stale_preview, expected_revision=0)


def test_runtime_preview_idempotency_and_active_preview_are_enforced(tmp_path: Path) -> None:
    store = SqliteStateStore(tmp_path / "state.db")
    scope = _save_runtime_graph(store, tmp_path)
    runtime = RuntimeSession.create(
        scope=scope,
        kind=RuntimeKind.STATIC_SITE,
        executor="rust_local_worker",
        idempotency_key="runtime:idempotent",
    )
    assert store.append_runtime(runtime) == runtime
    runtime_replay = RuntimeSession.create(
        scope=scope,
        kind=RuntimeKind.STATIC_SITE,
        executor="rust_local_worker",
        idempotency_key="runtime:idempotent",
    )
    assert store.append_runtime(runtime_replay) == runtime
    runtime_conflict = RuntimeSession.create(
        scope=scope,
        kind=RuntimeKind.WSL_PROJECT,
        executor="wsl_worker",
        idempotency_key="runtime:idempotent",
    )
    with pytest.raises(IdempotencyConflictError):
        store.append_runtime(runtime_conflict)

    preview = PreviewSession.create(
        scope=scope,
        runtime_id=runtime.id,
        visibility=PreviewVisibility.CHAT_DRAFT,
        idempotency_key="preview:idempotent",
    )
    assert store.append_preview(preview) == preview
    preview_replay = PreviewSession.create(
        scope=scope,
        runtime_id=runtime.id,
        visibility=PreviewVisibility.CHAT_DRAFT,
        idempotency_key="preview:idempotent",
    )
    assert store.append_preview(preview_replay) == preview

    second_preview = PreviewSession.create(
        scope=scope,
        runtime_id=runtime.id,
        visibility=PreviewVisibility.PRIVATE,
        idempotency_key="preview:second-active",
    )
    with pytest.raises(InvalidTransitionError):
        store.append_preview(second_preview)

    preview.begin_start()
    store.save_preview(preview, expected_revision=0)
    preview.mark_failed("PREVIEW_FAILED")
    store.save_preview(preview, expected_revision=1)
    assert store.append_preview(second_preview) == second_preview
    assert store.preview_for_task(scope.task_id) == second_preview
    assert store.preview_for_task(scope.task_id, include_terminal=True) == second_preview


def test_artifact_insert_is_immutable_and_idempotent(tmp_path: Path) -> None:
    store = SqliteStateStore(tmp_path / "state.db")
    scope = _save_runtime_graph(store, tmp_path)
    artifact = Artifact.create(
        project_id=scope.project_id,
        conversation_id=scope.conversation_id,
        task_id=scope.task_id,
        version_id=scope.target_version_id,
        artifact_type=ArtifactType.LOG,
        visibility=ArtifactVisibility.PRIVATE,
        storage_location="logs/runtime.txt",
        media_type="text/plain",
        byte_length=12,
        content_hash="b" * 64,
        metadata={},
    )

    assert store.append_artifact(artifact) == artifact
    assert store.append_artifact(artifact) == artifact
    with pytest.raises(IdempotencyConflictError):
        store.append_artifact(replace(artifact, content_hash="c" * 64))


def _save_runtime_graph(store: SqliteStateStore, tmp_path: Path) -> ScopeContract:
    project = Project.create(name="Runtime", residency=ProjectResidency.LOCAL_ONLY)
    conversation = Conversation.create(
        project_id=project.id,
        workspace_type=WorkspaceType.PROJECT_CHAT,
        base_version_id=None,
    )
    task = Task.create(
        project_id=project.id,
        conversation_id=conversation.id,
        user_request="Preview the project",
        operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
        base_version_id=None,
        execution_target="local",
    )
    version = Version.create(
        project_id=project.id,
        source_conversation_id=conversation.id,
        source_task_id=task.id,
        parent_version_id=None,
        project_root=tmp_path / "versions" / str(task.id),
        visibility=VersionVisibility.CHAT_DRAFT,
    )
    task.bind_target_version(version.id)
    store.save_project(project)
    store.save_conversation(conversation)
    store.save_task(task, idempotency_key=f"task:{task.id}")
    store.save_version(version)
    return ScopeContract.create(
        workspace_type=WorkspaceType.PROJECT_CHAT,
        project_id=project.id,
        conversation_id=conversation.id,
        task_id=task.id,
        operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
        base_version_id=version.id,
        target_version_id=version.id,
        project_root=version.project_root,
        allowed_write_paths=(version.project_root,),
        forbidden_write_paths=(),
        execution_target="local",
        network_policy="off",
        memory_read_scope=("project_canonical",),
        memory_write_scope=("conversation_draft",),
    )
