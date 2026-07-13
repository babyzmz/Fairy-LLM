from __future__ import annotations

import base64
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from fairy_capabilities.documents import CompositeDocumentParser, ManagedFileDocumentStore
from fairy_core.application.service import CoreService
from fairy_core.commanding.settings import StaticSandboxHealthProvider
from fairy_core.contracts.methods import CORE_METHODS
from fairy_core.contracts.models import ErrorCode
from fairy_core.mcp.ports import McpError
from fairy_core.runtime.models import (
    ExecutorRuntimeState,
    RuntimeExecutorHealth,
    RuntimeProbeResult,
    RuntimeRecoveryTarget,
    RuntimeStartResult,
    RuntimeStopResult,
    StaticRuntimeStart,
)
from fairy_core.transports.stdio import build_local_service
from httpx import ASGITransport, AsyncClient

from fairy_cloud.api import EVENT_POLL_SECONDS, PUBLIC_ERROR_STATUS, create_cloud_app
from fairy_cloud.auth import RequestIdentity, StaticTokenAuthenticator
from fairy_cloud.dispatchers import TenantRuntimeRegistry

AUTH_HEADERS = {
    "Authorization": "Bearer test-token",
    "X-Fairy-Device-ID": "device-1",
}


def test_cloud_public_error_and_event_latency_contract_is_complete() -> None:
    assert {code.value for code in ErrorCode} <= PUBLIC_ERROR_STATUS.keys()
    assert EVENT_POLL_SECONDS <= 0.1
    assert PUBLIC_ERROR_STATUS[ErrorCode.PATH_OUT_OF_SCOPE.value] == 403
    assert PUBLIC_ERROR_STATUS[ErrorCode.SECRET_EGRESS_BLOCKED.value] == 403
    assert PUBLIC_ERROR_STATUS[ErrorCode.WORKER_INTERRUPTED.value] == 503


class HttpRuntimeExecutor:
    def __init__(self) -> None:
        self.probes: dict[str, RuntimeProbeResult] = {}

    def health(self) -> RuntimeExecutorHealth:
        return RuntimeExecutorHealth(
            available=True,
            executor="http_test_worker",
            version="1",
            error_code=None,
            diagnostics=(),
        )

    def start_static(self, request: StaticRuntimeStart) -> RuntimeStartResult:
        handle = f"static:{request.preview_id}"
        url = f"http://127.0.0.1:43125/{request.preview_id}/"
        result = RuntimeStartResult(
            executor_handle=handle,
            host="127.0.0.1",
            port=43125,
            url=url,
            state=ExecutorRuntimeState.RUNNING,
        )
        self.probes[handle] = RuntimeProbeResult(
            executor_handle=handle,
            state=ExecutorRuntimeState.RUNNING,
            host=result.host,
            port=result.port,
            url=result.url,
        )
        return result

    def probe(self, executor_handle: str) -> RuntimeProbeResult:
        return self.probes[executor_handle]

    def recovery_handle(self, target: RuntimeRecoveryTarget) -> str:
        return f"static:{target.preview_id}"

    def stop(self, executor_handle: str) -> RuntimeStopResult:
        current = self.probes[executor_handle]
        self.probes[executor_handle] = RuntimeProbeResult(
            executor_handle=executor_handle,
            state=ExecutorRuntimeState.STOPPED,
            host=current.host,
            port=current.port,
            url=current.url,
        )
        return RuntimeStopResult(stopped=True)


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
async def test_mcp_domain_errors_keep_stable_public_code_and_status() -> None:
    class FailingService:
        def invoke(self, _method: str, _params: dict[str, Any]) -> Any:
            raise McpError("MCP transport unavailable", error_code="MCP_UNAVAILABLE")

    identity = RequestIdentity("mcp-user", "mcp-device", frozenset({"fairy.api"}))
    direct_app = create_cloud_app(
        cast(CoreService, FailingService()),
        authenticator=StaticTokenAuthenticator({"mcp-token": identity}),
    )
    async with AsyncClient(
        transport=ASGITransport(app=direct_app),
        base_url="http://test",
        headers={
            "Authorization": "Bearer mcp-token",
            "X-Fairy-Device-ID": "mcp-device",
        },
    ) as client:
        response = await client.get("/v1/mcp/servers")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "MCP_UNAVAILABLE"


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
                    "workspace_id": "018f0f7c-1234-7000-8000-000000000001",
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
                    "workspace_id": "018f0f7c-1234-7000-8000-000000000001",
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
        build_local_service(
            tmp_path / "cloud",
            document_parser=CompositeDocumentParser(),
            document_blob_store=ManagedFileDocumentStore(tmp_path / "cloud" / "documents"),
            sandbox_health_provider=StaticSandboxHealthProvider(),
        ),
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
async def test_extension_routes_enforce_identity_and_replay_delete_tombstone(app) -> None:
    configure = {
        "server_id": "docs",
        "display_name": "Docs MCP",
        "transport": "streamable_http",
        "command": None,
        "arguments": [],
        "endpoint": "https://mcp.example.test/mcp",
        "credential_ref": None,
        "environment_refs": {},
        "expected_revision": 0,
        "idempotency_key": "http:mcp:configure:docs",
    }
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=AUTH_HEADERS,
    ) as client:
        skills = await client.get("/v1/skills")
        configured = await client.put(
            "/v1/mcp/servers/docs",
            headers={"Idempotency-Key": configure["idempotency_key"]},
            json=configure,
        )
        replayed = await client.put(
            "/v1/mcp/servers/docs",
            headers={"Idempotency-Key": configure["idempotency_key"]},
            json=configure,
        )
        mismatched = await client.put(
            "/v1/mcp/servers/other",
            headers={"Idempotency-Key": configure["idempotency_key"]},
            json=configure,
        )
        delete = {
            "server_id": "docs",
            "expected_revision": configured.json()["revision"],
            "idempotency_key": "http:mcp:delete:docs",
        }
        deleted = await client.request(
            "DELETE",
            "/v1/mcp/servers/docs",
            headers={"Idempotency-Key": delete["idempotency_key"]},
            json=delete,
        )
        delete_replay = await client.request(
            "DELETE",
            "/v1/mcp/servers/docs",
            headers={"Idempotency-Key": delete["idempotency_key"]},
            json=delete,
        )

    skills.raise_for_status()
    configured.raise_for_status()
    replayed.raise_for_status()
    deleted.raise_for_status()
    delete_replay.raise_for_status()
    assert skills.json() == {"items": []}
    assert replayed.json() == configured.json()
    assert mismatched.status_code == 409
    assert mismatched.json()["detail"]["code"] == "SCOPE_MISMATCH"
    assert deleted.json() == {"server_id": "docs", "deleted": True}
    assert delete_replay.json() == deleted.json()


@pytest.mark.asyncio
async def test_document_routes_share_the_task_scoped_core_contract(app) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=AUTH_HEADERS,
    ) as client:
        project_response = await client.post(
            "/v1/projects",
            json={"name": "Cloud documents", "residency": "local_only"},
        )
        project_response.raise_for_status()
        project = project_response.json()["project"]
        conversation_response = await client.post(
            "/v1/conversations",
            json={"project_id": project["id"], "workspace_type": "project_chat"},
        )
        conversation_response.raise_for_status()
        task_response = await client.post(
            "/v1/tasks",
            json={
                "conversation_id": conversation_response.json()["id"],
                "user_request": "Search managed documents",
                "operation_mode": "continue_current_chat_draft",
                "execution_target": "local",
                "idempotency_key": "http:documents:task",
            },
        )
        task_response.raise_for_status()
        task = task_response.json()["task"]
        imported_response = await client.post(
            "/v1/documents/import",
            headers={"Idempotency-Key": "http:documents:import"},
            json={
                "task_id": task["id"],
                "filename": "evidence.txt",
                "media_type": "text/plain",
                "content_base64": base64.b64encode(b"Cloud document sentinel").decode(),
                "visibility": "conversation",
                "idempotency_key": "http:documents:import",
                "user_confirmed": True,
            },
        )
        imported_response.raise_for_status()
        imported = imported_response.json()
        document_id = imported["document"]["id"]

        listed = await client.get("/v1/documents", params={"task_id": task["id"]})
        fetched = await client.get(
            f"/v1/documents/{document_id}",
            params={"task_id": task["id"]},
        )
        searched = await client.post(
            "/v1/documents/search",
            json={"task_id": task["id"], "query": "sentinel", "limit": 10},
        )
        deleted = await client.post(
            f"/v1/documents/{document_id}/delete",
            headers={"Idempotency-Key": "http:documents:delete"},
            json={
                "task_id": task["id"],
                "document_id": document_id,
                "idempotency_key": "http:documents:delete",
                "user_confirmed": True,
            },
        )

        for response in (listed, fetched, searched, deleted):
            response.raise_for_status()
        assert listed.json()["items"] == [imported]
        assert fetched.json() == imported
        assert searched.json()["items"][0]["document"] == imported["document"]
        assert deleted.json()["document"]["status"] == "deleted"
        assert "storage_location" not in imported["document"]


@pytest.mark.asyncio
async def test_rest_exposes_task_bound_assistant_ledger_with_idempotency_header(app) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=AUTH_HEADERS,
    ) as client:
        conversation_response = await client.post(
            "/v1/conversations",
            json={"project_id": None, "workspace_type": "chat_scratch"},
        )
        conversation_response.raise_for_status()
        conversation_id = conversation_response.json()["id"]
        task_response = await client.post(
            "/v1/tasks",
            json={
                "conversation_id": conversation_id,
                "user_request": "Explain Fairy",
                "operation_mode": "answer",
                "execution_target": "cloud",
                "idempotency_key": "http:assistant:task",
            },
        )
        task_response.raise_for_status()
        task = task_response.json()["task"]
        body = {
            "task_id": task["id"],
            "profile_id": "cloud-default",
            "idempotency_key": "http:assistant:turn",
        }

        missing_header = await client.post("/v1/assistant/turns", json=body)
        mismatched_header = await client.post(
            "/v1/assistant/turns",
            headers={"Idempotency-Key": "different"},
            json=body,
        )
        created_response = await client.post(
            "/v1/assistant/turns",
            headers={"Idempotency-Key": body["idempotency_key"]},
            json=body,
        )
        created_response.raise_for_status()
        created = created_response.json()
        fetched = await client.get(f"/v1/assistant/turns/{created['id']}")
        messages = await client.get(
            "/v1/messages",
            params={"conversation_id": conversation_id},
        )
        cancelled = await client.post(
            f"/v1/assistant/turns/{created['id']}/cancel",
            json={
                "turn_id": created["id"],
                "expected_cancellation_revision": 0,
            },
        )
        stale_cancel = await client.post(
            f"/v1/assistant/turns/{created['id']}/cancel",
            json={
                "turn_id": created["id"],
                "expected_cancellation_revision": 0,
            },
        )
        started_terminal = await client.post(
            f"/v1/assistant/turns/{created['id']}/start",
            json={"turn_id": created["id"]},
        )
        retry_body = {
            "turn_id": created["id"],
            "idempotency_key": "http:assistant:turn:retry",
        }
        retried = await client.post(
            f"/v1/assistant/turns/{created['id']}/retry",
            headers={"Idempotency-Key": retry_body["idempotency_key"]},
            json=retry_body,
        )
        retried.raise_for_status()
        run = await client.post(
            f"/v1/assistant/turns/{retried.json()['id']}/run",
            json={"turn_id": retried.json()["id"]},
        )

    assert missing_header.status_code == 422
    assert mismatched_header.status_code == 409
    assert mismatched_header.json()["detail"]["code"] == "SCOPE_MISMATCH"
    assert fetched.json() == created
    assert [(item["role"], item["content"]) for item in messages.json()["items"]] == [
        ("user", "Explain Fairy")
    ]
    assert cancelled.json()["status"] == "cancelled"
    assert stale_cancel.status_code == 409
    assert stale_cancel.json()["detail"]["code"] == "INVALID_STATE_TRANSITION"
    assert started_terminal.status_code == 200
    assert started_terminal.json()["status"] == "cancelled"
    assert retried.json()["status"] == "created"
    assert run.status_code == 200
    assert run.json()["status"] == "failed"
    assert run.json()["error_code"] == "PROVIDER_UNAVAILABLE"


@pytest.mark.asyncio
async def test_rest_exposes_collection_runtime_preview_and_artifact_contracts(
    tmp_path: Path,
) -> None:
    identity = RequestIdentity("user", "device-1", frozenset({"fairy.api"}))
    app = create_cloud_app(
        build_local_service(
            tmp_path / "runtime-http",
            runtime_executor=HttpRuntimeExecutor(),
        ),
        authenticator=StaticTokenAuthenticator({"test-token": identity}),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=AUTH_HEADERS,
    ) as client:
        project = (
            await client.post(
                "/v1/projects",
                json={"name": "Runtime HTTP", "residency": "local_only"},
            )
        ).json()
        project_id = project["project"]["id"]
        projects = await client.get("/v1/projects", params={"limit": 1})
        conversation = (
            await client.post(
                "/v1/conversations",
                json={"project_id": project_id, "workspace_type": "project_chat"},
            )
        ).json()
        conversation_id = conversation["id"]
        fetched_conversation = await client.get(f"/v1/conversations/{conversation_id}")
        conversations = await client.get(
            "/v1/conversations",
            params={"project_id": project_id},
        )
        task = (
            await client.post(
                "/v1/tasks",
                json={
                    "conversation_id": conversation_id,
                    "user_request": "Build preview",
                    "operation_mode": "continue_current_chat_draft",
                    "execution_target": "local",
                    "idempotency_key": "http:runtime:task",
                },
            )
        ).json()["task"]
        task_id = task["id"]
        pending = (
            await client.post(
                "/v1/changesets",
                json={
                    "task_id": task_id,
                    "files": [{"path": "index.html", "content": "<h1>HTTP</h1>"}],
                    "reason": "Preview entry",
                    "idempotency_key": "http:runtime:changeset",
                },
            )
        ).json()
        approval = pending["approval"]
        decided = await client.post(
            f"/v1/approvals/{approval['id']}/decision",
            json={
                "approval_id": approval["id"],
                "approved": True,
            },
        )
        tasks = await client.get("/v1/tasks", params={"conversation_id": conversation_id})
        versions = await client.get("/v1/versions", params={"project_id": project_id})
        approvals = await client.get("/v1/approvals", params={"task_id": task_id})
        missing_idempotency = await client.post(
            "/v1/previews/start",
            json={"task_id": task_id, "idempotency_key": "http:preview:start"},
        )
        mismatched_idempotency = await client.post(
            "/v1/previews/start",
            headers={"Idempotency-Key": "different"},
            json={"task_id": task_id, "idempotency_key": "http:preview:start"},
        )
        started = await client.post(
            "/v1/previews/start",
            headers={"Idempotency-Key": "http:preview:start"},
            json={"task_id": task_id, "idempotency_key": "http:preview:start"},
        )
        started.raise_for_status()
        preview = started.json()["preview"]
        runtime = started.json()["runtime"]
        fetched_runtime = await client.get(f"/v1/runtimes/{runtime['id']}")
        runtime_health = await client.get("/v1/runtimes/health", params={"task_id": task_id})
        fetched_preview = await client.get(f"/v1/previews/{preview['id']}")
        resolved_preview = await client.get(
            "/v1/previews/resolve",
            params={"conversation_id": conversation_id},
        )
        artifacts = await client.get("/v1/artifacts", params={"task_id": task_id})
        missing_artifact = await client.get("/v1/artifacts/018f0f7c-1234-7000-8000-000000000099")
        stopped = await client.post(
            f"/v1/previews/{preview['id']}/stop",
            headers={"Idempotency-Key": "http:preview:stop"},
            json={
                "preview_id": preview["id"],
                "idempotency_key": "http:preview:stop",
            },
        )

    for response in (
        projects,
        fetched_conversation,
        conversations,
        decided,
        tasks,
        versions,
        approvals,
        fetched_runtime,
        runtime_health,
        fetched_preview,
        resolved_preview,
        artifacts,
        stopped,
    ):
        response.raise_for_status()
    assert projects.json()["items"][0]["id"] == project_id
    assert fetched_conversation.json()["id"] == conversation_id
    assert task_id in {item["id"] for item in tasks.json()["items"]}
    assert approval["id"] in {item["id"] for item in approvals.json()["items"]}
    assert len(versions.json()["items"]) == 2
    assert missing_idempotency.status_code == 422
    assert mismatched_idempotency.status_code == 409
    assert mismatched_idempotency.json()["detail"]["code"] == "SCOPE_MISMATCH"
    assert fetched_runtime.json()["id"] == runtime["id"]
    assert runtime_health.json()["executor"]["available"] is True
    assert fetched_preview.json()["preview"]["id"] == preview["id"]
    assert resolved_preview.json()["preview"]["id"] == preview["id"]
    artifact_items = artifacts.json()["items"]
    assert len(artifact_items) == 1
    assert artifact_items[0]["artifact_type"] == "preview_manifest"
    assert artifact_items[0]["task_id"] == task_id
    assert artifact_items[0]["metadata"]["preview_id"] == preview["id"]
    assert missing_artifact.status_code == 404
    assert stopped.json()["status"] == "stopped"


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
async def test_provider_contracts_are_available_over_rest(app) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=AUTH_HEADERS,
    ) as client:
        profiles = await client.get("/v1/providers")
        health = await client.get("/v1/providers/health")

    assert profiles.status_code == 200
    assert profiles.json() == {"items": []}
    assert health.status_code == 200
    assert health.json() == {"items": []}


@pytest.mark.asyncio
async def test_cloud_capabilities_use_revision_fenced_core_permissions(app) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=AUTH_HEADERS,
    ) as client:
        original = await client.get("/v1/permissions")
        changed = await client.put(
            "/v1/permissions",
            headers={"Idempotency-Key": "permissions:http:autonomous"},
            json={
                "profile": "autonomous",
                "capability_overrides": {"preview.start": False},
                "expected_revision": 0,
                "idempotency_key": "permissions:http:autonomous",
            },
        )
        capabilities = await client.get("/v1/capabilities")
        missing_key = await client.put(
            "/v1/permissions",
            json={
                "profile": "observe",
                "capability_overrides": {},
                "expected_revision": 1,
                "idempotency_key": "permissions:http:observe",
            },
        )
        forged = await client.post(
            "/v1/capabilities",
            json={"profile": "autonomous", "sandbox_healthy": True},
        )

    original.raise_for_status()
    changed.raise_for_status()
    capabilities.raise_for_status()
    assert original.json()["profile"] == "standard"
    assert changed.json()["revision"] == 1
    assert capabilities.json()["profile"] == "autonomous"
    assert capabilities.json()["sandbox_healthy"] is False
    assert capabilities.json()["operations"]["preview.start"] is False
    assert missing_key.status_code == 422
    assert forged.status_code == 405


@pytest.mark.asyncio
async def test_memory_routes_are_task_scoped_and_share_core_contracts(app) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=AUTH_HEADERS,
    ) as client:
        project = (
            await client.post(
                "/v1/projects",
                json={"name": "Memory HTTP", "residency": "synced"},
            )
        ).json()
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
                "user_request": "Remember the framework",
                "operation_mode": "continue_current_chat_draft",
                "execution_target": "cloud",
                "idempotency_key": "memory:http:task",
            },
        )
        task_response.raise_for_status()
        task_id = task_response.json()["task"]["id"]

        forged = await client.post(
            "/v1/memory/observations",
            json={
                "task_id": task_id,
                "content": "The project uses React Aria.",
                "idempotency_key": "memory:http:forged",
                "project_id": project["project"]["id"],
            },
        )
        observed = await client.post(
            "/v1/memory/observations",
            json={
                "task_id": task_id,
                "content": "The project uses React Aria.",
                "idempotency_key": "memory:http:observe",
            },
        )
        observed.raise_for_status()
        observations = await client.get(
            "/v1/memory/observations",
            params={"task_id": task_id, "namespace": "conversation_draft"},
        )
        observations.raise_for_status()
        promote_payload = {
            "task_id": task_id,
            "observation_id": observed.json()["id"],
            "subject": "project",
            "predicate": "accessibility_framework",
            "value": "React Aria",
            "normalized_text": "react aria",
            "user_confirmed": False,
            "idempotency_key": "memory:http:promote",
        }
        approval_required = await client.post(
            "/v1/memory/claims/promote",
            json=promote_payload,
        )
        promoted = await client.post(
            "/v1/memory/claims/promote",
            json={**promote_payload, "user_confirmed": True},
        )
        promoted.raise_for_status()
        inspected = await client.get(
            f"/v1/memory/claims/{promoted.json()['claim']['id']}",
            params={"task_id": task_id},
        )
        inspected.raise_for_status()

    assert forged.status_code == 422
    assert observations.json()["items"] == [observed.json()]
    assert approval_required.status_code == 409
    assert approval_required.json()["detail"]["code"] == "APPROVAL_REQUIRED"
    assert inspected.json()["current_revision"]["revision"] == 1


@pytest.mark.asyncio
async def test_memory_retrieval_routes_share_task_scoped_core_contracts(app) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=AUTH_HEADERS,
    ) as client:
        project = (
            await client.post(
                "/v1/projects",
                json={"name": "Memory retrieval", "residency": "synced"},
            )
        ).json()
        conversation = (
            await client.post(
                "/v1/conversations",
                json={
                    "project_id": project["project"]["id"],
                    "workspace_type": "project_chat",
                },
            )
        ).json()
        task_response = await client.post(
            "/v1/tasks",
            json={
                "conversation_id": conversation["id"],
                "user_request": "inspect memory",
                "operation_mode": "continue_current_chat_draft",
                "execution_target": "cloud",
                "idempotency_key": "memory:retrieval:http:task",
            },
        )
        task_response.raise_for_status()
        task = task_response.json()["task"]

        search = await client.get(
            "/v1/memory/search",
            params={"task_id": task["id"], "query": "memory", "limit": 10},
        )
        snapshot = await client.get(
            f"/v1/memory/snapshots/{task['memory_snapshot_id']}",
            params={"task_id": task["id"]},
        )
        health = await client.get(
            "/v1/memory/projection/health",
            params={"task_id": task["id"]},
        )
        invalid_limit = await client.get(
            "/v1/memory/search",
            params={"task_id": task["id"], "query": "memory", "limit": 101},
        )
        injected_scope = await client.get(
            "/v1/memory/search",
            params={
                "task_id": task["id"],
                "query": "memory",
                "project_id": project["project"]["id"],
            },
        )
        forged = await client.get(
            "/v1/memory/snapshots/018f0f7c-1234-7000-8000-000000000099",
            params={"task_id": task["id"]},
        )

    search.raise_for_status()
    snapshot.raise_for_status()
    health.raise_for_status()
    assert search.json() == {"items": []}
    assert snapshot.json()["id"] == task["memory_snapshot_id"]
    assert health.json()["lag"] >= 0
    assert invalid_limit.status_code == 422
    assert injected_scope.status_code == 422
    assert forged.status_code == 409
    assert forged.json()["detail"]["code"] == "MEMORY_SCOPE_VIOLATION"


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
