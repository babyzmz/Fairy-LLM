from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

from fairy_core.persistence.tenant import TENANT_ID_LENGTH

ID_LENGTH = 36

state_metadata = MetaData()


class UTCDateTime(TypeDecorator[datetime]):
    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect):
        return dialect.type_descriptor(DateTime(timezone=dialect.name != "sqlite"))

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("datetime values must include a timezone")
        normalized = value.astimezone(UTC)
        return normalized.replace(tzinfo=None) if dialect.name == "sqlite" else normalized

    def process_result_value(self, value: datetime | None, _dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


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
    Column("revision", BigInteger, nullable=False),
    Column("created_at", UTCDateTime(), nullable=False),
    Column("updated_at", UTCDateTime(), nullable=False),
    PrimaryKeyConstraint("tenant_id", "id", name="pk_core_projects"),
)

execution_settings = Table(
    "core_execution_settings",
    state_metadata,
    _tenant_id(),
    Column("profile", String(32), nullable=False),
    Column("capability_overrides", JSON, nullable=False),
    Column("revision", BigInteger, nullable=False),
    Column("updated_at", UTCDateTime(), nullable=False),
    PrimaryKeyConstraint("tenant_id", name="pk_core_execution_settings"),
    CheckConstraint(
        "profile IN ('observe', 'standard', 'autonomous')",
        name="ck_core_execution_settings_profile",
    ),
    CheckConstraint("revision >= 1", name="ck_core_execution_settings_revision"),
)

execution_setting_updates = Table(
    "core_execution_setting_updates",
    state_metadata,
    _tenant_id(),
    Column("idempotency_key", String(512), primary_key=True),
    Column("request_fingerprint", String(64), nullable=False),
    Column("profile", String(32), nullable=False),
    Column("capability_overrides", JSON, nullable=False),
    Column("expected_revision", BigInteger, nullable=False),
    Column("result_revision", BigInteger, nullable=False),
    Column("result_updated_at", UTCDateTime(), nullable=False),
    PrimaryKeyConstraint(
        "tenant_id",
        "idempotency_key",
        name="pk_core_execution_setting_updates",
    ),
    CheckConstraint(
        "profile IN ('observe', 'standard', 'autonomous')",
        name="ck_core_execution_setting_updates_profile",
    ),
    CheckConstraint(
        "expected_revision >= 0 AND result_revision = expected_revision + 1",
        name="ck_core_execution_setting_updates_revision",
    ),
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
    Column("created_at", UTCDateTime(), nullable=False),
    Column("updated_at", UTCDateTime(), nullable=False),
    PrimaryKeyConstraint("tenant_id", "id", name="pk_core_conversations"),
    ForeignKeyConstraint(
        ["tenant_id", "project_id"],
        [projects.c.tenant_id, projects.c.id],
        name="fk_core_conversations_project",
        ondelete="CASCADE",
    ),
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
    Column("created_at", UTCDateTime(), nullable=False),
    PrimaryKeyConstraint("tenant_id", "id", name="pk_core_versions"),
    ForeignKeyConstraint(
        ["tenant_id", "project_id"],
        [projects.c.tenant_id, projects.c.id],
        name="fk_core_versions_project",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "source_conversation_id"],
        [conversations.c.tenant_id, conversations.c.id],
        name="fk_core_versions_source_conversation",
    ),
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
    Column("memory_snapshot_id", String(ID_LENGTH)),
    Column("memory_snapshot_hash", String(64)),
    Column("status", String(32), nullable=False),
    Column("idempotency_key", String(512), nullable=False),
    Column("created_at", UTCDateTime(), nullable=False),
    Column("updated_at", UTCDateTime(), nullable=False),
    PrimaryKeyConstraint("tenant_id", "id", name="pk_core_tasks"),
    UniqueConstraint("tenant_id", "idempotency_key", name="uq_core_tasks_tenant_idempotency"),
    CheckConstraint(
        "(memory_snapshot_id IS NULL AND memory_snapshot_hash IS NULL) OR "
        "(memory_snapshot_id IS NOT NULL AND memory_snapshot_hash IS NOT NULL)",
        name="ck_core_tasks_memory_snapshot_binding",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "project_id"],
        [projects.c.tenant_id, projects.c.id],
        name="fk_core_tasks_project",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "conversation_id"],
        [conversations.c.tenant_id, conversations.c.id],
        name="fk_core_tasks_conversation",
        ondelete="CASCADE",
    ),
)

assistant_turns = Table(
    "core_assistant_turns",
    state_metadata,
    _tenant_id(),
    _id(),
    Column("conversation_id", String(ID_LENGTH), nullable=False),
    Column("task_id", String(ID_LENGTH), nullable=False),
    Column("profile_id", String(255), nullable=False),
    Column("scope_digest", String(64), nullable=False),
    Column("memory_snapshot_id", String(ID_LENGTH), nullable=False),
    Column("memory_snapshot_hash", String(64), nullable=False),
    Column("idempotency_key", String(512), nullable=False),
    Column("status", String(32), nullable=False),
    Column("cancellation_revision", BigInteger, nullable=False),
    Column("usage", JSON, nullable=False),
    Column("error_code", String(128)),
    Column("created_at", UTCDateTime(), nullable=False),
    Column("updated_at", UTCDateTime(), nullable=False),
    Column("started_at", UTCDateTime()),
    Column("completed_at", UTCDateTime()),
    PrimaryKeyConstraint("tenant_id", "id", name="pk_core_assistant_turns"),
    UniqueConstraint(
        "tenant_id",
        "idempotency_key",
        name="uq_core_assistant_turns_tenant_idempotency",
    ),
    UniqueConstraint(
        "tenant_id",
        "id",
        "conversation_id",
        "task_id",
        name="uq_core_assistant_turns_scope",
    ),
    UniqueConstraint(
        "tenant_id",
        "id",
        "task_id",
        name="uq_core_assistant_turns_task_scope",
    ),
    CheckConstraint(
        "status IN ('created', 'running', 'waiting_for_tool', 'completed', 'cancelled', 'failed')",
        name="ck_core_assistant_turns_status",
    ),
    CheckConstraint(
        "cancellation_revision >= 0",
        name="ck_core_assistant_turns_cancellation_revision",
    ),
    CheckConstraint(
        "length(scope_digest) = 64 AND scope_digest = lower(scope_digest)",
        name="ck_core_assistant_turns_scope_digest",
    ),
    CheckConstraint(
        "length(memory_snapshot_hash) = 64 AND memory_snapshot_hash = lower(memory_snapshot_hash)",
        name="ck_core_assistant_turns_memory_snapshot_hash",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "conversation_id"],
        [conversations.c.tenant_id, conversations.c.id],
        name="fk_core_assistant_turns_conversation",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "task_id"],
        [tasks.c.tenant_id, tasks.c.id],
        name="fk_core_assistant_turns_task",
    ),
)

assistant_message_sequences = Table(
    "core_assistant_message_sequences",
    state_metadata,
    _tenant_id(),
    Column("conversation_id", String(ID_LENGTH), primary_key=True),
    Column("last_sequence", BigInteger, nullable=False),
    PrimaryKeyConstraint(
        "tenant_id",
        "conversation_id",
        name="pk_core_assistant_message_sequences",
    ),
    CheckConstraint(
        "last_sequence > 0",
        name="ck_core_assistant_message_sequences_positive",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "conversation_id"],
        [conversations.c.tenant_id, conversations.c.id],
        name="fk_core_assistant_message_sequences_conversation",
    ),
)

assistant_messages = Table(
    "core_assistant_messages",
    state_metadata,
    _tenant_id(),
    _id(),
    Column("conversation_id", String(ID_LENGTH), nullable=False),
    Column("task_id", String(ID_LENGTH), nullable=False),
    Column("turn_id", String(ID_LENGTH)),
    Column("sequence", BigInteger, nullable=False),
    Column("role", String(32), nullable=False),
    Column("visibility", String(32), nullable=False),
    Column("content", String, nullable=False),
    Column("created_at", UTCDateTime(), nullable=False),
    PrimaryKeyConstraint("tenant_id", "id", name="pk_core_assistant_messages"),
    UniqueConstraint(
        "tenant_id",
        "conversation_id",
        "sequence",
        name="uq_core_assistant_messages_conversation_sequence",
    ),
    CheckConstraint("sequence > 0", name="ck_core_assistant_messages_sequence"),
    CheckConstraint(
        "role IN ('user', 'assistant', 'tool', 'system_notice')",
        name="ck_core_assistant_messages_role",
    ),
    CheckConstraint(
        "visibility IN ('user', 'developer', 'internal')",
        name="ck_core_assistant_messages_visibility",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "conversation_id"],
        [conversations.c.tenant_id, conversations.c.id],
        name="fk_core_assistant_messages_conversation",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "task_id"],
        [tasks.c.tenant_id, tasks.c.id],
        name="fk_core_assistant_messages_task",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "turn_id", "conversation_id", "task_id"],
        [
            assistant_turns.c.tenant_id,
            assistant_turns.c.id,
            assistant_turns.c.conversation_id,
            assistant_turns.c.task_id,
        ],
        name="fk_core_assistant_messages_turn_scope",
    ),
)

assistant_tool_invocations = Table(
    "core_assistant_tool_invocations",
    state_metadata,
    _tenant_id(),
    _id(),
    Column("turn_id", String(ID_LENGTH), nullable=False),
    Column("task_id", String(ID_LENGTH), nullable=False),
    Column("sequence", BigInteger, nullable=False),
    Column("tool_name", String(255), nullable=False),
    Column("scope_digest", String(64), nullable=False),
    Column("argument_hash", String(64), nullable=False),
    Column("arguments", JSON, nullable=False),
    Column("command_run_id", String(ID_LENGTH)),
    Column("status", String(32), nullable=False),
    Column("public_summary", String),
    Column("artifact_ids", JSON, nullable=False),
    Column("error_code", String(128)),
    Column("created_at", UTCDateTime(), nullable=False),
    Column("updated_at", UTCDateTime(), nullable=False),
    PrimaryKeyConstraint("tenant_id", "id", name="pk_core_assistant_tool_invocations"),
    UniqueConstraint(
        "tenant_id",
        "turn_id",
        "sequence",
        name="uq_core_assistant_tool_invocations_turn_sequence",
    ),
    UniqueConstraint(
        "tenant_id",
        "turn_id",
        "argument_hash",
        name="uq_core_assistant_tool_invocations_turn_arguments",
    ),
    CheckConstraint("sequence > 0", name="ck_core_assistant_tool_invocations_sequence"),
    CheckConstraint(
        "status IN ('created', 'queued', 'running', 'completed', 'failed', "
        "'rejected', 'cancelled')",
        name="ck_core_assistant_tool_invocations_status",
    ),
    CheckConstraint(
        "length(scope_digest) = 64 AND scope_digest = lower(scope_digest)",
        name="ck_core_assistant_tool_invocations_scope_digest",
    ),
    CheckConstraint(
        "length(argument_hash) = 64 AND argument_hash = lower(argument_hash)",
        name="ck_core_assistant_tool_invocations_argument_hash",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "turn_id", "task_id"],
        [assistant_turns.c.tenant_id, assistant_turns.c.id, assistant_turns.c.task_id],
        name="fk_core_assistant_tool_invocations_turn_task",
    ),
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
    Column("created_at", UTCDateTime(), nullable=False),
    Column("updated_at", UTCDateTime(), nullable=False),
    PrimaryKeyConstraint("tenant_id", "id", name="pk_core_changesets"),
    UniqueConstraint(
        "tenant_id",
        "idempotency_key",
        name="uq_core_changesets_tenant_idempotency",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "project_id"],
        [projects.c.tenant_id, projects.c.id],
        name="fk_core_changesets_project",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "conversation_id"],
        [conversations.c.tenant_id, conversations.c.id],
        name="fk_core_changesets_conversation",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "task_id"],
        [tasks.c.tenant_id, tasks.c.id],
        name="fk_core_changesets_task",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "version_id"],
        [versions.c.tenant_id, versions.c.id],
        name="fk_core_changesets_version",
        ondelete="CASCADE",
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
    Column("created_at", UTCDateTime(), nullable=False),
    Column("decided_at", UTCDateTime()),
    PrimaryKeyConstraint("tenant_id", "id", name="pk_core_approvals"),
    ForeignKeyConstraint(
        ["tenant_id", "task_id"],
        [tasks.c.tenant_id, tasks.c.id],
        name="fk_core_approvals_task",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "changeset_id"],
        [changesets.c.tenant_id, changesets.c.id],
        name="fk_core_approvals_changeset",
        ondelete="CASCADE",
    ),
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
    Column("created_at", UTCDateTime(), nullable=False),
    PrimaryKeyConstraint("tenant_id", "id", name="pk_core_checkpoints"),
    ForeignKeyConstraint(
        ["tenant_id", "task_id"],
        [tasks.c.tenant_id, tasks.c.id],
        name="fk_core_checkpoints_task",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "version_id"],
        [versions.c.tenant_id, versions.c.id],
        name="fk_core_checkpoints_version",
        ondelete="CASCADE",
    ),
)

runtime_sessions = Table(
    "core_runtime_sessions",
    state_metadata,
    _tenant_id(),
    _id(),
    Column("project_id", String(ID_LENGTH)),
    Column("conversation_id", String(ID_LENGTH), nullable=False),
    Column("task_id", String(ID_LENGTH), nullable=False),
    Column("version_id", String(ID_LENGTH)),
    Column("project_root", String(4096), nullable=False),
    Column("execution_target", String(32), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("executor", String(128), nullable=False),
    Column("executor_handle", String(512)),
    Column("port", Integer),
    Column("status", String(32), nullable=False),
    Column("health", String(32), nullable=False),
    Column("error_code", String(128)),
    Column("idempotency_key", String(512), nullable=False),
    Column("revision", BigInteger, nullable=False),
    Column("created_at", UTCDateTime(), nullable=False),
    Column("updated_at", UTCDateTime(), nullable=False),
    PrimaryKeyConstraint("tenant_id", "id", name="pk_core_runtime_sessions"),
    UniqueConstraint(
        "tenant_id",
        "idempotency_key",
        name="uq_core_runtime_sessions_tenant_idempotency",
    ),
    CheckConstraint(
        "(executor_handle IS NULL AND port IS NULL) OR "
        "(executor_handle IS NOT NULL AND port BETWEEN 1 AND 65535)",
        name="ck_core_runtime_sessions_handle_port",
    ),
    CheckConstraint(
        "execution_target IN ('local', 'cloud')",
        name="ck_core_runtime_sessions_execution_target",
    ),
    CheckConstraint(
        "status IN ('created', 'starting', 'running', 'stopping', 'stopped', "
        "'failed', 'interrupted')",
        name="ck_core_runtime_sessions_status",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "project_id"],
        [projects.c.tenant_id, projects.c.id],
        name="fk_core_runtime_sessions_project",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "conversation_id"],
        [conversations.c.tenant_id, conversations.c.id],
        name="fk_core_runtime_sessions_conversation",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "task_id"],
        [tasks.c.tenant_id, tasks.c.id],
        name="fk_core_runtime_sessions_task",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "version_id"],
        [versions.c.tenant_id, versions.c.id],
        name="fk_core_runtime_sessions_version",
        ondelete="CASCADE",
    ),
)

preview_sessions = Table(
    "core_preview_sessions",
    state_metadata,
    _tenant_id(),
    _id(),
    Column("project_id", String(ID_LENGTH)),
    Column("conversation_id", String(ID_LENGTH), nullable=False),
    Column("task_id", String(ID_LENGTH), nullable=False),
    Column("version_id", String(ID_LENGTH)),
    Column("runtime_id", String(ID_LENGTH), nullable=False),
    Column("project_root", String(4096), nullable=False),
    Column("execution_target", String(32), nullable=False),
    Column("url", String(4096)),
    Column("visibility", String(32), nullable=False),
    Column("status", String(32), nullable=False),
    Column("health", String(32), nullable=False),
    Column("error_code", String(128)),
    Column("idempotency_key", String(512), nullable=False),
    Column("revision", BigInteger, nullable=False),
    Column("created_at", UTCDateTime(), nullable=False),
    Column("updated_at", UTCDateTime(), nullable=False),
    PrimaryKeyConstraint("tenant_id", "id", name="pk_core_preview_sessions"),
    UniqueConstraint(
        "tenant_id",
        "idempotency_key",
        name="uq_core_preview_sessions_tenant_idempotency",
    ),
    CheckConstraint(
        "(status IN ('ready', 'stopping') AND url IS NOT NULL) OR "
        "(status IN ('created', 'starting', 'stopped', 'failed') AND url IS NULL) OR "
        "status = 'interrupted'",
        name="ck_core_preview_sessions_active_url",
    ),
    CheckConstraint(
        "execution_target IN ('local', 'cloud')",
        name="ck_core_preview_sessions_execution_target",
    ),
    CheckConstraint(
        "status IN ('created', 'starting', 'ready', 'stopping', 'stopped', "
        "'failed', 'interrupted')",
        name="ck_core_preview_sessions_status",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "project_id"],
        [projects.c.tenant_id, projects.c.id],
        name="fk_core_preview_sessions_project",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "conversation_id"],
        [conversations.c.tenant_id, conversations.c.id],
        name="fk_core_preview_sessions_conversation",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "task_id"],
        [tasks.c.tenant_id, tasks.c.id],
        name="fk_core_preview_sessions_task",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "version_id"],
        [versions.c.tenant_id, versions.c.id],
        name="fk_core_preview_sessions_version",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "runtime_id"],
        [runtime_sessions.c.tenant_id, runtime_sessions.c.id],
        name="fk_core_preview_sessions_runtime",
        ondelete="CASCADE",
    ),
)

artifacts = Table(
    "core_artifacts",
    state_metadata,
    _tenant_id(),
    _id(),
    Column("project_id", String(ID_LENGTH)),
    Column("conversation_id", String(ID_LENGTH), nullable=False),
    Column("task_id", String(ID_LENGTH), nullable=False),
    Column("version_id", String(ID_LENGTH)),
    Column("artifact_type", String(32), nullable=False),
    Column("visibility", String(32), nullable=False),
    Column("storage_location", String(4096), nullable=False),
    Column("media_type", String(255), nullable=False),
    Column("byte_length", BigInteger, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("metadata", JSON, nullable=False),
    Column("created_at", UTCDateTime(), nullable=False),
    PrimaryKeyConstraint("tenant_id", "id", name="pk_core_artifacts"),
    CheckConstraint("byte_length >= 0", name="ck_core_artifacts_byte_length"),
    CheckConstraint(
        "length(content_hash) = 64 AND content_hash = lower(content_hash)",
        name="ck_core_artifacts_content_hash",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "project_id"],
        [projects.c.tenant_id, projects.c.id],
        name="fk_core_artifacts_project",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "conversation_id"],
        [conversations.c.tenant_id, conversations.c.id],
        name="fk_core_artifacts_conversation",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "task_id"],
        [tasks.c.tenant_id, tasks.c.id],
        name="fk_core_artifacts_task",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "version_id"],
        [versions.c.tenant_id, versions.c.id],
        name="fk_core_artifacts_version",
        ondelete="CASCADE",
    ),
)

documents = Table(
    "core_documents",
    state_metadata,
    _tenant_id(),
    _id(),
    Column("project_id", String(ID_LENGTH)),
    Column("conversation_id", String(ID_LENGTH), nullable=False),
    Column("source_task_id", String(ID_LENGTH), nullable=False),
    Column("version_id", String(ID_LENGTH)),
    Column("filename", String(255), nullable=False),
    Column("media_type", String(255), nullable=False),
    Column("byte_length", BigInteger, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("storage_location", String(4096), nullable=False),
    Column("current_revision", Integer, nullable=False),
    Column("visibility", String(32), nullable=False),
    Column("status", String(32), nullable=False),
    Column("idempotency_key", String(512), nullable=False),
    Column("created_at", UTCDateTime(), nullable=False),
    Column("updated_at", UTCDateTime(), nullable=False),
    PrimaryKeyConstraint("tenant_id", "id", name="pk_core_documents"),
    UniqueConstraint(
        "tenant_id",
        "idempotency_key",
        name="uq_core_documents_tenant_idempotency",
    ),
    CheckConstraint("byte_length >= 0", name="ck_core_documents_byte_length"),
    CheckConstraint("current_revision > 0", name="ck_core_documents_current_revision"),
    CheckConstraint(
        "length(content_hash) = 64 AND content_hash = lower(content_hash)",
        name="ck_core_documents_content_hash",
    ),
    CheckConstraint(
        "visibility IN ('conversation', 'project')",
        name="ck_core_documents_visibility",
    ),
    CheckConstraint(
        "status IN ('active', 'deleted')",
        name="ck_core_documents_status",
    ),
    CheckConstraint(
        "visibility != 'project' OR project_id IS NOT NULL",
        name="ck_core_documents_project_visibility",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "project_id"],
        [projects.c.tenant_id, projects.c.id],
        name="fk_core_documents_project",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "conversation_id"],
        [conversations.c.tenant_id, conversations.c.id],
        name="fk_core_documents_conversation",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "source_task_id"],
        [tasks.c.tenant_id, tasks.c.id],
        name="fk_core_documents_source_task",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "version_id"],
        [versions.c.tenant_id, versions.c.id],
        name="fk_core_documents_version",
    ),
)

document_revisions = Table(
    "core_document_revisions",
    state_metadata,
    _tenant_id(),
    Column("document_id", String(ID_LENGTH), primary_key=True),
    Column("revision", Integer, primary_key=True),
    Column("content_hash", String(64), nullable=False),
    Column("byte_length", BigInteger, nullable=False),
    Column("media_type", String(255), nullable=False),
    Column("parser", String(128), nullable=False),
    Column("parser_version", String(128), nullable=False),
    Column("section_count", Integer, nullable=False),
    Column("chunk_count", Integer, nullable=False),
    Column("created_at", UTCDateTime(), nullable=False),
    PrimaryKeyConstraint(
        "tenant_id",
        "document_id",
        "revision",
        name="pk_core_document_revisions",
    ),
    CheckConstraint("revision > 0", name="ck_core_document_revisions_revision"),
    CheckConstraint("byte_length >= 0", name="ck_core_document_revisions_byte_length"),
    CheckConstraint(
        "section_count BETWEEN 1 AND 10000",
        name="ck_core_document_revisions_section_count",
    ),
    CheckConstraint(
        "chunk_count BETWEEN 1 AND 100000",
        name="ck_core_document_revisions_chunk_count",
    ),
    CheckConstraint(
        "length(content_hash) = 64 AND content_hash = lower(content_hash)",
        name="ck_core_document_revisions_content_hash",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "document_id"],
        [documents.c.tenant_id, documents.c.id],
        name="fk_core_document_revisions_document",
        ondelete="CASCADE",
    ),
)

document_chunks = Table(
    "core_document_chunks",
    state_metadata,
    _tenant_id(),
    _id(),
    Column("fts_rowid", BigInteger, nullable=False),
    Column("document_id", String(ID_LENGTH), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("revision_hash", String(64), nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("section_ordinal", Integer, nullable=False),
    Column("locator", JSON, nullable=False),
    Column("normalized_text", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("token_count", Integer, nullable=False),
    Column("updated_at", UTCDateTime(), nullable=False),
    PrimaryKeyConstraint("tenant_id", "id", name="pk_core_document_chunks"),
    UniqueConstraint(
        "tenant_id",
        "document_id",
        "revision",
        "ordinal",
        name="uq_core_document_chunks_revision_ordinal",
    ),
    CheckConstraint("fts_rowid > 0", name="ck_core_document_chunks_fts_rowid"),
    CheckConstraint("revision > 0", name="ck_core_document_chunks_revision"),
    CheckConstraint("ordinal >= 0", name="ck_core_document_chunks_ordinal"),
    CheckConstraint(
        "section_ordinal >= 0",
        name="ck_core_document_chunks_section_ordinal",
    ),
    CheckConstraint(
        "token_count BETWEEN 1 AND 20000",
        name="ck_core_document_chunks_token_count",
    ),
    CheckConstraint(
        "length(revision_hash) = 64 AND revision_hash = lower(revision_hash)",
        name="ck_core_document_chunks_revision_hash",
    ),
    CheckConstraint(
        "length(content_hash) = 64 AND content_hash = lower(content_hash)",
        name="ck_core_document_chunks_content_hash",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "document_id", "revision"],
        [
            document_revisions.c.tenant_id,
            document_revisions.c.document_id,
            document_revisions.c.revision,
        ],
        name="fk_core_document_chunks_revision",
        ondelete="CASCADE",
    ),
)

research_evidence = Table(
    "core_research_evidence",
    state_metadata,
    _tenant_id(),
    _id(),
    Column("artifact_id", String(ID_LENGTH), nullable=False),
    Column("project_id", String(ID_LENGTH)),
    Column("conversation_id", String(ID_LENGTH), nullable=False),
    Column("task_id", String(ID_LENGTH), nullable=False),
    Column("version_id", String(ID_LENGTH)),
    Column("ordinal", Integer, nullable=False),
    Column("source_url", String(2048), nullable=False),
    Column("canonical_url", String(2048), nullable=False),
    Column("redirect_chain", JSON, nullable=False),
    Column("title", String(1000), nullable=False),
    Column("media_type", String(255), nullable=False),
    Column("byte_length", BigInteger, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("excerpt", Text, nullable=False),
    Column("fetched_at", UTCDateTime(), nullable=False),
    Column("created_at", UTCDateTime(), nullable=False),
    PrimaryKeyConstraint("tenant_id", "id", name="pk_core_research_evidence"),
    UniqueConstraint(
        "tenant_id",
        "artifact_id",
        "ordinal",
        name="uq_core_research_evidence_artifact_ordinal",
    ),
    UniqueConstraint(
        "tenant_id",
        "artifact_id",
        "canonical_url",
        name="uq_core_research_evidence_artifact_url",
    ),
    CheckConstraint("ordinal > 0", name="ck_core_research_evidence_ordinal"),
    CheckConstraint("byte_length >= 0", name="ck_core_research_evidence_byte_length"),
    CheckConstraint(
        "length(content_hash) = 64 AND content_hash = lower(content_hash)",
        name="ck_core_research_evidence_content_hash",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "artifact_id"],
        [artifacts.c.tenant_id, artifacts.c.id],
        name="fk_core_research_evidence_artifact",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "project_id"],
        [projects.c.tenant_id, projects.c.id],
        name="fk_core_research_evidence_project",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "conversation_id"],
        [conversations.c.tenant_id, conversations.c.id],
        name="fk_core_research_evidence_conversation",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "task_id"],
        [tasks.c.tenant_id, tasks.c.id],
        name="fk_core_research_evidence_task",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "version_id"],
        [versions.c.tenant_id, versions.c.id],
        name="fk_core_research_evidence_version",
    ),
)

Index("ix_core_projects_tenant_updated", projects.c.tenant_id, projects.c.updated_at)
Index("ix_core_conversations_tenant_project", conversations.c.tenant_id, conversations.c.project_id)
Index("ix_core_versions_tenant_project", versions.c.tenant_id, versions.c.project_id)
Index("ix_core_tasks_tenant_status", tasks.c.tenant_id, tasks.c.status, tasks.c.created_at)
Index(
    "ix_core_assistant_turns_tenant_task",
    assistant_turns.c.tenant_id,
    assistant_turns.c.task_id,
    assistant_turns.c.created_at,
)
Index(
    "ix_core_assistant_messages_tenant_conversation",
    assistant_messages.c.tenant_id,
    assistant_messages.c.conversation_id,
    assistant_messages.c.sequence,
)
Index(
    "ix_core_assistant_tool_invocations_tenant_turn",
    assistant_tool_invocations.c.tenant_id,
    assistant_tool_invocations.c.turn_id,
    assistant_tool_invocations.c.sequence,
)
Index("ix_core_changesets_tenant_task", changesets.c.tenant_id, changesets.c.task_id)
Index("ix_core_approvals_tenant_task", approvals.c.tenant_id, approvals.c.task_id)
Index("ix_core_checkpoints_tenant_task", checkpoints.c.tenant_id, checkpoints.c.task_id)
Index(
    "ix_core_runtime_sessions_tenant_task",
    runtime_sessions.c.tenant_id,
    runtime_sessions.c.task_id,
    runtime_sessions.c.created_at,
)
Index(
    "ix_core_preview_sessions_tenant_conversation",
    preview_sessions.c.tenant_id,
    preview_sessions.c.conversation_id,
    preview_sessions.c.created_at,
)
Index(
    "uq_core_preview_sessions_active_task",
    preview_sessions.c.tenant_id,
    preview_sessions.c.task_id,
    unique=True,
    sqlite_where=text("status IN ('created', 'starting', 'ready', 'stopping')"),
    postgresql_where=text("status IN ('created', 'starting', 'ready', 'stopping')"),
)
Index(
    "ix_core_artifacts_tenant_task",
    artifacts.c.tenant_id,
    artifacts.c.task_id,
    artifacts.c.created_at,
)
Index(
    "ix_core_documents_tenant_conversation",
    documents.c.tenant_id,
    documents.c.conversation_id,
    documents.c.status,
    documents.c.created_at,
)
Index(
    "ix_core_documents_tenant_project",
    documents.c.tenant_id,
    documents.c.project_id,
    documents.c.status,
    documents.c.created_at,
)
Index(
    "ix_core_document_revisions_tenant_document",
    document_revisions.c.tenant_id,
    document_revisions.c.document_id,
    document_revisions.c.revision,
)
Index(
    "ix_core_document_chunks_tenant_document",
    document_chunks.c.tenant_id,
    document_chunks.c.document_id,
    document_chunks.c.revision,
    document_chunks.c.ordinal,
)
Index(
    "uq_core_document_chunks_fts_rowid",
    document_chunks.c.fts_rowid,
    unique=True,
)
Index(
    "ix_core_research_evidence_tenant_artifact",
    research_evidence.c.tenant_id,
    research_evidence.c.artifact_id,
    research_evidence.c.ordinal,
)
