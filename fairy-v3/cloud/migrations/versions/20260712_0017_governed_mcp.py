"""Add tenant-scoped governed MCP trust configuration."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260712_0017"
down_revision: str | Sequence[str] | None = "20260712_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SERVERS = "core_mcp_servers"
UPDATES = "core_mcp_server_updates"


def upgrade() -> None:
    op.create_table(
        SERVERS,
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("server_id", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("transport", sa.String(32), nullable=False),
        sa.Column("command", sa.Text()),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("endpoint", sa.Text()),
        sa.Column("credential_ref", sa.String(288)),
        sa.Column("environment_refs", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("accepted_schema_digest", sa.String(64)),
        sa.Column("pending_schema_digest", sa.String(64)),
        sa.Column("accepted_tools", sa.JSON(), nullable=False),
        sa.Column("pending_tools", sa.JSON(), nullable=False),
        sa.Column("policies", sa.JSON(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("last_error_code", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "server_id", name="pk_core_mcp_servers"),
        sa.CheckConstraint(
            "transport IN ('stdio', 'streamable_http')",
            name="ck_core_mcp_servers_transport",
        ),
        sa.CheckConstraint(
            "status IN ('disabled', 'untrusted', 'review_required', 'ready', 'unavailable')",
            name="ck_core_mcp_servers_status",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_core_mcp_servers_revision"),
        sa.CheckConstraint(
            "(transport = 'stdio' AND command IS NOT NULL AND endpoint IS NULL) OR "
            "(transport = 'streamable_http' AND command IS NULL AND endpoint IS NOT NULL)",
            name="ck_core_mcp_servers_connection",
        ),
    )
    op.create_table(
        UPDATES,
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("server_id", sa.String(64), nullable=False),
        sa.Column("result_record", sa.JSON()),
        sa.Column("result_deleted", sa.Boolean()),
        sa.Column("result_error_code", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "idempotency_key",
            name="pk_core_mcp_server_updates",
        ),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_core_mcp_server_updates_fingerprint",
        ),
        sa.CheckConstraint(
            "(result_record IS NULL AND result_deleted IS NULL AND result_error_code IS NULL) OR "
            "(result_record IS NOT NULL AND result_deleted = false "
            "AND result_error_code IS NULL) OR "
            "(result_record IS NULL AND result_deleted = true "
            "AND result_error_code IS NULL) OR "
            "(result_record IS NULL AND result_deleted = false "
            "AND result_error_code IS NOT NULL)",
            name="ck_core_mcp_server_updates_result",
        ),
    )
    _enable_rls(SERVERS)
    _enable_rls(UPDATES)


def downgrade() -> None:
    _disable_rls(UPDATES)
    _disable_rls(SERVERS)
    op.drop_table(UPDATES)
    op.drop_table(SERVERS)


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


def _disable_rls(table_name: str) -> None:
    policy = f"tenant_isolation_{table_name}"
    op.execute(sa.text(f'DROP POLICY IF EXISTS "{policy}" ON "{table_name}"'))
    op.execute(sa.text(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY'))
