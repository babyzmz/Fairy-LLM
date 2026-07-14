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

from fairy_core.storage.types import UTCDateTime


def build_presentation_schema(
    *, metadata: MetaData, workspaces: Table, versions: Table
) -> tuple[Table, Table, Table, Table, Table, Table, Table]:
    jobs = Table(
        "core_file_render_jobs",
        metadata,
        Column("tenant_id", String(128), nullable=False),
        Column("id", String(36), nullable=False),
        Column("workspace_id", String(36), nullable=False),
        Column("version_id", String(36), nullable=False),
        Column("file_set_id", String(36), nullable=False),
        Column("source_path", String(4096), nullable=False),
        Column("source_hash", String(64), nullable=False),
        Column("cache_key", String(64), nullable=False),
        Column("requested_mode", String(64), nullable=False),
        Column("renderer_pack_id", String(128)),
        Column("renderer_pack_version", String(64)),
        Column("status", String(32), nullable=False),
        Column("progress", BigInteger, nullable=False),
        Column("error_code", String(128)),
        Column("public_summary", String(1000)),
        Column("created_at", UTCDateTime(), nullable=False),
        Column("updated_at", UTCDateTime(), nullable=False),
        PrimaryKeyConstraint("tenant_id", "id", name="pk_core_file_render_jobs"),
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "version_id",
            "cache_key",
            name="uq_core_file_render_jobs_cache",
        ),
        CheckConstraint("progress BETWEEN 0 AND 100", name="ck_core_file_render_jobs_progress"),
        CheckConstraint(
            "length(source_hash) = 64 AND source_hash = lower(source_hash)",
            name="ck_core_file_render_jobs_source_hash",
        ),
        CheckConstraint(
            "length(cache_key) = 64 AND cache_key = lower(cache_key)",
            name="ck_core_file_render_jobs_cache_key",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            [workspaces.c.tenant_id, workspaces.c.id],
            name="fk_core_file_render_jobs_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            [versions.c.tenant_id, versions.c.id],
            name="fk_core_file_render_jobs_version",
            ondelete="CASCADE",
        ),
    )
    presentations = Table(
        "core_file_presentations",
        metadata,
        Column("tenant_id", String(128), nullable=False),
        Column("id", String(36), nullable=False),
        Column("job_id", String(36), nullable=False),
        Column("workspace_id", String(36), nullable=False),
        Column("version_id", String(36), nullable=False),
        Column("file_set_id", String(36), nullable=False),
        Column("source_path", String(4096), nullable=False),
        Column("source_hash", String(64), nullable=False),
        Column("renderer", String(128), nullable=False),
        Column("fidelity", String(32), nullable=False),
        Column("status", String(32), nullable=False),
        Column("capabilities", JSON, nullable=False),
        Column("created_at", UTCDateTime(), nullable=False),
        PrimaryKeyConstraint("tenant_id", "id", name="pk_core_file_presentations"),
        UniqueConstraint("tenant_id", "job_id", name="uq_core_file_presentations_job"),
        ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            [jobs.c.tenant_id, jobs.c.id],
            name="fk_core_file_presentations_job",
            ondelete="CASCADE",
        ),
    )
    assets = Table(
        "core_derived_assets",
        metadata,
        Column("tenant_id", String(128), nullable=False),
        Column("id", String(36), nullable=False),
        Column("presentation_id", String(36), nullable=False),
        Column("role", String(64), nullable=False),
        Column("media_type", String(255), nullable=False),
        Column("content_hash", String(64), nullable=False),
        Column("byte_length", BigInteger, nullable=False),
        Column("storage_key", String(4096), nullable=False),
        Column("metadata", JSON, nullable=False),
        PrimaryKeyConstraint("tenant_id", "id", name="pk_core_derived_assets"),
        UniqueConstraint(
            "tenant_id",
            "presentation_id",
            "role",
            "content_hash",
            name="uq_core_derived_assets_role_hash",
        ),
        CheckConstraint("byte_length >= 0", name="ck_core_derived_assets_size"),
        ForeignKeyConstraint(
            ["tenant_id", "presentation_id"],
            [presentations.c.tenant_id, presentations.c.id],
            name="fk_core_derived_assets_presentation",
            ondelete="CASCADE",
        ),
    )
    packs = Table(
        "core_renderer_packs",
        metadata,
        Column("tenant_id", String(128), nullable=False),
        Column("id", String(128), nullable=False),
        Column("version", String(64), nullable=False),
        Column("platform", String(64), nullable=False),
        Column("manifest", JSON, nullable=False),
        Column("payload_hash", String(64), nullable=False),
        Column("install_path", String(4096), nullable=False),
        Column("health", String(32), nullable=False),
        Column("installed_at", UTCDateTime(), nullable=False),
        PrimaryKeyConstraint("tenant_id", "id", "version", name="pk_core_renderer_packs"),
        CheckConstraint(
            "length(payload_hash) = 64 AND payload_hash = lower(payload_hash)",
            name="ck_core_renderer_packs_payload_hash",
        ),
    )
    annotations = Table(
        "core_annotation_documents",
        metadata,
        Column("tenant_id", String(128), nullable=False),
        Column("id", String(36), nullable=False),
        Column("workspace_id", String(36), nullable=False),
        Column("version_id", String(36), nullable=False),
        Column("file_set_id", String(36), nullable=False),
        Column("source_hash", String(64), nullable=False),
        Column("revision", BigInteger, nullable=False),
        Column("annotations", JSON, nullable=False),
        Column("created_at", UTCDateTime(), nullable=False),
        Column("updated_at", UTCDateTime(), nullable=False),
        PrimaryKeyConstraint("tenant_id", "id", name="pk_core_annotation_documents"),
        UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "version_id",
            "file_set_id",
            name="uq_core_annotation_documents_file_set",
        ),
        CheckConstraint("revision > 0", name="ck_core_annotation_documents_revision"),
        CheckConstraint(
            "length(source_hash) = 64 AND source_hash = lower(source_hash)",
            name="ck_core_annotation_documents_source_hash",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            [workspaces.c.tenant_id, workspaces.c.id],
            name="fk_core_annotation_documents_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            [versions.c.tenant_id, versions.c.id],
            name="fk_core_annotation_documents_version",
            ondelete="CASCADE",
        ),
    )
    edit_recipes = Table(
        "core_edit_recipes",
        metadata,
        Column("tenant_id", String(128), nullable=False),
        Column("id", String(36), nullable=False),
        Column("workspace_id", String(36), nullable=False),
        Column("version_id", String(36), nullable=False),
        Column("file_set_id", String(36), nullable=False),
        Column("source_hash", String(64), nullable=False),
        Column("kind", String(64), nullable=False),
        Column("operations", JSON, nullable=False),
        Column("status", String(32), nullable=False),
        Column("revision", BigInteger, nullable=False),
        Column("created_at", UTCDateTime(), nullable=False),
        Column("updated_at", UTCDateTime(), nullable=False),
        PrimaryKeyConstraint("tenant_id", "id", name="pk_core_edit_recipes"),
        CheckConstraint("revision > 0", name="ck_core_edit_recipes_revision"),
        CheckConstraint(
            "length(source_hash) = 64 AND source_hash = lower(source_hash)",
            name="ck_core_edit_recipes_source_hash",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            [workspaces.c.tenant_id, workspaces.c.id],
            name="fk_core_edit_recipes_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            [versions.c.tenant_id, versions.c.id],
            name="fk_core_edit_recipes_version",
            ondelete="CASCADE",
        ),
    )
    selections = Table(
        "core_selection_references",
        metadata,
        Column("tenant_id", String(128), nullable=False),
        Column("id", String(36), nullable=False),
        Column("workspace_id", String(36), nullable=False),
        Column("version_id", String(36), nullable=False),
        Column("file_set_id", String(36), nullable=False),
        Column("source_path", String(4096), nullable=False),
        Column("source_hash", String(64), nullable=False),
        Column("viewer_kind", String(64), nullable=False),
        Column("locator_kind", String(64), nullable=False),
        Column("locator", JSON, nullable=False),
        Column("created_at", UTCDateTime(), nullable=False),
        PrimaryKeyConstraint("tenant_id", "id", name="pk_core_selection_references"),
        CheckConstraint(
            "length(source_hash) = 64 AND source_hash = lower(source_hash)",
            name="ck_core_selection_references_source_hash",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            [workspaces.c.tenant_id, workspaces.c.id],
            name="fk_core_selection_references_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            [versions.c.tenant_id, versions.c.id],
            name="fk_core_selection_references_version",
            ondelete="CASCADE",
        ),
    )
    Index("ix_core_file_render_jobs_status", jobs.c.tenant_id, jobs.c.status)
    Index(
        "ix_core_file_presentations_version",
        presentations.c.tenant_id,
        presentations.c.workspace_id,
        presentations.c.version_id,
    )
    return jobs, presentations, assets, packs, annotations, edit_recipes, selections


__all__ = ["build_presentation_schema"]
