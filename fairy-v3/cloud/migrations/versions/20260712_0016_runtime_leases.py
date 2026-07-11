"""Add lock-bound dependency layers and fenced dynamic Runtime leases."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260712_0016"
down_revision: str | Sequence[str] | None = "20260711_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXECUTION_JOBS = "execution_jobs"
RUNTIMES = "runtime_leases"
WORKERS = "runtime_workers"
ROUTES = "runtime_routes"


def upgrade() -> None:
    op.add_column(
        "core_checkpoints",
        sa.Column(
            "evidence_artifact_ids",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )
    op.alter_column("core_checkpoints", "evidence_artifact_ids", server_default=None)
    op.add_column(EXECUTION_JOBS, sa.Column("dependency_key", sa.String(64)))
    op.add_column(EXECUTION_JOBS, sa.Column("dependency_manager", sa.String(16)))
    op.execute(
        sa.text(
            "DO $$ BEGIN IF EXISTS (SELECT 1 FROM execution_jobs "
            "WHERE purpose IN ('dependency','review')) THEN "
            "RAISE EXCEPTION 'legacy dependency jobs require explicit retirement'; "
            "END IF; END $$"
        )
    )
    op.create_check_constraint(
        "ck_execution_jobs_dependency_layer",
        EXECUTION_JOBS,
        "(purpose = 'raw' AND dependency_key IS NULL AND dependency_manager IS NULL) OR "
        "(purpose IN ('dependency','review') AND project_id IS NOT NULL "
        "AND version_id IS NOT NULL AND dependency_key ~ '^[0-9a-f]{64}$' "
        "AND dependency_manager IN ('npm','pnpm','yarn','uv','pip','cargo'))",
    )
    op.create_table(
        RUNTIMES,
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("runtime_id", sa.String(36), nullable=False),
        sa.Column("preview_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("conversation_id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("version_id", sa.String(36), nullable=False),
        sa.Column("scope_digest", sa.String(64), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("workspace_generation", sa.BigInteger(), nullable=False),
        sa.Column("request_lease_fence", sa.BigInteger(), nullable=False),
        sa.Column("adapter", sa.String(32), nullable=False),
        sa.Column("argv", sa.JSON(), nullable=False),
        sa.Column("cwd", sa.String(4096), nullable=False),
        sa.Column("readiness_path", sa.String(2048), nullable=False),
        sa.Column("startup_timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("dependency_key", sa.String(64), nullable=False),
        sa.Column("workspace_archive", sa.LargeBinary(), nullable=False),
        sa.Column("archive_sha256", sa.String(64), nullable=False),
        sa.Column("archive_byte_length", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("internal_url", sa.String(4096)),
        sa.Column("worker_id", sa.String(128)),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("lease_fence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_code", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "runtime_id", name="pk_runtime_leases"),
        sa.UniqueConstraint("runtime_id", name="uq_runtime_leases_runtime_id"),
        sa.UniqueConstraint("tenant_id", "preview_id", name="uq_runtime_leases_tenant_preview"),
        sa.CheckConstraint(
            "status IN ('queued','starting','running','stopping','stopped','failed','interrupted')",
            name="ck_runtime_leases_status",
        ),
        sa.CheckConstraint(
            "workspace_generation > 0 AND request_lease_fence > 0 AND lease_fence >= 0 "
            "AND attempts >= 0",
            name="ck_runtime_leases_fences",
        ),
        sa.CheckConstraint(
            "scope_digest ~ '^[0-9a-f]{64}$' AND "
            "request_fingerprint ~ '^[0-9a-f]{64}$' AND "
            "dependency_key ~ '^[0-9a-f]{64}$' AND archive_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_runtime_leases_hashes",
        ),
        sa.CheckConstraint(
            "archive_byte_length > 0 AND archive_byte_length <= 134217728 AND "
            "archive_byte_length = octet_length(workspace_archive)",
            name="ck_runtime_leases_archive_size",
        ),
        sa.CheckConstraint(
            "adapter IN ('vite','next','astro','python_asgi') AND cwd = '.' AND "
            "startup_timeout_seconds BETWEEN 1 AND 120",
            name="ck_runtime_leases_template",
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL AND lease_fence > 0)",
            name="ck_runtime_leases_worker_lease",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND internal_url IS NOT NULL AND worker_id IS NOT NULL) OR "
            "(status <> 'running' AND internal_url IS NULL)",
            name="ck_runtime_leases_internal_endpoint",
        ),
        sa.CheckConstraint(
            "(status IN ('failed','interrupted') AND error_code IS NOT NULL) OR "
            "(status NOT IN ('failed','interrupted') AND error_code IS NULL)",
            name="ck_runtime_leases_error",
        ),
    )
    op.create_index(
        "ix_runtime_leases_claim",
        RUNTIMES,
        ["status", "lease_expires_at", "created_at"],
        postgresql_where=sa.text("status IN ('queued','starting','running','stopping')"),
    )
    op.create_index(
        "ix_runtime_leases_tenant_task",
        RUNTIMES,
        ["tenant_id", "task_id", "created_at"],
    )
    op.create_table(
        WORKERS,
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("executor", sa.String(128), nullable=False),
        sa.Column("executor_version", sa.String(64), nullable=False),
        sa.Column("attestation_digest", sa.String(64), nullable=False),
        sa.Column("gateway_base_url", sa.String(4096), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("owner_id", name="pk_runtime_workers"),
        sa.CheckConstraint(
            "executor = 'cloud_oci_runtime' AND executor_version = '1.0.0' AND "
            "attestation_digest ~ '^[0-9a-f]{64}$'",
            name="ck_runtime_workers_attestation",
        ),
    )
    op.create_table(
        ROUTES,
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("runtime_id", sa.String(36), nullable=False),
        sa.Column("preview_id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("version_id", sa.String(36), nullable=False),
        sa.Column("request_lease_fence", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("token_hash", name="pk_runtime_routes"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "runtime_id"],
            ["runtime_leases.tenant_id", "runtime_leases.runtime_id"],
            name="fk_runtime_routes_runtime",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$' AND request_lease_fence > 0",
            name="ck_runtime_routes_binding",
        ),
    )
    op.create_index("ix_runtime_routes_expiry", ROUTES, ["expires_at"])
    _enable_rls(RUNTIMES)


def downgrade() -> None:
    policy = f"tenant_isolation_{RUNTIMES}"
    op.execute(sa.text(f'DROP POLICY IF EXISTS "{policy}" ON "{RUNTIMES}"'))
    op.execute(sa.text(f'ALTER TABLE "{RUNTIMES}" DISABLE ROW LEVEL SECURITY'))
    op.drop_index("ix_runtime_routes_expiry", table_name=ROUTES)
    op.drop_table(ROUTES)
    op.drop_table(WORKERS)
    op.drop_index("ix_runtime_leases_tenant_task", table_name=RUNTIMES)
    op.drop_index("ix_runtime_leases_claim", table_name=RUNTIMES)
    op.drop_table(RUNTIMES)
    op.drop_constraint(
        "ck_execution_jobs_dependency_layer",
        EXECUTION_JOBS,
        type_="check",
    )
    op.drop_column(EXECUTION_JOBS, "dependency_manager")
    op.drop_column(EXECUTION_JOBS, "dependency_key")
    op.drop_column("core_checkpoints", "evidence_artifact_ids")


def _enable_rls(table_name: str) -> None:
    predicate = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')"
    policy = f"tenant_isolation_{table_name}"
    op.execute(sa.text(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY'))
    op.execute(sa.text(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY'))
    op.execute(
        sa.text(
            f'CREATE POLICY "{policy}" ON "{table_name}" '
            f"USING ({predicate}) WITH CHECK ({predicate})"
        )
    )
