from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fairy_core.domain.errors import VersionConflictError
from fairy_core.transports.stdio import build_local_service
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from fairy_cloud.api import create_cloud_app
from fairy_cloud.auth import RequestIdentity, StaticTokenAuthenticator
from fairy_cloud.storage.postgres import PostgresSyncStore, tenant_id_for_user
from fairy_cloud.storage.schema import version_candidates

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_offline_event_resume_dedup_and_two_device_version_conflict(
    tmp_path: Path,
    postgres_test_context,
) -> None:
    user_id = f"two-device-{uuid4().hex}"
    tenant_id = postgres_test_context.track_tenant(tenant_id_for_user(user_id))
    engine = create_async_engine(postgres_test_context.admin_dsn, pool_pre_ping=True)
    store = PostgresSyncStore(engine)
    local_service = build_local_service(tmp_path / "offline-device-a")
    try:
        conversation = local_service.invoke(
            "conversations.create",
            {"project_id": None, "workspace_type": "chat_scratch"},
        )
        task = local_service.invoke(
            "tasks.create",
            {
                "conversation_id": conversation["id"],
                "user_request": "Continue while cloud is offline",
                "operation_mode": "answer",
                "execution_target": "local",
                "idempotency_key": "offline:device-a:task",
            },
        )["task"]
        local_events = local_service.invoke("events.subscribe", {"cursor": 0})["items"]
        offline_event = next(
            event for event in reversed(local_events) if event["task_id"] == task["id"]
        )
        device_b_event = {
            **offline_event,
            "id": str(uuid4()),
            "task_sequence": offline_event["task_sequence"] + 1,
            "event_type": "assistant.message.delta",
            "message": "Assistant response updated",
            "payload": {
                "turn_id": str(uuid4()),
                "model_round": 1,
                "chunk_index": 1,
                "text": "synced from device b",
            },
        }
        app = create_cloud_app(
            local_service,
            authenticator=StaticTokenAuthenticator(
                {
                    "device-a-token": RequestIdentity(
                        user_id,
                        "device-a",
                        frozenset({"fairy.api"}),
                    ),
                    "device-b-token": RequestIdentity(
                        user_id,
                        "device-b",
                        frozenset({"fairy.api"}),
                    ),
                }
            ),
            sync_store=store,
        )

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            device_a_headers = {
                "Authorization": "Bearer device-a-token",
                "X-Fairy-Device-ID": "device-a",
            }
            device_b_headers = {
                "Authorization": "Bearer device-b-token",
                "X-Fairy-Device-ID": "device-b",
            }
            first = await client.post(
                "/v1/sync/events",
                json={"items": [offline_event]},
                headers=device_a_headers,
            )
            duplicate = await client.post(
                "/v1/sync/events",
                json={"items": [offline_event]},
                headers=device_a_headers,
            )
            second = await client.post(
                "/v1/sync/events",
                json={"items": [device_b_event]},
                headers=device_b_headers,
            )
            first.raise_for_status()
            duplicate.raise_for_status()
            second.raise_for_status()
            first_cursor = first.json()["accepted"][0]["cursor"]
            assert duplicate.json()["accepted"][0]["cursor"] == first_cursor

            resumed = await client.get(
                "/v1/events",
                params={"follow": False},
                headers={**device_b_headers, "Last-Event-ID": str(first_cursor)},
            )
            resumed.raise_for_status()

        frames = _parse_sse(resumed.text)
        assert [frame["id"] for frame in frames] == [str(second.json()["accepted"][0]["cursor"])]
        assert [frame["data"]["id"] for frame in frames] == [device_b_event["id"]]
        stored = await store.events_after(user_id=user_id, cursor=0)
        assert [event.device_id for event in stored] == ["device-a", "device-b"]
        assert await store.events_after(user_id=f"other-{user_id}", cursor=0) == []

        project_id = str(uuid4())
        version_a = str(uuid4())
        version_b = str(uuid4())
        await store.register_project(project_id=project_id, user_id=user_id)
        promoted = await store.promote_version(
            user_id=user_id,
            project_id=project_id,
            version_id=version_a,
            expected_revision=0,
            manifest={"device_id": "device-a", "snapshot": "a"},
        )
        assert promoted.revision == 1
        with pytest.raises(VersionConflictError):
            await store.promote_version(
                user_id=user_id,
                project_id=project_id,
                version_id=version_b,
                expected_revision=0,
                manifest={"device_id": "device-b", "snapshot": "b"},
            )
        async with engine.connect() as connection:
            candidates = (
                (
                    await connection.execute(
                        select(version_candidates.c.version_id).where(
                            version_candidates.c.tenant_id == tenant_id,
                            version_candidates.c.project_id == project_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert candidates == [version_b]
    finally:
        local_service.close()
        await engine.dispose()


def _parse_sse(body: str) -> list[dict[str, object]]:
    frames: list[dict[str, object]] = []
    for block in body.strip().split("\n\n"):
        if not block or block.startswith(":"):
            continue
        frame: dict[str, object] = {}
        for line in block.splitlines():
            name, _, value = line.partition(":")
            normalized = value.strip()
            frame[name] = json.loads(normalized) if name == "data" else normalized
        frames.append(frame)
    return frames
