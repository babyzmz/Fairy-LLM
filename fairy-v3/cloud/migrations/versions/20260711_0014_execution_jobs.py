"""Add fenced brokerless execution jobs and worker health."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260711_0014"
down_revision: str | Sequence[str] | None = "20260711_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOBS = "execution_jobs"
WORKERS = "execution_workers"


def upgrade() -> None:
    op.create_table(
        JOBS,
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("job_id", sa.String(36), nullable=False),
        sa.Column("command_run_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=True),
        sa.Column("conversation_id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("version_id", sa.String(36), nullable=True),
        sa.Column("scope_digest", sa.String(64), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("workspace_generation", sa.BigInteger(), nullable=False),
        sa.Column("request_lease_fence", sa.BigInteger(), nullable=False),
        sa.Column("argv", sa.JSON(), nullable=False),
        sa.Column("cwd", sa.String(4096), nullable=False),
        sa.Column("environment", sa.JSON(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("output_limit_bytes", sa.Integer(), nullable=False),
        sa.Column("network_policy", sa.String(16), nullable=False),
        sa.Column("workspace_archive", sa.LargeBinary(), nullable=False),
        sa.Column("archive_sha256", sa.String(64), nullable=False),
        sa.Column("archive_byte_length", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_fence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("spawned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_status", sa.String(32), nullable=True),
        sa.Column("executor", sa.String(128), nullable=True),
        sa.Column("executor_version", sa.String(64), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("stdout", sa.LargeBinary(), nullable=True),
        sa.Column("stderr", sa.LargeBinary(), nullable=True),
        sa.Column("stdout_sha256", sa.String(64), nullable=True),
        sa.Column("stderr_sha256", sa.String(64), nullable=True),
        sa.Column("output_truncated", sa.Boolean(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_recorded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "job_id", name="pk_execution_jobs"),
        sa.UniqueConstraint(
            "tenant_id",
            "command_run_id",
            name="uq_execution_jobs_tenant_command_run",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "command_run_id"],
            ["command_runs.tenant_id", "command_runs.id"],
            name="fk_execution_jobs_command_run",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('queued','claimed','running','result_recorded','succeeded',"
            "'failed','timed_out','cancelled','interrupted')",
            name="ck_execution_jobs_status",
        ),
        sa.CheckConstraint(
            "(project_id IS NULL AND version_id IS NULL) OR "
            "(project_id IS NOT NULL AND version_id IS NOT NULL)",
            name="ck_execution_jobs_project_version",
        ),
        sa.CheckConstraint(
            "workspace_generation > 0 AND request_lease_fence > 0 AND lease_fence >= 0",
            name="ck_execution_jobs_fences",
        ),
        sa.CheckConstraint(
            "job_id = command_run_id",
            name="ck_execution_jobs_command_identity",
        ),
        sa.CheckConstraint(
            "timeout_seconds BETWEEN 1 AND 900 AND output_limit_bytes BETWEEN 1024 AND 1048576",
            name="ck_execution_jobs_resources",
        ),
        sa.CheckConstraint(
            "archive_byte_length > 0 AND archive_byte_length <= 134217728 AND "
            "archive_byte_length = octet_length(workspace_archive)",
            name="ck_execution_jobs_archive_size",
        ),
        sa.CheckConstraint(
            "scope_digest ~ '^[0-9a-f]{64}$' AND "
            "request_fingerprint ~ '^[0-9a-f]{64}$' AND "
            "archive_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_execution_jobs_hashes",
        ),
        sa.CheckConstraint(
            "network_policy = 'none'",
            name="ck_execution_jobs_network_policy",
        ),
        sa.CheckConstraint(
            "(status IN ('claimed','running','result_recorded')) = "
            "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND lease_fence > 0)",
            name="ck_execution_jobs_active_lease",
        ),
        sa.CheckConstraint(
            "(result_status IS NULL AND executor IS NULL AND executor_version IS NULL "
            "AND exit_code IS NULL AND stdout IS NULL AND stderr IS NULL "
            "AND stdout_sha256 IS NULL AND stderr_sha256 IS NULL "
            "AND output_truncated IS NULL AND started_at IS NULL "
            "AND result_recorded_at IS NULL) OR "
            "(result_status IN ('completed','failed','timed_out','cancelled') "
            "AND executor = 'cloud_oci_worker' AND executor_version = '1.0.0' "
            "AND stdout IS NOT NULL AND stderr IS NOT NULL "
            "AND stdout_sha256 ~ '^[0-9a-f]{64}$' "
            "AND stderr_sha256 ~ '^[0-9a-f]{64}$' "
            "AND output_truncated IS NOT NULL AND started_at IS NOT NULL "
            "AND result_recorded_at IS NOT NULL AND finished_at IS NOT NULL "
            "AND octet_length(stdout) + octet_length(stderr) <= output_limit_bytes)",
            name="ck_execution_jobs_result_evidence",
        ),
        sa.CheckConstraint(
            "(status IN ('queued','claimed','running','interrupted') "
            "AND result_status IS NULL) OR "
            "(status = 'result_recorded' AND result_status IS NOT NULL) OR "
            "(status = 'succeeded' AND result_status = 'completed') OR "
            "(status = 'failed' AND result_status = 'failed') OR "
            "(status = 'timed_out' AND result_status = 'timed_out') OR "
            "(status = 'cancelled' AND "
            "(result_status IS NULL OR result_status = 'cancelled'))",
            name="ck_execution_jobs_result_status",
        ),
    )
    op.create_index(
        "ix_execution_jobs_claim",
        JOBS,
        ["status", "lease_expires_at", "created_at"],
        postgresql_where=sa.text("status IN ('queued','claimed','result_recorded')"),
    )
    op.create_index(
        "ix_execution_jobs_tenant_task",
        JOBS,
        ["tenant_id", "task_id", "created_at"],
    )
    op.create_table(
        WORKERS,
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("executor", sa.String(128), nullable=False),
        sa.Column("executor_version", sa.String(64), nullable=False),
        sa.Column("attestation_digest", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("owner_id", name="pk_execution_workers"),
        sa.CheckConstraint(
            "attestation_digest ~ '^[0-9a-f]{64}$'",
            name="ck_execution_workers_attestation_digest",
        ),
    )
    _enable_rls(JOBS)


def downgrade() -> None:
    policy_name = f"tenant_isolation_{JOBS}"
    op.execute(sa.text(f'DROP POLICY IF EXISTS "{policy_name}" ON "{JOBS}"'))
    op.execute(sa.text(f'ALTER TABLE "{JOBS}" DISABLE ROW LEVEL SECURITY'))
    op.drop_table(WORKERS)
    op.drop_table(JOBS)


def _enable_rls(table_name: str) -> None:
    predicate = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')"
    policy_name = f"tenant_isolation_{table_name}"
    op.execute(sa.text(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY'))
    op.execute(sa.text(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY'))
    op.execute(
        sa.text(
            f'CREATE POLICY "{policy_name}" ON "{table_name}" '
            f"USING ({predicate}) WITH CHECK ({predicate})"
        )
    )
