from __future__ import annotations

import io
from pathlib import Path

import yaml
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from fairy_cloud.openapi import build_openapi_document

CLOUD_ROOT = Path(__file__).parents[1]


def test_outbox_worker_has_one_canonical_module_entrypoint() -> None:
    assert not (CLOUD_ROOT / "src" / "fairy_cloud" / "worker.py").exists()


def test_alembic_has_one_linear_cloud_schema_head() -> None:
    config = Config(CLOUD_ROOT / "alembic.ini")
    scripts = ScriptDirectory.from_config(config)

    assert scripts.get_heads() == ["20260714_0025"]
    assert scripts.get_revision("20260712_0016").down_revision == "20260711_0015"
    assert scripts.get_revision("20260711_0015").down_revision == "20260711_0014"
    assert scripts.get_revision("20260711_0013").down_revision == "20260711_0012"
    assert scripts.get_revision("20260711_0012").down_revision == "20260711_0011"
    assert scripts.get_revision("20260711_0009").down_revision == "20260711_0008"


def test_offline_migration_contains_canonical_tenant_rls_and_fencing() -> None:
    output = io.StringIO()
    config = Config(CLOUD_ROOT / "alembic.ini", output_buffer=output)

    command.upgrade(config, "head", sql=True)

    ddl = " ".join(output.getvalue().upper().split())
    for table_name in (
        "CORE_TENANTS",
        "CORE_PROJECTS",
        "CORE_EXECUTION_SETTINGS",
        "CORE_EXECUTION_SETTING_UPDATES",
        "CORE_MCP_SERVERS",
        "CORE_MCP_SERVER_UPDATES",
        "CORE_CONVERSATIONS",
        "CORE_VERSIONS",
        "CORE_TASKS",
        "CORE_TASK_WORKSPACES",
        "CORE_PROJECT_INDEXES",
        "EXECUTION_JOBS",
        "EXECUTION_WORKERS",
        "RUNTIME_LEASES",
        "RUNTIME_ROUTES",
        "RUNTIME_WORKERS",
        "CORE_CHANGESETS",
        "CORE_APPROVALS",
        "CORE_CHECKPOINTS",
        "CORE_RUNTIME_SESSIONS",
        "CORE_PREVIEW_SESSIONS",
        "CORE_ARTIFACTS",
        "CORE_ASSISTANT_TURNS",
        "CORE_ASSISTANT_MESSAGE_SEQUENCES",
        "CORE_ASSISTANT_MESSAGES",
        "CORE_ASSISTANT_TOOL_INVOCATIONS",
        "CORE_RESEARCH_EVIDENCE",
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
    assert "ALTER TABLE MEMORY_SEARCH_DOCUMENTS ADD COLUMN FTS_ROWID" in ddl
    assert "UQ_MEMORY_SEARCH_DOCUMENTS_FTS_ROWID" in ddl
    assert "UQ_CORE_RUNTIME_SESSIONS_TENANT_IDEMPOTENCY" in ddl
    assert "UQ_CORE_PREVIEW_SESSIONS_TENANT_IDEMPOTENCY" in ddl
    assert "UQ_CORE_PREVIEW_SESSIONS_ACTIVE_TASK" in ddl
    assert "CK_CORE_RUNTIME_SESSIONS_HANDLE_PORT" in ddl
    assert "CK_CORE_PREVIEW_SESSIONS_ACTIVE_URL" in ddl
    assert "UQ_CORE_APPROVALS_TENANT_COMMAND_RUN" in ddl
    assert "UQ_CORE_APPROVALS_TENANT_TOOL_INVOCATION" in ddl
    assert "UQ_CORE_ASSISTANT_TOOL_INVOCATIONS_TURN_PROVIDER_CALL" in ddl
    assert "FK_CORE_APPROVALS_TOOL_INVOCATION" in ddl
    assert "ALTER TABLE CORE_CHECKPOINTS ADD COLUMN EVIDENCE_ARTIFACT_IDS" in ddl
    for table_name in (
        "CORE_RUNTIME_SESSIONS",
        "CORE_PREVIEW_SESSIONS",
        "CORE_ARTIFACTS",
        "CORE_ASSISTANT_TURNS",
        "CORE_ASSISTANT_MESSAGE_SEQUENCES",
        "CORE_ASSISTANT_MESSAGES",
        "CORE_ASSISTANT_TOOL_INVOCATIONS",
        "CORE_RESEARCH_EVIDENCE",
        "CORE_EXECUTION_SETTINGS",
        "CORE_EXECUTION_SETTING_UPDATES",
        "CORE_MCP_SERVERS",
        "CORE_MCP_SERVER_UPDATES",
        "CORE_TASK_WORKSPACES",
        "CORE_PROJECT_INDEXES",
        "EXECUTION_JOBS",
        "RUNTIME_LEASES",
    ):
        assert f'CREATE POLICY "TENANT_ISOLATION_{table_name}"' in ddl
    assert "RESULT_DELETED" in ddl
    assert "RESULT_ERROR_CODE" in ddl
    assert "FK_CORE_MCP_SERVER_UPDATES_SERVER" not in ddl


def test_event_outbox_migration_executes_asyncpg_ddl_one_command_at_a_time() -> None:
    migration = (
        CLOUD_ROOT / "migrations" / "versions" / "20260710_0004_event_outbox.py"
    ).read_text(encoding="utf-8")

    for statement in (
        "_CREATE_OUTBOX_FUNCTION",
        "_CREATE_OUTBOX_TRIGGER",
        "_DROP_OUTBOX_TRIGGER",
        "_DROP_OUTBOX_FUNCTION",
    ):
        assert f"op.execute(sa.text({statement}))" in migration
    function_ddl = migration.split('_CREATE_OUTBOX_FUNCTION = r"""', 1)[1].split('"""', 1)[0]
    trigger_ddl = migration.split('_CREATE_OUTBOX_TRIGGER = """', 1)[1].split('"""', 1)[0]
    assert "CREATE TRIGGER" not in function_ddl
    assert "CREATE FUNCTION" not in trigger_ddl


def test_execution_settings_migration_has_reversible_ddl() -> None:
    output = io.StringIO()
    config = Config(CLOUD_ROOT / "alembic.ini", output_buffer=output)

    command.downgrade(config, "20260711_0011:20260711_0010", sql=True)

    ddl = " ".join(output.getvalue().upper().split())
    assert 'DROP POLICY IF EXISTS "TENANT_ISOLATION_CORE_EXECUTION_SETTINGS"' in ddl
    assert 'DROP POLICY IF EXISTS "TENANT_ISOLATION_CORE_EXECUTION_SETTING_UPDATES"' in ddl
    assert "DROP TABLE CORE_EXECUTION_SETTING_UPDATES" in ddl
    assert "DROP TABLE CORE_EXECUTION_SETTINGS" in ddl


def test_generic_approval_migration_has_reversible_ddl() -> None:
    output = io.StringIO()
    config = Config(CLOUD_ROOT / "alembic.ini", output_buffer=output)

    command.downgrade(config, "20260711_0012:20260711_0011", sql=True)

    ddl = " ".join(output.getvalue().upper().split())
    assert "DROP CONSTRAINT FK_CORE_APPROVALS_TOOL_INVOCATION" in ddl
    assert "DROP COLUMN TOOL_INVOCATION_ID" in ddl
    assert "DROP COLUMN PROVIDER_CALL_ID" in ddl
    assert "DROP COLUMN MODEL_ROUND" in ddl


def test_workspace_index_migration_has_reversible_ddl() -> None:
    output = io.StringIO()
    config = Config(CLOUD_ROOT / "alembic.ini", output_buffer=output)

    command.downgrade(config, "20260711_0013:20260711_0012", sql=True)

    ddl = " ".join(output.getvalue().upper().split())
    assert 'DROP POLICY IF EXISTS "TENANT_ISOLATION_CORE_PROJECT_INDEXES"' in ddl
    assert 'DROP POLICY IF EXISTS "TENANT_ISOLATION_CORE_TASK_WORKSPACES"' in ddl
    assert "DROP TABLE CORE_PROJECT_INDEXES" in ddl
    assert "DROP TABLE CORE_TASK_WORKSPACES" in ddl


def test_execution_job_migration_has_reversible_fenced_queue_ddl() -> None:
    output = io.StringIO()
    config = Config(CLOUD_ROOT / "alembic.ini", output_buffer=output)

    command.downgrade(config, "20260711_0014:20260711_0013", sql=True)

    ddl = " ".join(output.getvalue().upper().split())
    assert 'DROP POLICY IF EXISTS "TENANT_ISOLATION_EXECUTION_JOBS"' in ddl
    assert "DROP TABLE EXECUTION_WORKERS" in ddl
    assert "DROP TABLE EXECUTION_JOBS" in ddl


def test_execution_purpose_migration_is_reversible_and_fail_closed() -> None:
    output = io.StringIO()
    config = Config(CLOUD_ROOT / "alembic.ini", output_buffer=output)

    command.downgrade(config, "20260711_0015:20260711_0014", sql=True)

    ddl = " ".join(output.getvalue().upper().split())
    assert "DROP CONSTRAINT CK_EXECUTION_JOBS_NETWORK_POLICY" in ddl
    assert "DROP COLUMN PURPOSE" in ddl


def test_dynamic_runtime_migration_is_reversible_and_fenced() -> None:
    output = io.StringIO()
    config = Config(CLOUD_ROOT / "alembic.ini", output_buffer=output)

    command.downgrade(config, "20260712_0016:20260711_0015", sql=True)

    ddl = " ".join(output.getvalue().upper().split())
    assert "DROP TABLE RUNTIME_ROUTES" in ddl
    assert "DROP TABLE RUNTIME_WORKERS" in ddl
    assert "DROP TABLE RUNTIME_LEASES" in ddl
    assert "DROP COLUMN DEPENDENCY_KEY" in ddl
    assert "DROP COLUMN DEPENDENCY_MANAGER" in ddl
    assert "DROP COLUMN EVIDENCE_ARTIFACT_IDS" in ddl


def test_governed_mcp_migration_is_reversible_and_keeps_delete_tombstones() -> None:
    output = io.StringIO()
    config = Config(CLOUD_ROOT / "alembic.ini", output_buffer=output)

    command.downgrade(config, "20260712_0017:20260712_0016", sql=True)

    ddl = " ".join(output.getvalue().upper().split())
    assert 'DROP POLICY IF EXISTS "TENANT_ISOLATION_CORE_MCP_SERVER_UPDATES"' in ddl
    assert 'DROP POLICY IF EXISTS "TENANT_ISOLATION_CORE_MCP_SERVERS"' in ddl
    assert "DROP TABLE CORE_MCP_SERVER_UPDATES" in ddl
    assert "DROP TABLE CORE_MCP_SERVERS" in ddl


def test_research_evidence_migration_has_reversible_ddl() -> None:
    output = io.StringIO()
    config = Config(CLOUD_ROOT / "alembic.ini", output_buffer=output)

    command.downgrade(config, "20260711_0009:20260711_0008", sql=True)

    ddl = " ".join(output.getvalue().upper().split())
    assert 'DROP POLICY IF EXISTS "TENANT_ISOLATION_CORE_RESEARCH_EVIDENCE"' in ddl
    assert "DROP INDEX IX_CORE_RESEARCH_EVIDENCE_TENANT_ARTIFACT" in ddl
    assert "DROP TABLE CORE_RESEARCH_EVIDENCE" in ddl


def test_assistant_ledger_migration_has_reversible_ddl() -> None:
    output = io.StringIO()
    config = Config(CLOUD_ROOT / "alembic.ini", output_buffer=output)

    command.downgrade(config, "20260711_0008:20260711_0007", sql=True)

    ddl = " ".join(output.getvalue().upper().split())
    assert 'DROP POLICY IF EXISTS "TENANT_ISOLATION_CORE_ASSISTANT_TOOL_INVOCATIONS"' in ddl
    assert 'DROP POLICY IF EXISTS "TENANT_ISOLATION_CORE_ASSISTANT_MESSAGES"' in ddl
    assert 'DROP POLICY IF EXISTS "TENANT_ISOLATION_CORE_ASSISTANT_TURNS"' in ddl
    assert "DROP TABLE CORE_ASSISTANT_TOOL_INVOCATIONS" in ddl
    assert "DROP TABLE CORE_ASSISTANT_MESSAGES" in ddl
    assert "DROP TABLE CORE_ASSISTANT_MESSAGE_SEQUENCES" in ddl
    assert "DROP TABLE CORE_ASSISTANT_TURNS" in ddl


def test_runtime_preview_migration_has_reversible_ddl() -> None:
    output = io.StringIO()
    config = Config(CLOUD_ROOT / "alembic.ini", output_buffer=output)

    command.downgrade(config, "20260711_0007:20260711_0006", sql=True)

    ddl = " ".join(output.getvalue().upper().split())
    assert 'DROP POLICY IF EXISTS "TENANT_ISOLATION_CORE_ARTIFACTS"' in ddl
    assert "DROP TABLE CORE_ARTIFACTS" in ddl
    assert "DROP TABLE CORE_PREVIEW_SESSIONS" in ddl
    assert "DROP TABLE CORE_RUNTIME_SESSIONS" in ddl


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
        "execution",
        "runtime",
    }
    assert services["postgres"]["image"] == "postgres:18.4-alpine3.24"
    assert services["object-store"]["image"] == "chrislusf/seaweedfs:4.39"
    assert services["oidc"]["image"] == "ghcr.io/navikt/mock-oauth2-server:4.0.0"
    assert "FAIRY_PROVIDER_SECRET_BRAVE" in services["api"]["environment"]
    assert "FAIRY_PROVIDER_SECRET_ALPHA_VANTAGE" in services["api"]["environment"]
    assert "FAIRY_RUNTIME_GATEWAY_KEY" in services["api"]["environment"]
    assert "FAIRY_MCP_CREDENTIALS" in services["api"]["environment"]
    assert "FAIRY_MCP_ALLOWED_HOSTS" in services["api"]["environment"]
    assert services["api"]["environment"]["FAIRY_EVENT_POLL_SECONDS"] == "0.025"
    assert services["api"]["environment"]["FAIRY_RECOVERY_INTERVAL_SECONDS"] == "5"
    assert services["api"]["environment"]["FAIRY_PROVIDER_BRAVE_CREDENTIAL_REF"].endswith(
        ":-brave}"
    )
    assert services["api"]["environment"]["FAIRY_PROVIDER_ALPHA_VANTAGE_CREDENTIAL_REF"].endswith(
        ":-alpha_vantage}"
    )
    assert "fairy-postgres:/var/lib/postgresql" in services["postgres"]["volumes"]
    assert services["object-store"]["environment"]["S3_BUCKET"] == "fairy-objects"
    assert services["object-store"]["healthcheck"]["test"][-1] == (
        "http://localhost:9333/cluster/status"
    )
    for worker in ("execution", "runtime"):
        assert services[worker]["cap_drop"] == ["ALL"]
        assert services[worker]["read_only"] is True
        assert services[worker]["security_opt"] == [
            "no-new-privileges:true",
            "seccomp:unconfined",
        ]
    assert all(
        "healthcheck" in services[name]
        for name in {
            "api",
            "execution",
            "runtime",
            "object-store",
            "oidc",
            "postgres",
            "worker",
        }
    )
    assert services["api"]["build"]["target"] == "runtime"
    assert services["worker"]["command"] == [
        "python",
        "-m",
        "fairy_cloud.workers.outbox",
    ]
    assert services["worker"]["read_only"] is True
    assert services["worker"]["cap_drop"] == ["ALL"]
    assert services["worker"].get("volumes", []) == []
    assert "FAIRY_S3_SECRET_KEY" not in services["worker"]["environment"]
    assert "FAIRY_PROVIDER_SECRET_OPENROUTER" not in services["worker"]["environment"]
    assert services["execution"]["command"] == [
        "python",
        "-m",
        "fairy_cloud.workers.execution",
    ]
    assert services["execution"]["read_only"] is True
    assert services["execution"]["cap_drop"] == ["ALL"]
    assert services["execution"]["volumes"] == [
        "fairy-dependencies:/var/lib/fairy-sandbox/dependencies"
    ]
    assert services["execution"]["pids_limit"] == 768
    assert services["execution"]["mem_limit"] == "3g"
    assert services["execution"]["cpus"] == 2.0
    assert any("/var/lib/fairy-sandbox" in item for item in services["execution"]["tmpfs"])
    assert services["runtime"]["command"] == [
        "python",
        "-m",
        "fairy_cloud.workers.runtime",
    ]
    assert services["runtime"]["read_only"] is True
    assert services["runtime"]["cap_drop"] == ["ALL"]
    assert services["runtime"]["volumes"] == [
        "fairy-dependencies:/var/lib/fairy-sandbox/dependencies:ro"
    ]
    assert "FAIRY_RUNTIME_GATEWAY_KEY" in services["runtime"]["environment"]
    assert services["runtime"]["environment"]["FAIRY_RUNTIME_LEASE_SECONDS"] == "180"
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
    assert "fairy_execution:" in services["execution"]["environment"]["FAIRY_POSTGRES_DSN"]
    assert "fairy_runtime:" in services["runtime"]["environment"]["FAIRY_POSTGRES_DSN"]
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
    execution_role = next(
        line for line in script.splitlines() if line.startswith("ALTER ROLE fairy_execution")
    )
    runtime_role = next(
        line for line in script.splitlines() if line.startswith("ALTER ROLE fairy_runtime")
    )

    assert "NOSUPERUSER" in app_role
    assert "NOBYPASSRLS" in app_role
    assert "NOSUPERUSER" in worker_role
    assert "BYPASSRLS" in worker_role
    assert "NOBYPASSRLS" not in worker_role
    assert "NOSUPERUSER" in execution_role
    assert "BYPASSRLS" in execution_role
    assert "NOSUPERUSER" in runtime_role
    assert "BYPASSRLS" in runtime_role
    assert "ALTER DEFAULT PRIVILEGES" in script
    assert "GRANT USAGE, SELECT ON ALL SEQUENCES" in script
    assert "ON ALL TABLES IN SCHEMA public TO fairy_app, fairy_worker" not in script
    assert "ON TABLE public.outbox TO fairy_worker" in script
    assert "ON TABLE public.worker_leases TO fairy_worker" in script
    assert "ON TABLE public.execution_jobs TO fairy_execution" in script
    assert "ON TABLE public.execution_workers TO fairy_execution" in script
    assert "ON TABLE public.runtime_leases TO fairy_runtime" in script
    assert "ON TABLE public.runtime_workers TO fairy_runtime" in script
    assert "INSERT, UPDATE, DELETE ON TABLE public.execution_jobs TO fairy_execution" not in script
    assert "DELETE ON TABLE public.execution_workers TO fairy_execution" not in script
    assert "ON TABLE public.core_projects TO fairy_execution" not in script
    assert "ON TABLE public.outbox TO fairy_execution" not in script
    assert "TABLES TO fairy_app, fairy_worker" not in script
    assert "REVOKE ALL ON TABLE public.alembic_version FROM fairy_app, fairy_worker" in script


def test_cloud_image_is_pinned_and_runs_as_non_root() -> None:
    dockerfile = (CLOUD_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "node:24.18.0-trixie-slim@sha256:366fdef9" in dockerfile
    assert "ghcr.io/astral-sh/uv:0.11.28-python3.13-trixie-slim" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert "uv sync --locked --no-dev --no-editable" in dockerfile
    assert "bubblewrap" in dockerfile
    assert "chromium" in dockerfile
    assert "npm" in dockerfile
    assert "pnpm@10.34.4" in dockerfile
    assert 'test "$(node --version)" = "v24.18.0"' in dockerfile
    assert "fairy_sandbox_runner.py" in dockerfile
    assert "/usr/local/bin/fairy-sandbox-runner" in dockerfile
    assert "fairy_runtime_supervisor.py" in dockerfile
    assert "/usr/local/bin/fairy-runtime-supervisor" in dockerfile


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
        "Desktop: npm test",
        "npm run e2e",
        "npm run build",
        "release_performance.py",
        "Core ready <= 3s and initial renderer gzip <= 800 KiB",
        "generate-contracts.ps1",
        "Contracts: generated files unchanged",
        "Git: diff --check",
        "docker version",
        "docker compose",
        "integration",
        "PostgreSQL/S3 integration tests skipped",
        "RequireWslSandbox",
        "WslSandboxHealthProbe",
        "FairySandbox attestation",
        "preview_recovery",
        "WSL sandbox verification skipped",
        "runtime Preview integration was not executed",
        '"clippy", "--workspace"',
        '"test", "--workspace"',
    ):
        assert required_text in script
