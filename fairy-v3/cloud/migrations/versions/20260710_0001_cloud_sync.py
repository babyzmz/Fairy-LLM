"""Create the Fairy cloud sync ledger and brokerless queue."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260710_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cloud_projects",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("revision", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("active_version_id", sa.String(length=36)),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("project_id"),
    )
    op.create_index("ix_cloud_projects_user_id", "cloud_projects", ["user_id"])

    op.create_table(
        "domain_events",
        sa.Column("cursor", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("device_id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=36)),
        sa.Column("conversation_id", sa.String(length=36)),
        sa.Column("task_id", sa.String(length=36)),
        sa.Column("version_id", sa.String(length=36)),
        sa.Column("task_sequence", sa.BigInteger()),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("visibility", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("cursor"),
        sa.UniqueConstraint("event_id"),
    )
    op.create_index("ix_domain_events_project_id", "domain_events", ["project_id"])
    op.create_index(
        "ix_domain_events_task_sequence",
        "domain_events",
        ["task_id", "task_sequence"],
    )
    op.create_index("ix_domain_events_user_cursor", "domain_events", ["user_id", "cursor"])

    op.create_table(
        "outbox",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("topic", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_owner", sa.String(length=128)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id"),
    )
    op.create_index(
        "ix_outbox_claim",
        "outbox",
        ["published_at", "available_at", "lease_expires_at"],
    )

    op.create_table(
        "version_candidates",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("version_id", sa.String(length=36), nullable=False),
        sa.Column("base_revision", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(length=32), server_default="candidate", nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "version_id", name="uq_version_candidate"),
    )
    op.create_index(
        "ix_version_candidates_project_state",
        "version_candidates",
        ["project_id", "state"],
    )

    op.create_table(
        "worker_leases",
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=128), nullable=False),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("fence", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        sa.PrimaryKeyConstraint("resource_type", "resource_id"),
    )


def downgrade() -> None:
    op.drop_table("worker_leases")
    op.drop_index("ix_version_candidates_project_state", table_name="version_candidates")
    op.drop_table("version_candidates")
    op.drop_index("ix_outbox_claim", table_name="outbox")
    op.drop_table("outbox")
    op.drop_index("ix_domain_events_user_cursor", table_name="domain_events")
    op.drop_index("ix_domain_events_task_sequence", table_name="domain_events")
    op.drop_index("ix_domain_events_project_id", table_name="domain_events")
    op.drop_table("domain_events")
    op.drop_index("ix_cloud_projects_user_id", table_name="cloud_projects")
    op.drop_table("cloud_projects")
