from __future__ import annotations

import json
from pathlib import Path

import pytest
from fairy_core.transports.stdio import build_local_dispatcher
from httpx import ASGITransport, AsyncClient

from fairy_cloud.api import create_cloud_app
from fairy_cloud.auth import RequestIdentity, StaticTokenAuthenticator

AUTH_HEADERS = {
    "Authorization": "Bearer test-token",
    "X-Fairy-Device-ID": "device-1",
}


@pytest.fixture
def app(tmp_path: Path):
    identity = RequestIdentity(
        user_id="user-1",
        device_id="device-1",
        scopes=frozenset({"fairy.api"}),
    )
    return create_cloud_app(
        build_local_dispatcher(tmp_path / "cloud"),
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
async def test_authenticated_commands_resolve_an_isolated_tenant_core(tmp_path: Path) -> None:
    identities = {
        "token-a": RequestIdentity("user-a", "device-a", frozenset({"fairy.api"})),
        "token-b": RequestIdentity("user-b", "device-b", frozenset({"fairy.api"})),
    }

    class TenantDispatcher:
        def __init__(self, user_id: str) -> None:
            self.user_id = user_id

        def dispatch(self, _request):
            return {"jsonrpc": "2.0", "id": 1, "result": {"tenant": self.user_id}}

    app = create_cloud_app(
        build_local_dispatcher(tmp_path / "system"),
        authenticator=StaticTokenAuthenticator(identities),
        dispatcher_resolver=lambda identity: TenantDispatcher(identity.user_id),
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

    assert response_a.json() == {"tenant": "user-a"}
    assert response_b.json() == {"tenant": "user-b"}


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
