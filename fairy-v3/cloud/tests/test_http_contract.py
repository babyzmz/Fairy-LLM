from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from fairy_core.application.service import CoreService
from fairy_core.contracts.methods import CORE_METHODS
from fairy_core.transports.stdio import build_local_service
from httpx import ASGITransport, AsyncClient

from fairy_cloud.api import create_cloud_app
from fairy_cloud.auth import RequestIdentity, StaticTokenAuthenticator
from fairy_cloud.dispatchers import TenantRuntimeRegistry

AUTH_HEADERS = {
    "Authorization": "Bearer test-token",
    "X-Fairy-Device-ID": "device-1",
}


@pytest.mark.asyncio
async def test_fastapi_invokes_core_service_without_jsonrpc_envelope() -> None:
    class RecordingService:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        def invoke(self, method: str, params: dict[str, Any]) -> Any:
            self.calls.append((method, params))
            return {"status": "ok", "service": "fake", "protocol": "core-service-v1"}

    service = RecordingService()
    direct_app = create_cloud_app(cast(CoreService, service))
    async with AsyncClient(
        transport=ASGITransport(app=direct_app),
        base_url="http://test",
    ) as client:
        response = await client.get("/v1/health")

    response.raise_for_status()
    assert service.calls == [("health", {})]


@pytest.mark.asyncio
async def test_async_core_routes_run_sync_service_in_threadpool() -> None:
    class ThreadRecordingService:
        def __init__(self) -> None:
            self.thread_id: int | None = None

        def invoke(self, method: str, _params: dict[str, Any]) -> Any:
            assert method == "projects.create"
            self.thread_id = threading.get_ident()
            now = datetime.now(UTC).isoformat()
            return {
                "project": {
                    "id": "018f0f7c-1234-7000-8000-000000000001",
                    "name": "Threaded",
                    "residency": "synced",
                    "active_version_id": "018f0f7c-1234-7000-8000-000000000002",
                    "active_preview_id": None,
                    "revision": 1,
                    "created_at": now,
                    "updated_at": now,
                },
                "initial_version": {
                    "id": "018f0f7c-1234-7000-8000-000000000002",
                    "project_id": "018f0f7c-1234-7000-8000-000000000001",
                    "source_conversation_id": None,
                    "source_task_id": None,
                    "parent_version_id": None,
                    "project_root": "/managed/project",
                    "visibility": "project_active",
                    "created_at": now,
                },
            }

    identity = RequestIdentity("user", "device", frozenset({"fairy.api"}))
    service = ThreadRecordingService()
    direct_app = create_cloud_app(
        cast(CoreService, service),
        authenticator=StaticTokenAuthenticator({"thread-token": identity}),
    )
    event_loop_thread = threading.get_ident()
    async with AsyncClient(
        transport=ASGITransport(app=direct_app),
        base_url="http://test",
        headers={"Authorization": "Bearer thread-token", "X-Fairy-Device-ID": "device"},
    ) as client:
        response = await client.post(
            "/v1/projects",
            json={"name": "Threaded", "residency": "synced"},
        )

    response.raise_for_status()
    assert service.thread_id is not None
    assert service.thread_id != event_loop_thread


@pytest.fixture
def app(tmp_path: Path):
    identity = RequestIdentity(
        user_id="user-1",
        device_id="device-1",
        scopes=frozenset({"fairy.api"}),
    )
    return create_cloud_app(
        build_local_service(tmp_path / "cloud"),
        authenticator=StaticTokenAuthenticator({"test-token": identity}),
    )


@pytest.mark.asyncio
async def test_rest_runs_the_same_project_contract_as_local_jsonrpc(app) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=AUTH_HEADERS,
    ) as client:
        project_response = await client.post(
            "/v1/projects",
            json={"name": "Cloud project", "residency": "synced"},
        )
        project_response.raise_for_status()
        project = project_response.json()
        conversation_response = await client.post(
            "/v1/conversations",
            json={
                "project_id": project["project"]["id"],
                "workspace_type": "project_chat",
            },
        )
        conversation_response.raise_for_status()
        task_response = await client.post(
            "/v1/tasks",
            json={
                "conversation_id": conversation_response.json()["id"],
                "user_request": "Prepare a cloud draft",
                "operation_mode": "continue_current_chat_draft",
                "execution_target": "cloud",
                "idempotency_key": "cloud:task-1",
            },
        )
        task_response.raise_for_status()

    task = task_response.json()
    assert project["project"]["residency"] == "synced"
    assert task["task"]["status"] == "planning"
    assert task["scope"]["execution_target"] == "cloud"
    assert task["scope"]["scope_digest"]


@pytest.mark.asyncio
async def test_sse_uses_cursor_as_event_id_and_resumes_without_duplicates(app) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=AUTH_HEADERS,
    ) as client:
        response = await client.post(
            "/v1/projects",
            json={"name": "Events", "residency": "synced"},
        )
        response.raise_for_status()
        first_stream = await client.get("/v1/events", params={"cursor": 0, "follow": False})
        first_stream.raise_for_status()
        first_events = _parse_sse(first_stream.text)
        resume_cursor = first_events[0]["id"]
        resumed_stream = await client.get(
            "/v1/events",
            params={"cursor": 0, "follow": False},
            headers={"Last-Event-ID": resume_cursor},
        )
        resumed_stream.raise_for_status()
        resumed_events = _parse_sse(resumed_stream.text)

    assert first_stream.headers["content-type"].startswith("text/event-stream")
    assert first_events[0]["data"]["cursor"] == int(first_events[0]["id"])
    assert all(int(event["id"]) > int(resume_cursor) for event in resumed_events)
    assert len({event["id"] for event in first_events}) == len(first_events)


def test_openapi_declares_native_typed_sse(app) -> None:
    schema = app.openapi()

    event_response = schema["paths"]["/v1/events"]["get"]["responses"]["200"]
    assert "text/event-stream" in event_response["content"]
    assert "HTTPBearer" in schema["components"]["securitySchemes"]

    operation_ids = {
        operation["operationId"]
        for path in schema["paths"].values()
        for operation in path.values()
        if isinstance(operation, dict) and "operationId" in operation
    }
    assert set(CORE_METHODS) <= operation_ids


@pytest.mark.asyncio
async def test_commands_and_event_stream_fail_closed_without_identity(app) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        health = await client.get("/v1/health")
        command = await client.post(
            "/v1/projects",
            json={"name": "Unauthorized", "residency": "synced"},
        )
        events = await client.get("/v1/events", params={"follow": False})

    assert health.status_code == 200
    assert command.status_code == 401
    assert events.status_code == 401


@pytest.mark.asyncio
async def test_cloud_capability_request_preserves_advanced_overrides(app) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=AUTH_HEADERS,
    ) as client:
        response = await client.post(
            "/v1/capabilities",
            json={
                "profile": "standard",
                "sandbox_healthy": False,
                "overrides": {"preview.start": False},
            },
        )

    response.raise_for_status()
    assert response.json()["operations"]["preview.start"] is False


@pytest.mark.asyncio
async def test_authenticated_commands_resolve_an_isolated_tenant_core(tmp_path: Path) -> None:
    identities = {
        "token-a": RequestIdentity("user-a", "device-a", frozenset({"fairy.api"})),
        "token-b": RequestIdentity("user-b", "device-b", frozenset({"fairy.api"})),
    }

    registry = TenantRuntimeRegistry(
        root=tmp_path / "tenant-cores",
        engine=cast(Any, object()),
        builder=lambda _tenant_id, path, _engine: build_local_service(path),
    )
    app = create_cloud_app(
        build_local_service(tmp_path / "system"),
        authenticator=StaticTokenAuthenticator(identities),
        service_resolver=registry.for_identity,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response_a = await client.post(
            "/v1/projects",
            headers={"Authorization": "Bearer token-a", "X-Fairy-Device-ID": "device-a"},
            json={"name": "A", "residency": "synced"},
        )
        response_b = await client.post(
            "/v1/projects",
            headers={"Authorization": "Bearer token-b", "X-Fairy-Device-ID": "device-b"},
            json={"name": "B", "residency": "synced"},
        )

    response_a.raise_for_status()
    response_b.raise_for_status()
    project_a = response_a.json()["project"]["id"]
    project_b = response_b.json()["project"]["id"]
    service_a = registry.for_identity(identities["token-a"])
    own_project = service_a.invoke("projects.get", {"project_id": project_a})

    assert own_project["id"] == project_a
    with pytest.raises(KeyError, match="project not found"):
        service_a.invoke("projects.get", {"project_id": project_b})


def _parse_sse(payload: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for block in payload.strip().split("\n\n"):
        fields = {}
        for line in block.splitlines():
            name, value = line.split(":", 1)
            fields[name] = value.strip()
        if "data" in fields:
            fields["data"] = json.loads(fields["data"])
            events.append(fields)
    return events
