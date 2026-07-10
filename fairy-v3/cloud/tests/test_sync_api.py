from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fairy_core.domain.errors import IdempotencyConflictError
from fairy_core.domain.ids import new_id
from fairy_core.transports.stdio import build_local_dispatcher
from httpx import ASGITransport, AsyncClient

from fairy_cloud.api import create_cloud_app
from fairy_cloud.auth import RequestIdentity, StaticTokenAuthenticator
from fairy_cloud.storage.objects import S3ObjectStore
from fairy_cloud.sync.memory import MemorySyncStore

AUTH_HEADERS = {
    "Authorization": "Bearer sync-token",
    "X-Fairy-Device-ID": "device-a",
}


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], dict] = {}

    def put_object(self, **request):
        identity = (request["Bucket"], request["Key"])
        if identity in self.objects:
            error = RuntimeError("PreconditionFailed")
            error.response = {"Error": {"Code": "PreconditionFailed"}}
            raise error
        self.objects[identity] = request

    def head_object(self, **request):
        stored = self.objects[(request["Bucket"], request["Key"])]
        return {
            "ContentLength": len(stored["Body"]),
            "Metadata": stored["Metadata"],
        }


@pytest.fixture
def sync_app(tmp_path: Path):
    identity = RequestIdentity(
        user_id="user-a",
        device_id="device-a",
        scopes=frozenset({"fairy.api"}),
    )
    sync_store = MemorySyncStore()
    app = create_cloud_app(
        build_local_dispatcher(tmp_path / "sync-core"),
        authenticator=StaticTokenAuthenticator({"sync-token": identity}),
        sync_store=sync_store,
        object_store=S3ObjectStore(client=FakeS3Client(), bucket="fairy-objects"),
    )
    return app, sync_store


@pytest.mark.asyncio
async def test_uploaded_events_are_identity_bound_and_resumable(sync_app) -> None:
    app, sync_store = sync_app
    event = _event_payload()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=AUTH_HEADERS,
    ) as client:
        first = await client.post("/v1/sync/events", json={"items": [event]})
        duplicate = await client.post("/v1/sync/events", json={"items": [event]})
        changed_event = {**event, "payload": {"status": "changed"}}
        changed = await client.post("/v1/sync/events", json={"items": [changed_event]})
        stream = await client.get("/v1/events", params={"follow": False})
        resumed = await client.get(
            "/v1/events",
            params={"follow": False},
            headers={"Last-Event-ID": "1"},
        )

    first.raise_for_status()
    duplicate.raise_for_status()
    stream.raise_for_status()
    resumed.raise_for_status()
    assert first.json()["accepted"] == [{"event_id": event["id"], "cursor": 1}]
    assert duplicate.json()["accepted"] == [{"event_id": event["id"], "cursor": 1}]
    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"
    stored = await sync_store.events_after(user_id="user-a", cursor=0)
    assert len(stored) == 1
    assert stored[0].device_id == "device-a"
    streamed = _parse_sse(stream.text)
    assert streamed[0]["id"] == "1"
    assert streamed[0]["data"]["id"] == event["id"]
    assert _parse_sse(resumed.text) == []


@pytest.mark.asyncio
async def test_memory_sync_store_scopes_same_ids_by_tenant() -> None:
    store = MemorySyncStore()
    await store.register_project(project_id="shared-project", user_id="user-a")
    await store.register_project(project_id="shared-project", user_id="user-b")

    cursor_a = await store.append_event(
        event_id="shared-event",
        user_id="user-a",
        device_id="device-a",
        project_id="shared-project",
        task_id="shared-task",
        task_sequence=1,
        schema_version=1,
        event_type="task.created",
        payload={"owner": "a"},
    )
    cursor_b = await store.append_event(
        event_id="shared-event",
        user_id="user-b",
        device_id="device-b",
        project_id="shared-project",
        task_id="shared-task",
        task_sequence=1,
        schema_version=1,
        event_type="task.created",
        payload={"owner": "b"},
    )

    assert cursor_b > cursor_a
    assert [event.payload for event in await store.events_after(user_id="user-a", cursor=0)] == [
        {"owner": "a"}
    ]
    assert [event.payload for event in await store.events_after(user_id="user-b", cursor=0)] == [
        {"owner": "b"}
    ]
    with pytest.raises(IdempotencyConflictError, match="task sequence"):
        await store.append_event(
            event_id="different-event",
            user_id="user-a",
            device_id="device-a",
            project_id="shared-project",
            task_id="shared-task",
            task_sequence=1,
            schema_version=1,
            event_type="task.updated",
            payload={"owner": "a"},
        )


@pytest.mark.asyncio
async def test_internal_events_never_enter_work_presence(sync_app) -> None:
    app, sync_store = sync_app
    event = _event_payload()
    event["visibility"] = "internal"
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=AUTH_HEADERS,
    ) as client:
        uploaded = await client.post("/v1/sync/events", json={"items": [event]})
        stream = await client.get("/v1/events", params={"follow": False})

    uploaded.raise_for_status()
    stream.raise_for_status()
    assert _parse_sse(stream.text) == []
    internal = await sync_store.events_after(
        user_id="user-a",
        cursor=0,
        visibilities=frozenset({"internal"}),
    )
    assert [item.event_id for item in internal] == [event["id"]]


@pytest.mark.asyncio
async def test_snapshot_promotion_keeps_stale_device_version_as_candidate(sync_app) -> None:
    app, sync_store = sync_app
    project_id = str(new_id())
    version_a = str(new_id())
    version_b = str(new_id())
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=AUTH_HEADERS,
    ) as client:
        registered = await client.post(
            "/v1/sync/projects",
            json={"project_id": project_id, "active_version_id": None},
        )
        snapshot_a = await client.put(
            f"/v1/sync/projects/{project_id}/versions/{version_a}/snapshot",
            content=b"snapshot-a",
            headers={"Content-Type": "application/zstd"},
        )
        snapshot_b = await client.put(
            f"/v1/sync/projects/{project_id}/versions/{version_b}/snapshot",
            content=b"snapshot-b",
            headers={"Content-Type": "application/zstd"},
        )
        manifest_a = _manifest(snapshot_a.json(), expected_revision=0)
        promoted = await client.post(
            f"/v1/sync/projects/{project_id}/versions/{version_a}/promote",
            json=manifest_a,
        )
        retried = await client.post(
            f"/v1/sync/projects/{project_id}/versions/{version_a}/promote",
            json=manifest_a,
        )
        conflict = await client.post(
            f"/v1/sync/projects/{project_id}/versions/{version_b}/promote",
            json=_manifest(snapshot_b.json(), expected_revision=0),
        )

    registered.raise_for_status()
    snapshot_a.raise_for_status()
    snapshot_b.raise_for_status()
    promoted.raise_for_status()
    retried.raise_for_status()
    assert promoted.json()["revision"] == 1
    assert retried.json()["revision"] == 1
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "VERSION_CONFLICT"
    state = sync_store.project_state(project_id)
    assert state.active_version_id == version_a
    assert state.candidate_version_ids == [version_b]
    decision_events = await sync_store.events_after(user_id="user-a", cursor=0)
    assert [event.event_type for event in decision_events] == [
        "version.promoted",
        "version.candidate_retained",
    ]
    assert all("created_at" not in event.payload for event in decision_events)


def _event_payload() -> dict[str, object]:
    return {
        "id": str(new_id()),
        "cursor": 41,
        "run_id": str(new_id()),
        "project_id": str(new_id()),
        "conversation_id": str(new_id()),
        "task_id": str(new_id()),
        "version_id": str(new_id()),
        "task_sequence": 1,
        "event_type": "task.created",
        "visibility": "user",
        "message": "Task created",
        "payload": {"status": "planning"},
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
    }


def _manifest(snapshot: dict[str, object], *, expected_revision: int) -> dict[str, object]:
    return {
        "expected_revision": expected_revision,
        "base_version_id": None,
        "snapshot_key": snapshot["key"],
        "snapshot_sha256": snapshot["sha256"],
        "snapshot_size": snapshot["size"],
        "files_digest": "a" * 64,
        "decision_event_id": str(new_id()),
        "conversation_id": str(new_id()),
        "task_id": str(new_id()),
        "task_sequence": 1,
    }


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
