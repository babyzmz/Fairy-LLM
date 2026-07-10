from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from fairy_cloud.api import create_cloud_app
from fairy_cloud.auth import RequestIdentity, StaticTokenAuthenticator
from fairy_cloud.dispatchers import TenantRuntimeRegistry
from fairy_cloud.storage.postgres import PostgresSyncStore, tenant_id_for_user
from fairy_cloud.storage.schema import domain_events, outbox

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_production_memory_events_resume_without_ledger_or_outbox_gaps(
    tmp_path: Path,
    postgres_test_context,
) -> None:
    user_id = f"production-events-{uuid4().hex}"
    tenant_id = postgres_test_context.track_tenant(tenant_id_for_user(user_id))
    core_engine = create_engine(postgres_test_context.core_sync_dsn, pool_pre_ping=True)
    app_engine = create_async_engine(postgres_test_context.app_dsn, pool_pre_ping=True)
    admin_engine = create_async_engine(postgres_test_context.admin_dsn, pool_pre_ping=True)
    runtimes = TenantRuntimeRegistry(root=tmp_path / "workspaces", engine=core_engine)
    identity = RequestIdentity(user_id, "device-integration", frozenset({"fairy.api"}))
    app = create_cloud_app(
        runtimes.system_service(),
        authenticator=StaticTokenAuthenticator({"integration-token": identity}),
        sync_store=PostgresSyncStore(app_engine),
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
            project = await client.post(
                "/v1/projects",
                json={"name": "Production events", "residency": "synced"},
            )
            project.raise_for_status()
            conversation = await client.post(
                "/v1/conversations",
                json={
                    "project_id": project.json()["project"]["id"],
                    "workspace_type": "project_chat",
                },
            )
            conversation.raise_for_status()
            task = await client.post(
                "/v1/tasks",
                json={
                    "conversation_id": conversation.json()["id"],
                    "user_request": "Remember the framework",
                    "operation_mode": "continue_current_chat_draft",
                    "execution_target": "cloud",
                    "idempotency_key": "production-events:task",
                },
            )
            task.raise_for_status()
            task_id = task.json()["task"]["id"]
            observed = await client.post(
                "/v1/memory/observations",
                json={
                    "task_id": task_id,
                    "content": "The project uses React Aria.",
                    "idempotency_key": "production-events:observe",
                },
            )
            observed.raise_for_status()

            initial_stream = await client.get(
                "/v1/events",
                params={"cursor": 0, "follow": False},
            )
            initial_stream.raise_for_status()
            initial_events = _parse_sse(initial_stream.text)
            assert initial_events
            resume_cursor = int(initial_events[-1]["id"])

            promoted = await client.post(
                "/v1/memory/claims/promote",
                json={
                    "task_id": task_id,
                    "observation_id": observed.json()["id"],
                    "subject": "project",
                    "predicate": "accessibility_framework",
                    "value": "React Aria",
                    "normalized_text": "react aria",
                    "user_confirmed": True,
                    "idempotency_key": "production-events:promote",
                },
            )
            promoted.raise_for_status()
            resumed_stream = await client.get(
                "/v1/events",
                params={"cursor": 0, "follow": False},
                headers={"Last-Event-ID": str(resume_cursor)},
            )
            resumed_stream.raise_for_status()
            resumed_events = _parse_sse(resumed_stream.text)

            baseline_counts = await _ledger_counts(admin_engine, tenant_id)
            trigger_name, function_name = await _install_outbox_failure(
                admin_engine,
                tenant_id,
            )
            try:
                failed = await client.post(
                    "/v1/memory/observations",
                    json={
                        "task_id": task_id,
                        "content": "This write is interrupted before Outbox insertion.",
                        "idempotency_key": "production-events:rollback-observe",
                    },
                )
                assert failed.status_code == 500
                assert await _ledger_counts(admin_engine, tenant_id) == baseline_counts
            finally:
                await _drop_outbox_failure(admin_engine, trigger_name, function_name)

            recovered = await client.post(
                "/v1/memory/observations",
                json={
                    "task_id": task_id,
                    "content": "This write is interrupted before Outbox insertion.",
                    "idempotency_key": "production-events:rollback-observe",
                },
            )
            recovered.raise_for_status()

        initial_ids = [int(event["id"]) for event in initial_events]
        resumed_ids = [int(event["id"]) for event in resumed_events]
        assert all(cursor > resume_cursor for cursor in resumed_ids)
        assert not (set(initial_ids) & set(resumed_ids))
        assert resumed_ids == sorted(set(resumed_ids))
        resumed_types = [str(event["data"]["event_type"]) for event in resumed_events]
        assert resumed_types.count("memory.claim.promoted") == 1
        assert [str(event["data"]["event_type"]) for event in initial_events].count(
            "memory.observation.accepted"
        ) == 1

        async with admin_engine.connect() as connection:
            event_count = int(
                (
                    await connection.execute(
                        select(func.count())
                        .select_from(domain_events)
                        .where(domain_events.c.tenant_id == tenant_id)
                    )
                ).scalar_one()
            )
            outbox_rows = (
                (
                    await connection.execute(
                        select(outbox.c.event_id, outbox.c.topic, outbox.c.payload)
                        .where(outbox.c.tenant_id == tenant_id)
                        .order_by(outbox.c.id)
                    )
                )
                .mappings()
                .all()
            )
        assert len(outbox_rows) == event_count
        assert all(row["topic"] == "domain.events" for row in outbox_rows)
        assert {row["event_id"] for row in outbox_rows} == {
            row["payload"]["event_id"] for row in outbox_rows
        }
    finally:
        runtimes.close()
        core_engine.dispose()
        await app_engine.dispose()
        await admin_engine.dispose()


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


async def _ledger_counts(admin_engine, tenant_id: str) -> tuple[int, int]:
    async with admin_engine.connect() as connection:
        event_count = int(
            (
                await connection.execute(
                    select(func.count())
                    .select_from(domain_events)
                    .where(domain_events.c.tenant_id == tenant_id)
                )
            ).scalar_one()
        )
        outbox_count = int(
            (
                await connection.execute(
                    select(func.count()).select_from(outbox).where(outbox.c.tenant_id == tenant_id)
                )
            ).scalar_one()
        )
    return event_count, outbox_count


async def _install_outbox_failure(admin_engine, tenant_id: str) -> tuple[str, str]:
    suffix = uuid4().hex
    trigger_name = f"trg_test_outbox_{suffix}"
    function_name = f"fairy_test_fail_outbox_{suffix}"
    function_statement = f"""
    CREATE FUNCTION {function_name}()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $test$
    BEGIN
        IF NEW.tenant_id = '{tenant_id}' THEN
            RAISE EXCEPTION 'simulated outbox failure';
        END IF;
        RETURN NEW;
    END;
    $test$;
    """
    trigger_statement = (
        f"CREATE TRIGGER {trigger_name} BEFORE INSERT ON outbox "
        f"FOR EACH ROW EXECUTE FUNCTION {function_name}()"
    )
    async with admin_engine.begin() as connection:
        await connection.execute(text(function_statement))
        await connection.execute(text(trigger_statement))
    return trigger_name, function_name


async def _drop_outbox_failure(
    admin_engine,
    trigger_name: str,
    function_name: str,
) -> None:
    async with admin_engine.begin() as connection:
        await connection.execute(text(f"DROP TRIGGER IF EXISTS {trigger_name} ON outbox"))
        await connection.execute(text(f"DROP FUNCTION IF EXISTS {function_name}()"))
