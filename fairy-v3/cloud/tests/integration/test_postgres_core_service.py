from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine

from fairy_cloud.api import create_cloud_app
from fairy_cloud.auth import RequestIdentity, StaticTokenAuthenticator
from fairy_cloud.dispatchers import TenantRuntimeRegistry
from fairy_cloud.storage.postgres import PostgresSyncStore, tenant_id_for_user

ADMIN_DSN = os.environ.get("FAIRY_TEST_POSTGRES_DSN")
APP_DSN = os.environ.get("FAIRY_TEST_APP_POSTGRES_DSN")
CORE_DSN = os.environ.get("FAIRY_TEST_CORE_POSTGRES_DSN")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not all((ADMIN_DSN, APP_DSN, CORE_DSN)),
        reason=(
            "FAIRY_TEST_POSTGRES_DSN, FAIRY_TEST_APP_POSTGRES_DSN, and "
            "FAIRY_TEST_CORE_POSTGRES_DSN are required"
        ),
    ),
]


@pytest.mark.asyncio
async def test_rest_core_command_is_visible_once_through_postgres_sse(tmp_path: Path) -> None:
    assert ADMIN_DSN is not None
    assert APP_DSN is not None
    assert CORE_DSN is not None
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    old_dsn = os.environ.get("FAIRY_POSTGRES_DSN")
    os.environ["FAIRY_POSTGRES_DSN"] = ADMIN_DSN
    try:
        command.upgrade(config, "head")
    finally:
        if old_dsn is None:
            os.environ.pop("FAIRY_POSTGRES_DSN", None)
        else:
            os.environ["FAIRY_POSTGRES_DSN"] = old_dsn

    user_id = f"core-service-{uuid4().hex}"
    tenant_id = tenant_id_for_user(user_id)
    core_engine = create_engine(CORE_DSN, pool_pre_ping=True)
    async_engine = create_async_engine(APP_DSN, pool_pre_ping=True)
    admin_engine = create_async_engine(ADMIN_DSN, pool_pre_ping=True)
    runtimes = TenantRuntimeRegistry(root=tmp_path / "workspaces", engine=core_engine)
    identity = RequestIdentity(user_id, "device-integration", frozenset({"fairy.api"}))
    app = create_cloud_app(
        runtimes.system_service(),
        authenticator=StaticTokenAuthenticator({"integration-token": identity}),
        sync_store=PostgresSyncStore(async_engine),
        service_resolver=runtimes.for_identity,
    )
    headers = {
        "Authorization": "Bearer integration-token",
        "X-Fairy-Device-ID": "device-integration",
    }
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers=headers,
        ) as client:
            created = await client.post(
                "/v1/projects",
                json={"name": "PostgreSQL Core", "residency": "synced"},
            )
            stream = await client.get("/v1/events", params={"cursor": 0, "follow": False})

        created.raise_for_status()
        stream.raise_for_status()
        events = _parse_sse(stream.text)
        succeeded = [
            event for event in events if event["data"]["event_type"] == "command.succeeded"
        ]

        assert len(succeeded) == 1
        assert succeeded[0]["data"]["project_id"] == created.json()["project"]["id"]
        cursors = [int(event["id"]) for event in events]
        assert cursors == sorted(set(cursors))
    finally:
        runtimes.close()
        core_engine.dispose()
        await async_engine.dispose()
        await _delete_tenant(admin_engine, tenant_id)
        await admin_engine.dispose()


async def _delete_tenant(admin_engine, tenant_id: str) -> None:
    tables = (
        "outbox",
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
    async with admin_engine.begin() as connection:
        for table_name in tables:
            await connection.execute(
                text(f"DELETE FROM {table_name} WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )


def _parse_sse(payload: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for block in payload.strip().split("\n\n"):
        fields: dict[str, object] = {}
        for line in block.splitlines():
            name, value = line.split(":", 1)
            fields[name] = value.strip()
        if "data" in fields:
            fields["data"] = json.loads(str(fields["data"]))
            events.append(fields)
    return events
