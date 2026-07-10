from __future__ import annotations

import io
from pathlib import Path

import yaml
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from fairy_cloud.openapi import build_openapi_document

CLOUD_ROOT = Path(__file__).parents[1]


def test_alembic_has_one_linear_cloud_schema_head() -> None:
    config = Config(CLOUD_ROOT / "alembic.ini")
    scripts = ScriptDirectory.from_config(config)

    assert scripts.get_heads() == ["20260711_0005"]
    assert scripts.get_revision("20260711_0005").down_revision == "20260710_0004"


def test_offline_migration_contains_canonical_tenant_rls_and_fencing() -> None:
    output = io.StringIO()
    config = Config(CLOUD_ROOT / "alembic.ini", output_buffer=output)

    command.upgrade(config, "head", sql=True)

    ddl = " ".join(output.getvalue().upper().split())
    for table_name in (
        "CORE_TENANTS",
        "CORE_PROJECTS",
        "CORE_CONVERSATIONS",
        "CORE_VERSIONS",
        "CORE_TASKS",
        "CORE_CHANGESETS",
        "CORE_APPROVALS",
        "CORE_CHECKPOINTS",
        "COMMAND_RUNS",
        "TASK_EVENT_SEQUENCES",
        "MEMORY_OBSERVATIONS",
        "MEMORY_CLAIMS",
        "MEMORY_CLAIM_REVISIONS",
        "MEMORY_TOMBSTONES",
        "MEMORY_SNAPSHOTS",
        "MEMORY_SNAPSHOT_ITEMS",
        "MEMORY_SEARCH_DOCUMENTS",
        "MEMORY_ACCESS_LOG",
        "MEMORY_PROJECTION_CHECKPOINTS",
    ):
        assert f"CREATE TABLE {table_name}" in ddl
    assert "ALTER TABLE DOMAIN_EVENTS ADD COLUMN TENANT_ID" in ddl
    assert "LEASE_FENCE" in ddl
    assert "CREATE INDEX IX_OUTBOX_CLAIM_GLOBAL" in ddl
    assert "ENABLE ROW LEVEL SECURITY" in ddl
    assert "FORCE ROW LEVEL SECURITY" in ddl
    assert "CURRENT_SETTING('APP.TENANT_ID', TRUE)" in ddl
    assert "PROJECT_OWNERSHIP_MISMATCH" in ddl
    assert "JSON_BUILD_OBJECT" in ddl
    assert "PAYLOAD ->> 'RUN_ID'" in ddl
    assert "CONSTRAINT FK_DOMAIN_EVENTS_RUN" not in ddl
    assert "DROP TABLE CLOUD_PROJECTS" in ddl
    assert "CREATE UNIQUE INDEX UQ_MEMORY_CLAIM_REVISIONS_CURRENT" in ddl
    assert "CREATE FUNCTION FAIRY_ENQUEUE_DOMAIN_EVENT" in ddl
    assert "CREATE TRIGGER TRG_DOMAIN_EVENT_OUTBOX" in ddl
    assert "ON CONFLICT (TENANT_ID, EVENT_ID) DO NOTHING" in ddl
    assert "SECURITY DEFINER" not in ddl
    assert "ALTER TABLE CORE_TASKS ADD COLUMN MEMORY_SNAPSHOT_ID" in ddl
    assert "GENERATED ALWAYS AS" in ddl
    assert "TO_TSVECTOR('SIMPLE'" in ddl
    assert "USING GIN" in ddl
    assert "CK_MEMORY_SNAPSHOTS_STATUS" in ddl


def test_compose_uses_supported_brokerless_development_services() -> None:
    composition = yaml.safe_load((CLOUD_ROOT / "compose.yaml").read_text(encoding="utf-8"))
    services = composition["services"]

    assert set(services) == {
        "api",
        "integration",
        "migrate",
        "object-store",
        "oidc",
        "postgres",
        "postgres-permissions",
        "worker",
    }
    assert services["postgres"]["image"] == "postgres:18.4-alpine3.24"
    assert services["object-store"]["image"] == "chrislusf/seaweedfs:4.39"
    assert services["oidc"]["image"] == "ghcr.io/navikt/mock-oauth2-server:4.0.0"
    assert "fairy-postgres:/var/lib/postgresql" in services["postgres"]["volumes"]
    assert services["object-store"]["environment"]["S3_BUCKET"] == "fairy-objects"
    assert all(
        "healthcheck" in services[name]
        for name in {"api", "object-store", "oidc", "postgres", "worker"}
    )
    assert services["api"]["build"]["target"] == "runtime"
    assert services["worker"]["command"] == [
        "python",
        "-m",
        "fairy_cloud.workers.outbox",
    ]
    assert services["worker"]["read_only"] is True
    assert services["worker"]["cap_drop"] == ["ALL"]
    assert services["migrate"]["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert services["postgres-permissions"]["depends_on"]["migrate"]["condition"] == (
        "service_completed_successfully"
    )
    assert services["api"]["depends_on"]["postgres-permissions"]["condition"] == (
        "service_completed_successfully"
    )
    assert services["worker"]["depends_on"]["postgres-permissions"]["condition"] == (
        "service_completed_successfully"
    )
    assert services["integration"]["profiles"] == ["test"]
    assert "fairy_app:" in services["integration"]["environment"]["FAIRY_TEST_APP_POSTGRES_DSN"]
    assert (
        "postgresql+psycopg://fairy_app:"
        in services["integration"]["environment"]["FAIRY_TEST_CORE_POSTGRES_DSN"]
    )
    assert "fairy_app:" in services["api"]["environment"]["FAIRY_POSTGRES_DSN"]
    assert "fairy_worker:" in services["worker"]["environment"]["FAIRY_POSTGRES_DSN"]
    assert "fairy:" in services["migrate"]["environment"]["FAIRY_POSTGRES_DSN"]
    assert any(
        "docker-entrypoint-initdb.d/010-fairy-roles.sh" in volume
        for volume in services["postgres"]["volumes"]
    )
    assert not ({"redis", "nats"} & set(services))
    assert "docker.sock" not in (CLOUD_ROOT / "compose.yaml").read_text(encoding="utf-8")


def test_postgres_init_creates_rls_app_and_cross_tenant_worker_roles() -> None:
    script = (CLOUD_ROOT / "docker/postgres/010-fairy-roles.sh").read_text(encoding="utf-8")
    app_role = next(line for line in script.splitlines() if line.startswith("ALTER ROLE fairy_app"))
    worker_role = next(
        line for line in script.splitlines() if line.startswith("ALTER ROLE fairy_worker")
    )

    assert "NOSUPERUSER" in app_role
    assert "NOBYPASSRLS" in app_role
    assert "NOSUPERUSER" in worker_role
    assert "BYPASSRLS" in worker_role
    assert "NOBYPASSRLS" not in worker_role
    assert "ALTER DEFAULT PRIVILEGES" in script
    assert "GRANT USAGE, SELECT ON ALL SEQUENCES" in script
    assert "ON ALL TABLES IN SCHEMA public TO fairy_app, fairy_worker" not in script
    assert "ON TABLE public.outbox TO fairy_worker" in script
    assert "ON TABLE public.worker_leases TO fairy_worker" in script
    assert "TABLES TO fairy_app, fairy_worker" not in script
    assert "REVOKE ALL ON TABLE public.alembic_version FROM fairy_app, fairy_worker" in script


def test_cloud_image_is_pinned_and_runs_as_non_root() -> None:
    dockerfile = (CLOUD_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "ghcr.io/astral-sh/uv:0.11.28-python3.13-trixie-slim" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert "uv sync --locked --no-dev --no-editable" in dockerfile


def test_exported_openapi_uses_public_rpc_operation_ids() -> None:
    document = build_openapi_document()
    operation_ids = {
        operation["operationId"]
        for path in document["paths"].values()
        for operation in path.values()
        if isinstance(operation, dict) and "operationId" in operation
    }

    assert "projects.create" in operation_ids
    assert "events.subscribe" in operation_ids


def test_full_verification_script_covers_every_release_gate() -> None:
    script_path = CLOUD_ROOT.parent / "scripts" / "test-all.ps1"
    script = script_path.read_text(encoding="utf-8")

    for required_text in (
        "check_boundaries.py",
        "uv lock --check",
        "ruff format --check",
        "ruff check",
        "pytest",
        "alembic",
        "upgrade head --sql",
        "downgrade head:base --sql",
        "cargo fmt --check",
        "cargo clippy",
        "cargo test",
        "npm test -- --run",
        "npm run build",
        "generate-contracts.ps1",
        "git diff --exit-code",
        "docker version",
        "docker compose",
        "integration",
        "PostgreSQL/S3 integration tests skipped",
    ):
        assert required_text in script
