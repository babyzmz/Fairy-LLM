from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKeyConstraint,
    Index,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    UniqueConstraint,
)

from fairy_core.persistence.tenant import TENANT_ID_LENGTH
from fairy_core.storage.types import UTCDateTime


def build_media_schema(
    *,
    metadata: MetaData,
    projects: Table,
    workspaces: Table,
    conversations: Table,
    tasks: Table,
    versions: Table,
    assistant_turns: Table,
) -> Table:
    jobs = Table(
        "core_media_generation_jobs",
        metadata,
        Column("tenant_id", String(TENANT_ID_LENGTH), nullable=False),
        Column("id", String(36), nullable=False),
        Column("project_id", String(36)),
        Column("workspace_id", String(36), nullable=False),
        Column("conversation_id", String(36), nullable=False),
        Column("task_id", String(36), nullable=False),
        Column("version_id", String(36), nullable=False),
        Column("turn_id", String(36)),
        Column("command_run_id", String(36), nullable=False),
        Column("scope_digest", String(64), nullable=False),
        Column("kind", String(16), nullable=False),
        Column("model_id", String(255), nullable=False),
        Column("endpoint_kind", String(32), nullable=False),
        Column("output_path", String(1024), nullable=False),
        Column("request_spec", JSON, nullable=False),
        Column("request_fingerprint", String(64), nullable=False),
        Column("idempotency_key", String(512), nullable=False),
        Column("artifact_id", String(36), nullable=False),
        Column("status", String(32), nullable=False),
        Column("provider_job_id", String(255)),
        Column("progress", BigInteger, nullable=False),
        Column("usage_cost", String(64)),
        Column("error_code", String(128)),
        Column("revision", BigInteger, nullable=False),
        Column("created_at", UTCDateTime(), nullable=False),
        Column("updated_at", UTCDateTime(), nullable=False),
        PrimaryKeyConstraint("tenant_id", "id", name="pk_core_media_generation_jobs"),
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_core_media_generation_jobs_idempotency",
        ),
        UniqueConstraint(
            "tenant_id",
            "artifact_id",
            name="uq_core_media_generation_jobs_artifact",
        ),
        CheckConstraint(
            "kind IN ('image', 'music', 'video')",
            name="ck_core_media_generation_jobs_kind",
        ),
        CheckConstraint(
            "endpoint_kind IN ('images', 'audio', 'videos')",
            name="ck_core_media_generation_jobs_endpoint",
        ),
        CheckConstraint(
            "status IN ('created', 'generating', 'pending', 'in_progress', "
            "'completed', 'failed', 'cancelled', 'interrupted')",
            name="ck_core_media_generation_jobs_status",
        ),
        CheckConstraint(
            "progress BETWEEN 0 AND 100",
            name="ck_core_media_generation_jobs_progress",
        ),
        CheckConstraint("revision >= 0", name="ck_core_media_generation_jobs_revision"),
        CheckConstraint(
            "length(scope_digest) = 64 AND scope_digest = lower(scope_digest)",
            name="ck_core_media_generation_jobs_scope_digest",
        ),
        CheckConstraint(
            "length(request_fingerprint) = 64 AND request_fingerprint = lower(request_fingerprint)",
            name="ck_core_media_generation_jobs_fingerprint",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            [projects.c.tenant_id, projects.c.id],
            name="fk_core_media_generation_jobs_project",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            [workspaces.c.tenant_id, workspaces.c.id],
            name="fk_core_media_generation_jobs_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            [conversations.c.tenant_id, conversations.c.id],
            name="fk_core_media_generation_jobs_conversation",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            [tasks.c.tenant_id, tasks.c.id],
            name="fk_core_media_generation_jobs_task",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            [versions.c.tenant_id, versions.c.id],
            name="fk_core_media_generation_jobs_version",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "turn_id", "conversation_id", "task_id"],
            [
                assistant_turns.c.tenant_id,
                assistant_turns.c.id,
                assistant_turns.c.conversation_id,
                assistant_turns.c.task_id,
            ],
            name="fk_core_media_generation_jobs_turn_scope",
            ondelete="CASCADE",
        ),
    )
    Index(
        "ix_core_media_generation_jobs_recovery",
        jobs.c.tenant_id,
        jobs.c.status,
        jobs.c.updated_at,
    )
    Index(
        "ix_core_media_generation_jobs_task",
        jobs.c.tenant_id,
        jobs.c.task_id,
        jobs.c.created_at,
    )
    Index(
        "ix_core_media_generation_jobs_provider",
        jobs.c.tenant_id,
        jobs.c.provider_job_id,
    )
    return jobs


__all__ = ["build_media_schema"]
