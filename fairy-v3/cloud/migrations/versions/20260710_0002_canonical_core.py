"""Establish the canonical tenant-scoped Core, ledger, and outbox schema."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import context, op

revision: str = "20260710_0002"
down_revision: str | Sequence[str] | None = "20260710_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_LENGTH = 128
ID_LENGTH = 36
_RLS_TABLES = (
    "core_tenants",
    "core_projects",
    "core_conversations",
    "core_versions",
    "core_tasks",
    "core_changesets",
    "core_approvals",
    "core_checkpoints",
    "command_runs",
    "task_event_sequences",
    "domain_events",
    "outbox",
    "version_candidates",
    "worker_leases",
)


def upgrade() -> None:
    _create_canonical_tables()
    _add_tenant_columns_to_existing_tables()
    if context.is_offline_mode():
        _emit_offline_backfill()
    else:
        _backfill_existing_rows()
    _finalize_existing_tables()
    _verify_and_drop_cloud_projects()
    _enable_rls()


def _create_canonical_tables() -> None:
    op.create_table(
        "core_tenants",
        sa.Column("tenant_id", sa.String(TENANT_LENGTH), nullable=False),
        sa.Column("subject_id", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("tenant_id", name="pk_core_tenants"),
        sa.UniqueConstraint("subject_id", name="uq_core_tenants_subject"),
    )
    op.create_table(
        "core_projects",
        _tenant_column(),
        _id_column(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("residency", sa.String(32), nullable=False),
        sa.Column("active_version_id", sa.String(ID_LENGTH)),
        sa.Column("active_preview_id", sa.String(ID_LENGTH)),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        _created_at_column(),
        _updated_at_column(),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_projects"),
    )
    op.create_index(
        "ix_core_projects_tenant_updated",
        "core_projects",
        ["tenant_id", "updated_at"],
    )
    op.create_table(
        "core_conversations",
        _tenant_column(),
        _id_column(),
        sa.Column("project_id", sa.String(ID_LENGTH)),
        sa.Column("workspace_type", sa.String(32), nullable=False),
        sa.Column("base_version_id", sa.String(ID_LENGTH)),
        sa.Column("active_draft_version_id", sa.String(ID_LENGTH)),
        sa.Column("active_task_id", sa.String(ID_LENGTH)),
        sa.Column("active_preview_id", sa.String(ID_LENGTH)),
        _created_at_column(),
        _updated_at_column(),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_conversations"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["core_projects.tenant_id", "core_projects.id"],
            name="fk_core_conversations_project",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_core_conversations_tenant_project",
        "core_conversations",
        ["tenant_id", "project_id"],
    )
    op.create_table(
        "core_versions",
        _tenant_column(),
        _id_column(),
        sa.Column("project_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("source_conversation_id", sa.String(ID_LENGTH)),
        sa.Column("source_task_id", sa.String(ID_LENGTH)),
        sa.Column("parent_version_id", sa.String(ID_LENGTH)),
        sa.Column("project_root", sa.String(4096), nullable=False),
        sa.Column("visibility", sa.String(32), nullable=False),
        _created_at_column(),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_versions"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["core_projects.tenant_id", "core_projects.id"],
            name="fk_core_versions_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_conversation_id"],
            ["core_conversations.tenant_id", "core_conversations.id"],
            name="fk_core_versions_source_conversation",
        ),
    )
    op.create_index(
        "ix_core_versions_tenant_project",
        "core_versions",
        ["tenant_id", "project_id"],
    )
    op.create_table(
        "core_tasks",
        _tenant_column(),
        _id_column(),
        sa.Column("project_id", sa.String(ID_LENGTH)),
        sa.Column("conversation_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("user_request", sa.String(), nullable=False),
        sa.Column("operation_mode", sa.String(64), nullable=False),
        sa.Column("base_version_id", sa.String(ID_LENGTH)),
        sa.Column("target_version_id", sa.String(ID_LENGTH)),
        sa.Column("execution_target", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        _created_at_column(),
        _updated_at_column(),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_tasks"),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_core_tasks_tenant_idempotency",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["core_projects.tenant_id", "core_projects.id"],
            name="fk_core_tasks_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["core_conversations.tenant_id", "core_conversations.id"],
            name="fk_core_tasks_conversation",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_core_tasks_tenant_status",
        "core_tasks",
        ["tenant_id", "status", "created_at"],
    )
    op.create_table(
        "command_runs",
        _tenant_column(),
        _id_column(),
        sa.Column("command_name", sa.String(128), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("scope", sa.JSON(), nullable=False),
        sa.Column("scope_digest", sa.String(64), nullable=False),
        sa.Column("project_id", sa.String(ID_LENGTH)),
        sa.Column("conversation_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("task_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("risk_level", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("lease_fence", sa.BigInteger(), server_default="0", nullable=False),
        _created_at_column(),
        _updated_at_column(),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_command_runs"),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_command_runs_tenant_idempotency",
        ),
    )
    op.create_index(
        "ix_command_runs_tenant_status",
        "command_runs",
        ["tenant_id", "status", "created_at"],
    )
    op.create_table(
        "core_changesets",
        _tenant_column(),
        _id_column(),
        sa.Column("project_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("conversation_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("task_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("version_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("files", sa.JSON(), nullable=False),
        sa.Column("patches", sa.JSON(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("risk_level", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("approval_decision", sa.String(32), nullable=False),
        _created_at_column(),
        _updated_at_column(),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_changesets"),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_core_changesets_tenant_idempotency",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["core_projects.tenant_id", "core_projects.id"],
            name="fk_core_changesets_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["core_conversations.tenant_id", "core_conversations.id"],
            name="fk_core_changesets_conversation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["core_tasks.tenant_id", "core_tasks.id"],
            name="fk_core_changesets_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["core_versions.tenant_id", "core_versions.id"],
            name="fk_core_changesets_version",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_core_changesets_tenant_task",
        "core_changesets",
        ["tenant_id", "task_id"],
    )
    op.create_table(
        "core_approvals",
        _tenant_column(),
        _id_column(),
        sa.Column("task_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("command_run_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("changeset_id", sa.String(ID_LENGTH)),
        sa.Column("requested_by", sa.String(128), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("decided_by", sa.String(128)),
        _created_at_column(),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_approvals"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["core_tasks.tenant_id", "core_tasks.id"],
            name="fk_core_approvals_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "changeset_id"],
            ["core_changesets.tenant_id", "core_changesets.id"],
            name="fk_core_approvals_changeset",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_core_approvals_tenant_task",
        "core_approvals",
        ["tenant_id", "task_id"],
    )
    op.create_table(
        "core_checkpoints",
        _tenant_column(),
        _id_column(),
        sa.Column("task_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("version_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("changed_files", sa.JSON(), nullable=False),
        sa.Column("command_run_ids", sa.JSON(), nullable=False),
        sa.Column("preview_artifact_id", sa.String(ID_LENGTH)),
        _created_at_column(),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_core_checkpoints"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["core_tasks.tenant_id", "core_tasks.id"],
            name="fk_core_checkpoints_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["core_versions.tenant_id", "core_versions.id"],
            name="fk_core_checkpoints_version",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_core_checkpoints_tenant_task",
        "core_checkpoints",
        ["tenant_id", "task_id"],
    )
    op.create_table(
        "task_event_sequences",
        _tenant_column(),
        sa.Column("task_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "task_id",
            name="pk_task_event_sequences",
        ),
    )


def _add_tenant_columns_to_existing_tables() -> None:
    op.add_column("domain_events", sa.Column("tenant_id", sa.String(TENANT_LENGTH)))
    op.add_column("domain_events", sa.Column("run_id", sa.String(ID_LENGTH)))
    op.add_column("domain_events", sa.Column("message", sa.String()))
    op.add_column("outbox", sa.Column("tenant_id", sa.String(TENANT_LENGTH)))
    op.add_column(
        "outbox",
        sa.Column("lease_fence", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.add_column("version_candidates", sa.Column("tenant_id", sa.String(TENANT_LENGTH)))
    op.add_column("worker_leases", sa.Column("tenant_id", sa.String(TENANT_LENGTH)))


def _backfill_existing_rows() -> None:
    connection = op.get_bind()
    user_ids = {
        str(row.user_id)
        for row in connection.execute(
            sa.text(
                """
                SELECT user_id FROM cloud_projects
                UNION
                SELECT user_id FROM domain_events
                """
            )
        )
    }
    now = datetime.now(UTC)
    for user_id in sorted(user_ids):
        tenant_id = _tenant_id(user_id)
        connection.execute(
            sa.text(
                """
                INSERT INTO core_tenants (tenant_id, subject_id, created_at)
                VALUES (:tenant_id, :subject_id, :created_at)
                ON CONFLICT (tenant_id) DO NOTHING
                """
            ),
            {"tenant_id": tenant_id, "subject_id": user_id, "created_at": now},
        )
        connection.execute(
            sa.text(
                """
                UPDATE domain_events
                SET tenant_id = :tenant_id
                WHERE user_id = :subject_id
                """
            ),
            {"tenant_id": tenant_id, "subject_id": user_id},
        )
    projects = connection.execute(
        sa.text(
            """
            SELECT project_id, user_id, revision, active_version_id, updated_at
            FROM cloud_projects
            ORDER BY project_id
            """
        )
    ).mappings()
    for project in projects:
        connection.execute(
            sa.text(
                """
                INSERT INTO core_projects (
                    tenant_id, id, name, residency, active_version_id,
                    active_preview_id, revision, created_at, updated_at
                ) VALUES (
                    :tenant_id, :id, :name, 'synced', :active_version_id,
                    NULL, :revision, :updated_at, :updated_at
                )
                ON CONFLICT (tenant_id, id) DO NOTHING
                """
            ),
            {
                "tenant_id": _tenant_id(str(project["user_id"])),
                "id": str(project["project_id"]),
                "name": f"Migrated {project['project_id']}",
                "active_version_id": project["active_version_id"],
                "revision": int(project["revision"]),
                "updated_at": project["updated_at"],
            },
        )
    connection.execute(
        sa.text(
            """
            UPDATE outbox AS target
            SET tenant_id = event.tenant_id
            FROM domain_events AS event
            WHERE target.event_id = event.event_id
            """
        )
    )
    connection.execute(
        sa.text(
            """
            UPDATE version_candidates AS candidate
            SET tenant_id = project.tenant_id
            FROM core_projects AS project
            WHERE candidate.project_id = project.id
            """
        )
    )
    tenant_rows = (
        connection.execute(sa.text("SELECT tenant_id FROM core_tenants ORDER BY tenant_id"))
        .scalars()
        .all()
    )
    lease_count = int(
        connection.execute(sa.text("SELECT count(*) FROM worker_leases")).scalar_one()
    )
    if lease_count:
        if len(tenant_rows) != 1:
            raise RuntimeError("cannot assign pre-tenant worker leases to multiple tenants")
        connection.execute(
            sa.text("UPDATE worker_leases SET tenant_id = :tenant_id"),
            {"tenant_id": tenant_rows[0]},
        )
    _verify_backfill(connection)


def _emit_offline_backfill() -> None:
    tenant_expression = "encode(sha256(convert_to('fairy:v3:tenant:' || user_id, 'UTF8')), 'hex')"
    op.execute(
        sa.text(
            f"""
            INSERT INTO core_tenants (tenant_id, subject_id)
            SELECT DISTINCT {tenant_expression}, user_id
            FROM (
                SELECT user_id FROM cloud_projects
                UNION
                SELECT user_id FROM domain_events
            ) AS subjects
            ON CONFLICT (tenant_id) DO NOTHING
            """
        )
    )
    op.execute(sa.text(f"UPDATE domain_events SET tenant_id = {tenant_expression}"))
    op.execute(
        sa.text(
            """
            INSERT INTO core_projects (
                tenant_id, id, name, residency, active_version_id,
                active_preview_id, revision, created_at, updated_at
            )
            SELECT tenant.tenant_id, project.project_id,
                   'Migrated ' || project.project_id, 'synced',
                   project.active_version_id, NULL, project.revision,
                   project.updated_at, project.updated_at
            FROM cloud_projects AS project
            JOIN core_tenants AS tenant ON tenant.subject_id = project.user_id
            ON CONFLICT (tenant_id, id) DO NOTHING
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE outbox AS target SET tenant_id = event.tenant_id
            FROM domain_events AS event WHERE target.event_id = event.event_id
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE version_candidates AS candidate SET tenant_id = project.tenant_id
            FROM core_projects AS project WHERE candidate.project_id = project.id
            """
        )
    )
    op.execute(
        sa.text(
            """
            DO $$
            DECLARE tenant_count integer; lease_count integer; only_tenant text;
            BEGIN
                SELECT count(*), min(tenant_id) INTO tenant_count, only_tenant
                FROM core_tenants;
                SELECT count(*) INTO lease_count FROM worker_leases;
                IF lease_count > 0 AND tenant_count <> 1 THEN
                    RAISE EXCEPTION 'cannot assign pre-tenant worker leases';
                END IF;
                IF lease_count > 0 THEN
                    UPDATE worker_leases SET tenant_id = only_tenant;
                END IF;
            END $$
            """
        )
    )
    op.execute(sa.text(_BACKFILL_ASSERTIONS))


def _verify_backfill(connection: sa.Connection) -> None:
    failures = connection.execute(sa.text(_BACKFILL_FAILURE_QUERY)).mappings().one()
    failed = [name for name, count in failures.items() if int(count)]
    if failed:
        raise RuntimeError(f"canonical backfill failed: {', '.join(failed)}")


def _finalize_existing_tables() -> None:
    op.execute(
        sa.text(
            """
            UPDATE domain_events
            SET run_id = COALESCE(run_id, NULLIF(payload ->> 'run_id', '')),
                message = COALESCE(
                    message,
                    NULLIF(payload ->> 'message', ''),
                    event_type
                )
            """
        )
    )
    op.execute(sa.text(_OUTBOX_PAYLOAD_BACKFILL))
    op.alter_column("domain_events", "tenant_id", nullable=False)
    op.alter_column("domain_events", "message", nullable=False)
    op.drop_index("ix_domain_events_project_id", table_name="domain_events")
    op.drop_index("ix_domain_events_task_sequence", table_name="domain_events")
    op.drop_index("ix_domain_events_user_cursor", table_name="domain_events")
    op.drop_constraint("domain_events_event_id_key", "domain_events", type_="unique")
    op.drop_constraint("domain_events_pkey", "domain_events", type_="primary")
    op.create_primary_key("pk_domain_events", "domain_events", ["cursor"])
    op.create_unique_constraint(
        "uq_domain_events_tenant_event",
        "domain_events",
        ["tenant_id", "event_id"],
    )
    op.create_unique_constraint(
        "uq_domain_events_tenant_task_sequence",
        "domain_events",
        ["tenant_id", "task_id", "task_sequence"],
    )
    op.create_index(
        "ix_domain_events_tenant_cursor",
        "domain_events",
        ["tenant_id", "cursor"],
    )
    op.create_index(
        "ix_domain_events_tenant_project",
        "domain_events",
        ["tenant_id", "project_id"],
    )

    op.alter_column("outbox", "tenant_id", nullable=False)
    op.drop_index("ix_outbox_claim", table_name="outbox")
    op.drop_constraint("outbox_event_id_key", "outbox", type_="unique")
    op.drop_constraint("outbox_pkey", "outbox", type_="primary")
    op.create_primary_key("pk_outbox", "outbox", ["tenant_id", "id"])
    op.create_unique_constraint(
        "uq_outbox_tenant_event",
        "outbox",
        ["tenant_id", "event_id"],
    )
    op.create_foreign_key(
        "fk_outbox_event",
        "outbox",
        "domain_events",
        ["tenant_id", "event_id"],
        ["tenant_id", "event_id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_outbox_claim_global",
        "outbox",
        ["published_at", "available_at", "lease_expires_at", "id"],
        postgresql_where=sa.text("published_at IS NULL"),
    )

    op.alter_column("version_candidates", "tenant_id", nullable=False)
    op.drop_index("ix_version_candidates_project_state", table_name="version_candidates")
    op.drop_constraint("uq_version_candidate", "version_candidates", type_="unique")
    op.drop_constraint("version_candidates_pkey", "version_candidates", type_="primary")
    op.create_primary_key(
        "pk_version_candidates",
        "version_candidates",
        ["tenant_id", "id"],
    )
    op.create_unique_constraint(
        "uq_version_candidate_tenant_project_version",
        "version_candidates",
        ["tenant_id", "project_id", "version_id"],
    )
    op.create_foreign_key(
        "fk_version_candidates_project",
        "version_candidates",
        "core_projects",
        ["tenant_id", "project_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_version_candidates_tenant_project_state",
        "version_candidates",
        ["tenant_id", "project_id", "state"],
    )

    op.alter_column("worker_leases", "tenant_id", nullable=False)
    op.drop_constraint("worker_leases_pkey", "worker_leases", type_="primary")
    op.create_primary_key(
        "pk_worker_leases",
        "worker_leases",
        ["tenant_id", "resource_type", "resource_id"],
    )


def _enable_rls() -> None:
    for table_name in _RLS_TABLES:
        policy_name = f"tenant_isolation_{table_name}"
        predicate = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')"
        op.execute(sa.text(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY'))
        op.execute(sa.text(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY'))
        op.execute(
            sa.text(
                f'CREATE POLICY "{policy_name}" ON "{table_name}" '
                f"USING ({predicate}) WITH CHECK ({predicate})"
            )
        )


def _verify_and_drop_cloud_projects() -> None:
    if context.is_offline_mode():
        op.execute(sa.text(_BACKFILL_ASSERTIONS))
    else:
        _verify_backfill(op.get_bind())
    op.drop_index("ix_cloud_projects_user_id", table_name="cloud_projects")
    op.drop_table("cloud_projects")


def downgrade() -> None:
    for table_name in reversed(_RLS_TABLES):
        policy_name = f"tenant_isolation_{table_name}"
        op.execute(sa.text(f'DROP POLICY IF EXISTS "{policy_name}" ON "{table_name}"'))
        op.execute(sa.text(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY'))
    op.execute(sa.text(_DOWNGRADE_ASSERTIONS))

    op.create_table(
        "cloud_projects",
        sa.Column("project_id", sa.String(ID_LENGTH), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("revision", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("active_version_id", sa.String(ID_LENGTH)),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("project_id"),
    )
    op.execute(
        sa.text(
            """
            INSERT INTO cloud_projects (
                project_id, user_id, revision, active_version_id, updated_at
            )
            SELECT project.id, tenant.subject_id, project.revision,
                   project.active_version_id, project.updated_at
            FROM core_projects AS project
            JOIN core_tenants AS tenant USING (tenant_id)
            """
        )
    )
    op.create_index("ix_cloud_projects_user_id", "cloud_projects", ["user_id"])

    op.drop_constraint("fk_outbox_event", "outbox", type_="foreignkey")
    op.drop_index("ix_outbox_claim_global", table_name="outbox")
    op.drop_constraint("uq_outbox_tenant_event", "outbox", type_="unique")
    op.drop_constraint("pk_outbox", "outbox", type_="primary")
    op.create_primary_key("outbox_pkey", "outbox", ["id"])
    op.create_unique_constraint("outbox_event_id_key", "outbox", ["event_id"])
    op.create_index(
        "ix_outbox_claim",
        "outbox",
        ["published_at", "available_at", "lease_expires_at"],
    )
    op.drop_column("outbox", "lease_fence")
    op.drop_column("outbox", "tenant_id")

    op.drop_constraint(
        "fk_version_candidates_project",
        "version_candidates",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_version_candidates_tenant_project_state",
        table_name="version_candidates",
    )
    op.drop_constraint(
        "uq_version_candidate_tenant_project_version",
        "version_candidates",
        type_="unique",
    )
    op.drop_constraint("pk_version_candidates", "version_candidates", type_="primary")
    op.create_primary_key("version_candidates_pkey", "version_candidates", ["id"])
    op.create_unique_constraint(
        "uq_version_candidate",
        "version_candidates",
        ["project_id", "version_id"],
    )
    op.create_index(
        "ix_version_candidates_project_state",
        "version_candidates",
        ["project_id", "state"],
    )
    op.drop_column("version_candidates", "tenant_id")

    op.drop_constraint("pk_worker_leases", "worker_leases", type_="primary")
    op.create_primary_key(
        "worker_leases_pkey",
        "worker_leases",
        ["resource_type", "resource_id"],
    )
    op.drop_column("worker_leases", "tenant_id")

    op.drop_index("ix_domain_events_tenant_project", table_name="domain_events")
    op.drop_index("ix_domain_events_tenant_cursor", table_name="domain_events")
    op.drop_constraint(
        "uq_domain_events_tenant_task_sequence",
        "domain_events",
        type_="unique",
    )
    op.drop_constraint("uq_domain_events_tenant_event", "domain_events", type_="unique")
    op.drop_constraint("pk_domain_events", "domain_events", type_="primary")
    op.create_primary_key("domain_events_pkey", "domain_events", ["cursor"])
    op.create_unique_constraint("domain_events_event_id_key", "domain_events", ["event_id"])
    op.create_index("ix_domain_events_project_id", "domain_events", ["project_id"])
    op.create_index(
        "ix_domain_events_task_sequence",
        "domain_events",
        ["task_id", "task_sequence"],
    )
    op.create_index(
        "ix_domain_events_user_cursor",
        "domain_events",
        ["user_id", "cursor"],
    )
    op.drop_column("domain_events", "message")
    op.drop_column("domain_events", "run_id")
    op.drop_column("domain_events", "tenant_id")

    for table_name in (
        "task_event_sequences",
        "core_checkpoints",
        "core_approvals",
        "core_changesets",
        "command_runs",
        "core_tasks",
        "core_versions",
        "core_conversations",
        "core_projects",
        "core_tenants",
    ):
        op.drop_table(table_name)


def _tenant_id(user_id: str) -> str:
    return hashlib.sha256(f"fairy:v3:tenant:{user_id}".encode()).hexdigest()


def _tenant_column() -> sa.Column[str]:
    return sa.Column("tenant_id", sa.String(TENANT_LENGTH), nullable=False)


def _id_column() -> sa.Column[str]:
    return sa.Column("id", sa.String(ID_LENGTH), nullable=False)


def _created_at_column() -> sa.Column[datetime]:
    return sa.Column("created_at", sa.DateTime(timezone=True), nullable=False)


def _updated_at_column() -> sa.Column[datetime]:
    return sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)


_BACKFILL_FAILURE_QUERY = """
SELECT
    (SELECT count(*) FROM domain_events WHERE tenant_id IS NULL) AS events_without_tenant,
    (SELECT count(*) FROM outbox WHERE tenant_id IS NULL) AS outbox_without_tenant,
    (SELECT count(*) FROM version_candidates WHERE tenant_id IS NULL)
        AS candidates_without_tenant,
    (SELECT count(*) FROM worker_leases WHERE tenant_id IS NULL) AS leases_without_tenant,
    (
        SELECT count(*)
        FROM cloud_projects AS legacy
        LEFT JOIN core_tenants AS tenant
          ON tenant.subject_id = legacy.user_id
        LEFT JOIN core_projects AS project
          ON project.tenant_id = tenant.tenant_id
         AND project.id = legacy.project_id
        WHERE tenant.tenant_id IS NULL OR project.id IS NULL
    ) AS project_ownership_mismatch,
    abs(
        (SELECT count(*) FROM cloud_projects) -
        (SELECT count(*) FROM core_projects)
    ) AS project_count_mismatch
"""

_BACKFILL_ASSERTIONS = f"""
DO $$
DECLARE failures record;
BEGIN
    SELECT * INTO failures FROM ({_BACKFILL_FAILURE_QUERY}) AS checks;
    IF failures.events_without_tenant <> 0
       OR failures.outbox_without_tenant <> 0
       OR failures.candidates_without_tenant <> 0
       OR failures.leases_without_tenant <> 0
       OR failures.project_ownership_mismatch <> 0
       OR failures.project_count_mismatch <> 0 THEN
        RAISE EXCEPTION 'canonical backfill verification failed';
    END IF;
END $$
"""

_DOWNGRADE_ASSERTIONS = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM core_projects GROUP BY id HAVING count(*) > 1)
       OR EXISTS (SELECT 1 FROM domain_events GROUP BY event_id HAVING count(*) > 1)
       OR EXISTS (SELECT 1 FROM outbox GROUP BY event_id HAVING count(*) > 1)
       OR EXISTS (
           SELECT 1 FROM version_candidates
           GROUP BY project_id, version_id HAVING count(*) > 1
       )
       OR EXISTS (
           SELECT 1 FROM worker_leases
           GROUP BY resource_type, resource_id HAVING count(*) > 1
       ) THEN
        RAISE EXCEPTION 'cannot downgrade tenant-scoped identifiers to global uniqueness';
    END IF;
END $$
"""

_OUTBOX_PAYLOAD_BACKFILL = """
UPDATE outbox AS target
SET payload = json_build_object(
    'tenant_id', event.tenant_id,
    'cursor', event.cursor,
    'event_id', event.event_id,
    'run_id', event.run_id,
    'user_id', event.user_id,
    'device_id', event.device_id,
    'project_id', event.project_id,
    'conversation_id', event.conversation_id,
    'task_id', event.task_id,
    'version_id', event.version_id,
    'task_sequence', event.task_sequence,
    'schema_version', event.schema_version,
    'event_type', event.event_type,
    'visibility', event.visibility,
    'message', event.message,
    'payload', event.payload,
    'created_at', to_char(
        event.created_at AT TIME ZONE 'UTC',
        'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
    )
)
FROM domain_events AS event
WHERE target.tenant_id = event.tenant_id
  AND target.event_id = event.event_id
"""
