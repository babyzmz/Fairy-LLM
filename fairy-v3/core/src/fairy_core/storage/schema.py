from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
)

from fairy_core.persistence.tenant import TENANT_ID_LENGTH
from fairy_core.storage.assistant_attempt_schema import build_assistant_attempt_tables
from fairy_core.storage.history_schema import build_history_tables
from fairy_core.storage.index_schema import build_state_indexes
from fairy_core.storage.planning_schema import build_planning_schema
from fairy_core.storage.types import UTCDateTime

ID_LENGTH = 36

state_metadata = MetaData()


def _tenant_id() -> Column[str]:
    return Column("tenant_id", String(TENANT_ID_LENGTH), primary_key=True)


def _id() -> Column[str]:
    return Column("id", String(ID_LENGTH), primary_key=True)


workspaces = Table(
    "core_workspaces",
    state_metadata,
    _tenant_id(),
    _id(),
    Column("active_version_id", String(ID_LENGTH)),
    Column("active_preview_id", String(ID_LENGTH)),
    Column("revision", BigInteger, nullable=False),
    Column("max_files", BigInteger, nullable=False),
    Column("max_bytes", BigInteger, nullable=False),
    Column("created_at", UTCDateTime(), nullable=False),
    Column("updated_at", UTCDateTime(), nullable=False),
    PrimaryKeyConstraint("tenant_id", "id", name="pk_core_workspaces"),
    CheckConstraint("revision >= 0", name="ck_core_workspaces_revision"),
    CheckConstraint("max_files > 0", name="ck_core_workspaces_max_files"),
    CheckConstraint("max_bytes > 0", name="ck_core_workspaces_max_bytes"),
)


projects = Table(
    "core_projects",
    state_metadata,
    _tenant_id(),
    _id(),
    Column("name", String(255), nullable=False),
    Column("residency", String(32), nullable=False),
    Column("workspace_id", String(ID_LENGTH), nullable=False),
    Column("active_version_id", String(ID_LENGTH)),
    Column("active_preview_id", String(ID_LENGTH)),
    Column("revision", BigInteger, nullable=False),
    Column("created_at", UTCDateTime(), nullable=False),
    Column("updated_at", UTCDateTime(), nullable=False),
    PrimaryKeyConstraint("tenant_id", "id", name="pk_core_projects"),
    ForeignKeyConstraint(
        ["tenant_id", "workspace_id"],
        [workspaces.c.tenant_id, workspaces.c.id],
        name="fk_core_projects_workspace",
    ),
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
mcp_servers = Table(
    "core_mcp_servers",
    state_metadata,
    _tenant_id(),
    Column("server_id", String(64), primary_key=True),
    Column("display_name", String(200), nullable=False),
    Column("transport", String(32), nullable=False),
    Column("command", Text),
    Column("arguments", JSON, nullable=False),
    Column("endpoint", Text),
    Column("credential_ref", String(288)),
    Column("environment_refs", JSON, nullable=False),
    Column("enabled", Boolean, nullable=False),
    Column("status", String(32), nullable=False),
    Column("accepted_schema_digest", String(64)),
    Column("pending_schema_digest", String(64)),
    Column("accepted_tools", JSON, nullable=False),
    Column("pending_tools", JSON, nullable=False),
    Column("policies", JSON, nullable=False),
    Column("revision", BigInteger, nullable=False),
    Column("last_error_code", String(128)),
    Column("created_at", UTCDateTime(), nullable=False),
    Column("updated_at", UTCDateTime(), nullable=False),
    PrimaryKeyConstraint("tenant_id", "server_id", name="pk_core_mcp_servers"),
    CheckConstraint(
        "transport IN ('stdio', 'streamable_http')",
        name="ck_core_mcp_servers_transport",
    ),
    CheckConstraint(
        "status IN ('disabled', 'untrusted', 'review_required', 'ready', 'unavailable')",
        name="ck_core_mcp_servers_status",
    ),
    CheckConstraint("revision >= 1", name="ck_core_mcp_servers_revision"),
    CheckConstraint(
        "(transport = 'stdio' AND command IS NOT NULL AND endpoint IS NULL) OR "
        "(transport = 'streamable_http' AND command IS NULL AND endpoint IS NOT NULL)",
        name="ck_core_mcp_servers_connection",
    ),
)
mcp_server_updates = Table(
    "core_mcp_server_updates",
    state_metadata,
    _tenant_id(),
    Column("idempotency_key", String(512), primary_key=True),
    Column("request_fingerprint", String(64), nullable=False),
    Column("server_id", String(64), nullable=False),
    Column("result_record", JSON),
    Column("result_deleted", Boolean),
    Column("result_error_code", String(128)),
    Column("created_at", UTCDateTime(), nullable=False),
    PrimaryKeyConstraint("tenant_id", "idempotency_key", name="pk_core_mcp_server_updates"),
    CheckConstraint(
        "length(request_fingerprint) = 64 AND request_fingerprint = lower(request_fingerprint)",
        name="ck_core_mcp_server_updates_fingerprint",
    ),
    CheckConstraint(
        "(result_record IS NULL AND result_deleted IS NULL AND result_error_code IS NULL) OR "
        "(result_record IS NOT NULL AND result_deleted = false AND result_error_code IS NULL) OR "
        "(result_record IS NULL AND result_deleted = true AND result_error_code IS NULL) OR "
        "(result_record IS NULL AND result_deleted = false AND result_error_code IS NOT NULL)",
        name="ck_core_mcp_server_updates_result",
    ),
)
conversations = Table(
    "core_conversations",
    state_metadata,
    _tenant_id(),
    _id(),
    Column("project_id", String(ID_LENGTH)),
    Column("workspace_id", String(ID_LENGTH), nullable=False),
    Column("workspace_type", String(32), nullable=False),
    Column("base_version_id", String(ID_LENGTH)),
    Column("active_draft_version_id", String(ID_LENGTH)),
    Column("active_task_id", String(ID_LENGTH)),
    Column("active_preview_id", String(ID_LENGTH)),
    Column("title", String(200), nullable=False, server_default="New conversation"),
    Column("pinned_at", UTCDateTime()),
    Column("deleted_at", UTCDateTime()),
    Column("revision", BigInteger, nullable=False, server_default="0"),
    Column("created_at", UTCDateTime(), nullable=False),
    Column("updated_at", UTCDateTime(), nullable=False),
    PrimaryKeyConstraint("tenant_id", "id", name="pk_core_conversations"),
    ForeignKeyConstraint(
        ["tenant_id", "project_id"],
        [projects.c.tenant_id, projects.c.id],
        name="fk_core_conversations_project",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "workspace_id"],
        [workspaces.c.tenant_id, workspaces.c.id],
        name="fk_core_conversations_workspace",
    ),
)

versions = Table(
    "core_versions",
    state_metadata,
    _tenant_id(),
    _id(),
    Column("project_id", String(ID_LENGTH)),
    Column("workspace_id", String(ID_LENGTH), nullable=False),
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
        ["tenant_id", "workspace_id"],
        [workspaces.c.tenant_id, workspaces.c.id],
        name="fk_core_versions_workspace",
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
    Column("workspace_id", String(ID_LENGTH), nullable=False),
    Column("conversation_id", String(ID_LENGTH), nullable=False),
    Column("user_request", String, nullable=False),
    Column("operation_mode", String(64), nullable=False),
    Column("base_version_id", String(ID_LENGTH)),
    Column("target_version_id", String(ID_LENGTH)),
    Column("execution_target", String(32), nullable=False),
    Column("memory_snapshot_id", String(ID_LENGTH)),
    Column("memory_snapshot_hash", String(64)),
    Column("status", String(32), nullable=False),
    Column("display_title", String(200), nullable=False, server_default="Task"),
    Column("pinned_at", UTCDateTime()),
    Column("metadata_revision", BigInteger, nullable=False, server_default="0"),
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
        ["tenant_id", "workspace_id"],
        [workspaces.c.tenant_id, workspaces.c.id],
        name="fk_core_tasks_workspace",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "conversation_id"],
        [conversations.c.tenant_id, conversations.c.id],
        name="fk_core_tasks_conversation",
        ondelete="CASCADE",
    ),
)

execution_plans, task_steps = build_planning_schema(
    metadata=state_metadata,
    tasks=tasks,
    workspaces=workspaces,
    versions=versions,
)

task_workspaces = Table(
    "core_task_workspaces",
    state_metadata,
    _tenant_id(),
    Column("task_id", String(ID_LENGTH), primary_key=True),
    Column("project_id", String(ID_LENGTH)),
    Column("workspace_id", String(ID_LENGTH), nullable=False),
    Column("conversation_id", String(ID_LENGTH), nullable=False),
    Column("version_id", String(ID_LENGTH)),
    Column("root", String(4096), nullable=False),
    Column("editable_files", JSON, nullable=False),
    Column("reference_files", JSON, nullable=False),
    Column("constraints", JSON, nullable=False),
    Column("generation", BigInteger, nullable=False),
    Column("created_at", UTCDateTime(), nullable=False),
    PrimaryKeyConstraint("tenant_id", "task_id", name="pk_core_task_workspaces"),
    CheckConstraint("generation > 0", name="ck_core_task_workspaces_generation"),
    ForeignKeyConstraint(
        ["tenant_id", "task_id"],
        [tasks.c.tenant_id, tasks.c.id],
        name="fk_core_task_workspaces_task",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "project_id"],
        [projects.c.tenant_id, projects.c.id],
        name="fk_core_task_workspaces_project",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "workspace_id"],
        [workspaces.c.tenant_id, workspaces.c.id],
        name="fk_core_task_workspaces_workspace",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "conversation_id"],
        [conversations.c.tenant_id, conversations.c.id],
        name="fk_core_task_workspaces_conversation",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "version_id"],
        [versions.c.tenant_id, versions.c.id],
        name="fk_core_task_workspaces_version",
        ondelete="CASCADE",
    ),
)

project_indexes = Table(
    "core_project_indexes",
    state_metadata,
    _tenant_id(),
    Column("version_id", String(ID_LENGTH), primary_key=True),
    Column("project_id", String(ID_LENGTH)),
    Column("workspace_id", String(ID_LENGTH), nullable=False),
    Column("generation", BigInteger, nullable=False),
    Column("source_hash", String(64), nullable=False),
    Column("files", JSON, nullable=False),
    Column("created_at", UTCDateTime(), nullable=False),
    Column("updated_at", UTCDateTime(), nullable=False),
    PrimaryKeyConstraint("tenant_id", "version_id", name="pk_core_project_indexes"),
    CheckConstraint("generation > 0", name="ck_core_project_indexes_generation"),
    CheckConstraint(
        "length(source_hash) = 64 AND source_hash = lower(source_hash)",
        name="ck_core_project_indexes_source_hash",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "version_id"],
        [versions.c.tenant_id, versions.c.id],
        name="fk_core_project_indexes_version",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "project_id"],
        [projects.c.tenant_id, projects.c.id],
        name="fk_core_project_indexes_project",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "workspace_id"],
        [workspaces.c.tenant_id, workspaces.c.id],
        name="fk_core_project_indexes_workspace",
        ondelete="CASCADE",
    ),
)

Index(
    "ix_core_task_workspaces_tenant_version",
    task_workspaces.c.tenant_id,
    task_workspaces.c.version_id,
)
Index(
    "ix_core_project_indexes_tenant_project",
    project_indexes.c.tenant_id,
    project_indexes.c.project_id,
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

assistant_provider_attempts, assistant_message_sequences = build_assistant_attempt_tables(
    state_metadata,
    assistant_turns=assistant_turns,
    conversations=conversations,
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

assistant_imported_messages, conversation_moves = build_history_tables(
    metadata=state_metadata,
    tenant_id_column=_tenant_id,
    id_column=_id,
    conversations=conversations,
    assistant_messages=assistant_messages,
    projects=projects,
    utc_datetime=UTCDateTime,
    id_length=ID_LENGTH,
)

assistant_tool_invocations = Table(
    "core_assistant_tool_invocations",
    state_metadata,
    _tenant_id(),
    _id(),
    Column("turn_id", String(ID_LENGTH), nullable=False),
    Column("task_id", String(ID_LENGTH), nullable=False),
    Column("model_round", BigInteger, nullable=False),
    Column("sequence", BigInteger, nullable=False),
    Column("provider_call_id", String(255), nullable=False),
    Column("tool_name", String(255), nullable=False),
    Column("scope_digest", String(64), nullable=False),
    Column("argument_hash", String(64), nullable=False),
    Column("arguments", JSON, nullable=False),
    Column("command_run_id", String(ID_LENGTH)),
    Column("status", String(32), nullable=False),
    Column("public_summary", String),
    Column("model_content", String),
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
    UniqueConstraint(
        "tenant_id",
        "turn_id",
        "provider_call_id",
        name="uq_core_assistant_tool_invocations_turn_provider_call",
    ),
    CheckConstraint("model_round > 0", name="ck_core_assistant_tool_invocations_model_round"),
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
    Column("project_id", String(ID_LENGTH)),
    Column("workspace_id", String(ID_LENGTH), nullable=False),
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
        ["tenant_id", "workspace_id"],
        [workspaces.c.tenant_id, workspaces.c.id],
        name="fk_core_changesets_workspace",
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
    Column("tool_invocation_id", String(ID_LENGTH)),
    Column("requested_by", String(128), nullable=False),
    Column("reason", String, nullable=False),
    Column("decision", String(32), nullable=False),
    Column("decided_by", String(128)),
    Column("created_at", UTCDateTime(), nullable=False),
    Column("decided_at", UTCDateTime()),
    PrimaryKeyConstraint("tenant_id", "id", name="pk_core_approvals"),
    UniqueConstraint(
        "tenant_id",
        "command_run_id",
        name="uq_core_approvals_tenant_command_run",
    ),
    UniqueConstraint(
        "tenant_id",
        "tool_invocation_id",
        name="uq_core_approvals_tenant_tool_invocation",
    ),
    CheckConstraint(
        "changeset_id IS NULL OR tool_invocation_id IS NULL",
        name="ck_core_approvals_single_subject",
    ),
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
    ForeignKeyConstraint(
        ["tenant_id", "tool_invocation_id"],
        [assistant_tool_invocations.c.tenant_id, assistant_tool_invocations.c.id],
        name="fk_core_approvals_tool_invocation",
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
    Column("evidence_artifact_ids", JSON, nullable=False),
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

build_state_indexes(
    {
        "projects": projects,
        "conversations": conversations,
        "versions": versions,
        "tasks": tasks,
        "assistant_turns": assistant_turns,
        "assistant_provider_attempts": assistant_provider_attempts,
        "assistant_messages": assistant_messages,
        "assistant_tool_invocations": assistant_tool_invocations,
        "changesets": changesets,
        "approvals": approvals,
        "checkpoints": checkpoints,
        "runtime_sessions": runtime_sessions,
        "preview_sessions": preview_sessions,
        "artifacts": artifacts,
        "documents": documents,
        "document_revisions": document_revisions,
        "document_chunks": document_chunks,
        "research_evidence": research_evidence,
    }
)
