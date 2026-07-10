from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url


@dataclass(slots=True)
class PostgresTestContext:
    admin_dsn: str
    app_dsn: str
    core_dsn: str
    tenant_ids: list[str] = field(default_factory=list)

    @property
    def admin_sync_dsn(self) -> str:
        return _psycopg_url(self.admin_dsn).render_as_string(hide_password=False)

    @property
    def core_sync_dsn(self) -> str:
        return _psycopg_url(self.core_dsn).render_as_string(hide_password=False)

    def track_tenant(self, tenant_id: str) -> str:
        if tenant_id not in self.tenant_ids:
            self.tenant_ids.append(tenant_id)
        return tenant_id


@pytest.fixture(scope="session")
def postgres_integration_context() -> PostgresTestContext:
    admin_dsn = os.environ.get("FAIRY_TEST_POSTGRES_DSN")
    app_dsn = os.environ.get("FAIRY_TEST_APP_POSTGRES_DSN")
    core_dsn = os.environ.get("FAIRY_TEST_CORE_POSTGRES_DSN")
    if not all((admin_dsn, app_dsn, core_dsn)):
        pytest.skip(
            "FAIRY_TEST_POSTGRES_DSN, FAIRY_TEST_APP_POSTGRES_DSN, and "
            "FAIRY_TEST_CORE_POSTGRES_DSN are required"
        )
    assert admin_dsn is not None
    assert app_dsn is not None
    assert core_dsn is not None

    config = Config(Path(__file__).parents[2] / "alembic.ini")
    old_dsn = os.environ.get("FAIRY_POSTGRES_DSN")
    os.environ["FAIRY_POSTGRES_DSN"] = admin_dsn
    try:
        command.upgrade(config, "head")
    finally:
        if old_dsn is None:
            os.environ.pop("FAIRY_POSTGRES_DSN", None)
        else:
            os.environ["FAIRY_POSTGRES_DSN"] = old_dsn
    return PostgresTestContext(admin_dsn, app_dsn, core_dsn)


@pytest.fixture
def postgres_test_context(
    postgres_integration_context: PostgresTestContext,
) -> Iterator[PostgresTestContext]:
    context = PostgresTestContext(
        postgres_integration_context.admin_dsn,
        postgres_integration_context.app_dsn,
        postgres_integration_context.core_dsn,
    )
    try:
        yield context
    finally:
        _delete_tenants(context)


def _psycopg_url(dsn: str) -> URL:
    return make_url(dsn).set(drivername="postgresql+psycopg")


def _delete_tenants(context: PostgresTestContext) -> None:
    if not context.tenant_ids:
        return
    tables = (
        "memory_access_log",
        "memory_snapshot_items",
        "memory_search_documents",
        "memory_projection_checkpoints",
        "memory_snapshots",
        "memory_tombstones",
        "memory_claim_revisions",
        "memory_claims",
        "memory_observations",
        "outbox",
        "core_artifacts",
        "core_preview_sessions",
        "core_runtime_sessions",
        "core_checkpoints",
        "core_approvals",
        "core_changesets",
        "domain_events",
        "task_event_sequences",
        "command_runs",
        "core_tasks",
        "core_versions",
        "core_conversations",
        "version_candidates",
        "worker_leases",
        "core_projects",
        "core_tenants",
    )
    engine = create_engine(context.admin_sync_dsn, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            for table_name in tables:
                connection.execute(
                    text(f"DELETE FROM {table_name} WHERE tenant_id = ANY(:tenant_ids)"),
                    {"tenant_ids": context.tenant_ids},
                )
    finally:
        engine.dispose()
