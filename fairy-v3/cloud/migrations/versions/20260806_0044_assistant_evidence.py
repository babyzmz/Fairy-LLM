"""Add durable assistant evidence receipts.

Revision ID: 20260806_0044
Revises: 20260724_0043
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260806_0044"
down_revision: str | Sequence[str] | None = "20260724_0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "core_assistant_turns",
        sa.Column(
            "cited_evidence_receipt_ids",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )
    op.add_column(
        "core_assistant_tool_invocations",
        sa.Column(
            "evidence_receipts",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )
    op.alter_column(
        "core_assistant_turns",
        "cited_evidence_receipt_ids",
        server_default=None,
    )
    op.alter_column(
        "core_assistant_tool_invocations",
        "evidence_receipts",
        server_default=None,
    )
    op.drop_constraint("ck_execution_jobs_purpose", "execution_jobs", type_="check")
    op.drop_constraint("ck_execution_jobs_dependency_layer", "execution_jobs", type_="check")
    op.drop_constraint("ck_execution_jobs_result_evidence", "execution_jobs", type_="check")
    op.create_check_constraint(
        "ck_execution_jobs_purpose",
        "execution_jobs",
        "purpose IN ('raw','inspect','dependency','review')",
    )
    op.create_check_constraint(
        "ck_execution_jobs_dependency_layer",
        "execution_jobs",
        "(purpose IN ('raw','inspect') AND dependency_key IS NULL "
        "AND dependency_manager IS NULL) OR "
        "(purpose IN ('dependency','review') AND project_id IS NOT NULL "
        "AND version_id IS NOT NULL AND dependency_key ~ '^[0-9a-f]{64}$' "
        "AND dependency_manager IN ('npm','pnpm','yarn','uv','pip','cargo'))",
    )
    op.create_check_constraint(
        "ck_execution_jobs_result_evidence",
        "execution_jobs",
        "(result_status IS NULL AND executor IS NULL AND executor_version IS NULL "
        "AND exit_code IS NULL AND stdout IS NULL AND stderr IS NULL "
        "AND stdout_sha256 IS NULL AND stderr_sha256 IS NULL "
        "AND output_truncated IS NULL AND started_at IS NULL "
        "AND result_recorded_at IS NULL) OR "
        "(result_status IN ('completed','failed','timed_out','cancelled') "
        "AND executor = 'cloud_oci_worker' AND executor_version = '1.1.0' "
        "AND stdout IS NOT NULL AND stderr IS NOT NULL "
        "AND stdout_sha256 ~ '^[0-9a-f]{64}$' "
        "AND stderr_sha256 ~ '^[0-9a-f]{64}$' "
        "AND output_truncated IS NOT NULL AND started_at IS NOT NULL "
        "AND result_recorded_at IS NOT NULL AND finished_at IS NOT NULL "
        "AND octet_length(stdout) + octet_length(stderr) <= output_limit_bytes)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_execution_jobs_result_evidence", "execution_jobs", type_="check")
    op.drop_constraint("ck_execution_jobs_dependency_layer", "execution_jobs", type_="check")
    op.drop_constraint("ck_execution_jobs_purpose", "execution_jobs", type_="check")
    op.create_check_constraint(
        "ck_execution_jobs_purpose",
        "execution_jobs",
        "purpose IN ('raw','dependency','review')",
    )
    op.create_check_constraint(
        "ck_execution_jobs_dependency_layer",
        "execution_jobs",
        "(purpose = 'raw' AND dependency_key IS NULL AND dependency_manager IS NULL) OR "
        "(purpose IN ('dependency','review') AND project_id IS NOT NULL "
        "AND version_id IS NOT NULL AND dependency_key ~ '^[0-9a-f]{64}$' "
        "AND dependency_manager IN ('npm','pnpm','yarn','uv','pip','cargo'))",
    )
    op.create_check_constraint(
        "ck_execution_jobs_result_evidence",
        "execution_jobs",
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
    )
    op.drop_column("core_assistant_tool_invocations", "evidence_receipts")
    op.drop_column("core_assistant_turns", "cited_evidence_receipt_ids")
