from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from fairy_core.application.service import CoreService
from fairy_core.commanding.registry import build_default_registry
from fairy_core.domain.execution import Artifact, ArtifactType, ArtifactVisibility
from fairy_core.transports.jsonrpc import JsonRpcDispatcher
from fairy_core.transports.stdio import build_local_dispatcher
from tests.runtime_support import build_runtime_stack


def _dispatcher(tmp_path: Path) -> JsonRpcDispatcher:
    return build_local_dispatcher(tmp_path)


def _call(dispatcher: JsonRpcDispatcher, request_id: int, method: str, params: dict) -> dict:
    return dispatcher.dispatch(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
    )


def test_jsonrpc_transport_invokes_one_core_service_without_own_handlers() -> None:
    class RecordingService:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        def invoke(self, method: str, params: dict[str, Any]) -> Any:
            self.calls.append((method, params))
            return {"status": "ok", "service": "fake", "protocol": "core-service-v1"}

    service = RecordingService()
    dispatcher = JsonRpcDispatcher(cast(CoreService, service))

    response = _call(dispatcher, 7, "health", {})

    assert service.calls == [("health", {})]
    assert response["result"]["service"] == "fake"


def test_jsonrpc_transport_contains_unexpected_errors_and_remains_available() -> None:
    class RecoveringService:
        def __init__(self) -> None:
            self.failed = False

        def invoke(self, method: str, params: dict[str, Any]) -> Any:
            if not self.failed:
                self.failed = True
                raise RuntimeError("unexpected command failure")
            return {"status": "ok", "service": "fairy-core", "protocol": "core-service-v1"}

    dispatcher = JsonRpcDispatcher(cast(CoreService, RecoveringService()))

    failed = _call(dispatcher, 8, "health", {})
    recovered = _call(dispatcher, 9, "health", {})

    assert failed["error"]["data"] == {"error_code": "CORE_ERROR", "method": "health"}
    assert recovered["result"]["status"] == "ok"


def test_jsonrpc_system_action_reports_unavailable_without_local_worker(
    tmp_path: Path,
) -> None:
    response = _call(
        _dispatcher(tmp_path),
        8,
        "system.actions.execute",
        {
            "task_id": "0198f4de-0114-7000-8000-000000000003",
            "action": {"type": "open_settings", "page": "display"},
            "idempotency_key": "system:unavailable",
            "user_confirmed": True,
        },
    )

    assert response["error"]["data"]["error_code"] == "CAPABILITY_NOT_AVAILABLE"


def test_jsonrpc_project_conversation_task_vertical_slice(tmp_path: Path) -> None:
    dispatcher = _dispatcher(tmp_path)
    project_response = _call(
        dispatcher,
        1,
        "projects.create",
        {"name": "Example", "residency": "local_only"},
    )
    project = project_response["result"]["project"]
    initial_version = project_response["result"]["initial_version"]
    conversation_response = _call(
        dispatcher,
        2,
        "conversations.create",
        {"project_id": project["id"], "workspace_type": "project_chat"},
    )
    conversation = conversation_response["result"]
    task_response = _call(
        dispatcher,
        3,
        "tasks.create",
        {
            "conversation_id": conversation["id"],
            "user_request": "Add pricing",
            "operation_mode": "continue_current_chat_draft",
            "execution_target": "local",
            "idempotency_key": "rpc:request-1",
        },
    )
    task_context = task_response["result"]

    assert project["active_version_id"] == initial_version["id"]
    assert conversation["base_version_id"] == initial_version["id"]
    assert task_context["task"]["conversation_id"] == conversation["id"]
    assert task_context["target_version"]["parent_version_id"] == initial_version["id"]
    assert task_context["scope"]["scope_digest"]


def test_jsonrpc_runtime_preview_and_artifact_contracts(tmp_path: Path) -> None:
    stack = build_runtime_stack(tmp_path)
    artifact = Artifact.create(
        project_id=stack.task.task.project_id,
        conversation_id=stack.task.task.conversation_id,
        task_id=stack.task.task.id,
        version_id=stack.task.task.target_version_id,
        artifact_type=ArtifactType.PREVIEW_MANIFEST,
        visibility=ArtifactVisibility.CONVERSATION,
        storage_location="artifacts/preview.json",
        media_type="application/json",
        byte_length=2,
        content_hash="b" * 64,
        metadata={},
    )
    with stack.factory() as unit_of_work:
        unit_of_work.state.append_artifact(artifact)
        unit_of_work.commit()
    dispatcher = JsonRpcDispatcher(
        CoreService(
            stack.core,
            unit_of_work_factory=stack.factory,
            registry=build_default_registry(),
            runtime_application=stack.runtime,
        )
    )

    started = _call(
        dispatcher,
        30,
        "previews.start",
        {
            "task_id": str(stack.task.task.id),
            "idempotency_key": "rpc:preview:start",
        },
    )["result"]
    preview_id = started["preview"]["id"]
    runtime_id = started["runtime"]["id"]

    assert _call(dispatcher, 31, "previews.get", {"preview_id": preview_id})["result"] == started
    assert (
        _call(dispatcher, 32, "runtimes.get", {"runtime_id": runtime_id})["result"]["status"]
        == "running"
    )
    assert _call(
        dispatcher,
        33,
        "artifacts.list",
        {"task_id": str(stack.task.task.id)},
    )["result"]["items"][0]["id"] == str(artifact.id)
    assert (
        _call(
            dispatcher,
            34,
            "previews.stop",
            {"preview_id": preview_id, "idempotency_key": "rpc:preview:stop"},
        )["result"]["status"]
        == "stopped"
    )


def test_jsonrpc_task_create_rejects_client_scope_injection(tmp_path: Path) -> None:
    dispatcher = _dispatcher(tmp_path)

    response = _call(
        dispatcher,
        1,
        "tasks.create",
        {
            "conversation_id": "018f0f7c-1234-7000-8000-000000000001",
            "user_request": "Read secrets",
            "operation_mode": "answer",
            "execution_target": "local",
            "idempotency_key": "rpc:invalid",
            "project_root": "C:/Users/example",
        },
    )

    assert response["error"]["code"] == -32602
    assert response["error"]["data"]["error_code"] == "INVALID_PARAMS"


def test_jsonrpc_exposes_task_scoped_memory_retrieval(tmp_path: Path) -> None:
    dispatcher = _dispatcher(tmp_path)
    project = _call(
        dispatcher,
        1,
        "projects.create",
        {"name": "Memory retrieval", "residency": "local_only"},
    )["result"]
    conversation = _call(
        dispatcher,
        2,
        "conversations.create",
        {
            "project_id": project["project"]["id"],
            "workspace_type": "project_chat",
        },
    )["result"]
    task = _call(
        dispatcher,
        3,
        "tasks.create",
        {
            "conversation_id": conversation["id"],
            "user_request": "inspect memory",
            "operation_mode": "continue_current_chat_draft",
            "execution_target": "local",
            "idempotency_key": "rpc:memory-retrieval",
        },
    )["result"]
    task_id = task["task"]["id"]
    snapshot_id = task["task"]["memory_snapshot_id"]

    search = _call(
        dispatcher,
        4,
        "memory.search",
        {"task_id": task_id, "query": "memory", "limit": 10},
    )
    snapshot = _call(
        dispatcher,
        5,
        "memory.snapshots.get",
        {"task_id": task_id, "snapshot_id": snapshot_id},
    )
    health = _call(
        dispatcher,
        6,
        "memory.projection.health",
        {"task_id": task_id},
    )
    forged = _call(
        dispatcher,
        7,
        "memory.snapshots.get",
        {
            "task_id": task_id,
            "snapshot_id": "018f0f7c-1234-7000-8000-000000000099",
        },
    )
    invalid_limit = _call(
        dispatcher,
        8,
        "memory.search",
        {"task_id": task_id, "query": "memory", "limit": 101},
    )

    assert search["result"] == {"items": []}
    assert snapshot["result"]["id"] == snapshot_id
    assert [item["ordinal"] for item in snapshot["result"]["items"]] == list(
        range(len(snapshot["result"]["items"]))
    )
    assert health["result"]["lag"] >= 0
    assert health["result"]["state"] in {"ready", "stale", "unavailable", "failed"}
    assert forged["error"]["data"]["error_code"] == "MEMORY_SCOPE_VIOLATION"
    assert invalid_limit["error"]["data"]["error_code"] == "INVALID_PARAMS"


def test_jsonrpc_unknown_method_uses_standard_error(tmp_path: Path) -> None:
    response = _call(_dispatcher(tmp_path), 9, "shell.execute", {})

    assert response == {
        "jsonrpc": "2.0",
        "id": 9,
        "error": {
            "code": -32601,
            "message": "Method not found",
            "data": {"method": "shell.execute"},
        },
    }


def test_jsonrpc_exposes_complete_local_project_loop_and_resumable_events(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("base", encoding="utf-8")
    dispatcher = _dispatcher(tmp_path / "data")
    imported = _call(
        dispatcher,
        1,
        "projects.import",
        {"name": "Imported", "residency": "local_only", "source_path": str(source)},
    )["result"]
    project = imported["project"]
    base = imported["initial_version"]
    conversation = _call(
        dispatcher,
        2,
        "conversations.create",
        {"project_id": project["id"], "workspace_type": "project_chat"},
    )["result"]
    task_context = _call(
        dispatcher,
        3,
        "tasks.create",
        {
            "conversation_id": conversation["id"],
            "user_request": "Update readme",
            "operation_mode": "continue_current_chat_draft",
            "execution_target": "local",
            "idempotency_key": "rpc:loop",
        },
    )["result"]
    pending = _call(
        dispatcher,
        4,
        "changesets.propose",
        {
            "task_id": task_context["task"]["id"],
            "files": [{"path": "README.md", "content": "draft"}],
            "reason": "Apply requested copy",
            "idempotency_key": "rpc:changeset",
        },
    )["result"]
    applied = _call(
        dispatcher,
        5,
        "approvals.decide",
        {"approval_id": pending["approval"]["id"], "approved": True},
    )["result"]
    checkpoint = _call(
        dispatcher,
        6,
        "tasks.review",
        {"task_id": task_context["task"]["id"]},
    )["result"]
    accepted = _call(
        dispatcher,
        7,
        "versions.accept",
        {
            "task_id": task_context["task"]["id"],
            "expected_project_revision": project["revision"],
            "user_confirmed": True,
        },
    )["result"]
    capabilities = _call(
        dispatcher,
        8,
        "capabilities.get",
        {},
    )["result"]
    events = _call(dispatcher, 9, "events.subscribe", {"cursor": 0})["result"]

    assert base["project_root"] != task_context["target_version"]["project_root"]
    assert applied["approval"]["decision"] == "approved"
    assert applied["changeset"]["status"] == "applied"
    assert checkpoint["changed_files"] == ["README.md"]
    assert accepted["active_version_id"] == task_context["target_version"]["id"]
    assert capabilities["operations"]["workspace.fork"] is True
    assert capabilities["operations"]["run.sandboxed"] is False
    assert events["next_cursor"] == events["items"][-1]["cursor"]
    assert all(event["conversation_id"] for event in events["items"])
    assert all(event["task_id"] for event in events["items"])
    assert all(event["visibility"] != "internal" for event in events["items"])


def test_public_method_manifest_is_stable() -> None:
    assert JsonRpcDispatcher.method_names() == frozenset(
        {
            "approvals.decide",
            "approvals.list",
            "artifacts.list",
            "artifacts.read",
            "assistant.turns.cancel",
            "assistant.turns.create",
            "assistant.turns.get",
            "assistant.turns.retry",
            "assistant.turns.run",
            "assistant.turns.start",
            "capabilities.get",
            "changesets.propose",
            "conversations.create",
            "conversations.delete",
            "conversations.get",
            "conversations.list",
            "conversations.move_to_project",
            "conversations.update",
            "documents.delete",
            "documents.get",
            "documents.import",
            "documents.list",
            "documents.search",
            "events.subscribe",
            "health",
            "memory.claims.get",
            "memory.claims.list",
            "memory.claims.promote",
            "memory.claims.resolve_conflict",
            "memory.claims.supersede",
            "memory.forget",
            "memory.observations.create",
            "memory.observations.list",
            "memory.projection.health",
            "memory.search",
            "memory.snapshots.get",
            "mcp.servers.accept",
            "mcp.servers.configure",
            "mcp.servers.delete",
            "mcp.servers.discover",
            "mcp.servers.list",
            "mcp.servers.set_enabled",
            "messages.list",
            "projects.create",
            "projects.get",
            "projects.import",
            "projects.list",
            "previews.get",
            "previews.resolve",
            "previews.start",
            "previews.stop",
            "permissions.get",
            "permissions.update",
            "providers.health",
            "providers.list",
            "runtimes.get",
            "runtimes.health",
            "skills.list",
            "system.actions.execute",
            "tasks.archive",
            "tasks.create",
            "tasks.get",
            "tasks.list",
            "tasks.review",
            "tasks.update_metadata",
            "versions.accept",
            "versions.discard",
            "versions.get",
            "versions.list",
            "voice.synthesize",
            "voice.sessions.cancel",
            "voice.sessions.get",
                "voice.sessions.start",
                "voice.transcribe",
                "workspaces.files.list",
                "workspaces.files.read",
                "workspaces.get",
            }
    )
