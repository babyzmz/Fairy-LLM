from __future__ import annotations

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
)

TENANT_ID_LENGTH = 128
ID_LENGTH = 36

state_metadata = MetaData()


def _tenant_id() -> Column[str]:
    return Column("tenant_id", String(TENANT_ID_LENGTH), primary_key=True)


def _id() -> Column[str]:
    return Column("id", String(ID_LENGTH), primary_key=True)


projects = Table(
    "core_projects",
    state_metadata,
    _tenant_id(),
    _id(),
    Column("name", String(255), nullable=False),
    Column("residency", String(32), nullable=False),
    Column("active_version_id", String(ID_LENGTH)),
    Column("active_preview_id", String(ID_LENGTH)),
    Column("revision", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

conversations = Table(
    "core_conversations",
    state_metadata,
    _tenant_id(),
    _id(),
    Column("project_id", String(ID_LENGTH)),
    Column("workspace_type", String(32), nullable=False),
    Column("base_version_id", String(ID_LENGTH)),
    Column("active_draft_version_id", String(ID_LENGTH)),
    Column("active_task_id", String(ID_LENGTH)),
    Column("active_preview_id", String(ID_LENGTH)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

versions = Table(
    "core_versions",
    state_metadata,
    _tenant_id(),
    _id(),
    Column("project_id", String(ID_LENGTH), nullable=False),
    Column("source_conversation_id", String(ID_LENGTH)),
    Column("source_task_id", String(ID_LENGTH)),
    Column("parent_version_id", String(ID_LENGTH)),
    Column("project_root", String(4096), nullable=False),
    Column("visibility", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

tasks = Table(
    "core_tasks",
    state_metadata,
    _tenant_id(),
    _id(),
    Column("project_id", String(ID_LENGTH)),
    Column("conversation_id", String(ID_LENGTH), nullable=False),
    Column("user_request", String, nullable=False),
    Column("operation_mode", String(64), nullable=False),
    Column("base_version_id", String(ID_LENGTH)),
    Column("target_version_id", String(ID_LENGTH)),
    Column("execution_target", String(32), nullable=False),
    Column("status", String(32), nullable=False),
    Column("idempotency_key", String(512), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("tenant_id", "idempotency_key", name="uq_core_tasks_tenant_idempotency"),
)

changesets = Table(
    "core_changesets",
    state_metadata,
    _tenant_id(),
    _id(),
    Column("project_id", String(ID_LENGTH), nullable=False),
    Column("conversation_id", String(ID_LENGTH), nullable=False),
    Column("task_id", String(ID_LENGTH), nullable=False),
    Column("version_id", String(ID_LENGTH), nullable=False),
    Column("files", JSON, nullable=False),
    Column("patches", JSON, nullable=False),
    Column("reason", String, nullable=False),
    Column("risk_level", String(32), nullable=False),
    Column("idempotency_key", String(512), nullable=False),
    Column("status", String(32), nullable=False),
    Column("approval_decision", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "tenant_id",
        "idempotency_key",
        name="uq_core_changesets_tenant_idempotency",
    ),
)

approvals = Table(
    "core_approvals",
    state_metadata,
    _tenant_id(),
    _id(),
    Column("task_id", String(ID_LENGTH), nullable=False),
    Column("command_run_id", String(ID_LENGTH), nullable=False),
    Column("changeset_id", String(ID_LENGTH)),
    Column("requested_by", String(128), nullable=False),
    Column("reason", String, nullable=False),
    Column("decision", String(32), nullable=False),
    Column("decided_by", String(128)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("decided_at", DateTime(timezone=True)),
)

checkpoints = Table(
    "core_checkpoints",
    state_metadata,
    _tenant_id(),
    _id(),
    Column("task_id", String(ID_LENGTH), nullable=False),
    Column("version_id", String(ID_LENGTH), nullable=False),
    Column("changed_files", JSON, nullable=False),
    Column("command_run_ids", JSON, nullable=False),
    Column("preview_artifact_id", String(ID_LENGTH)),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

Index("ix_core_projects_tenant_updated", projects.c.tenant_id, projects.c.updated_at)
Index("ix_core_conversations_tenant_project", conversations.c.tenant_id, conversations.c.project_id)
Index("ix_core_versions_tenant_project", versions.c.tenant_id, versions.c.project_id)
Index("ix_core_tasks_tenant_status", tasks.c.tenant_id, tasks.c.status, tasks.c.created_at)
Index("ix_core_changesets_tenant_task", changesets.c.tenant_id, changesets.c.task_id)
Index("ix_core_approvals_tenant_task", approvals.c.tenant_id, approvals.c.task_id)
Index("ix_core_checkpoints_tenant_task", checkpoints.c.tenant_id, checkpoints.c.task_id)
