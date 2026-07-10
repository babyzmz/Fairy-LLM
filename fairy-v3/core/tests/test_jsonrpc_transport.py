from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from fairy_core.application.service import CoreService
from fairy_core.transports.jsonrpc import JsonRpcDispatcher
from fairy_core.transports.stdio import build_local_dispatcher


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
        {"approval_id": pending["approval"]["id"], "approved": True, "decided_by": "user"},
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
        {"profile": "standard", "sandbox_healthy": False},
    )["result"]
    events = _call(dispatcher, 9, "events.subscribe", {"cursor": 0})["result"]

    assert base["project_root"] != task_context["target_version"]["project_root"]
    assert applied["status"] == "applied"
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
            "capabilities.get",
            "changesets.propose",
            "conversations.create",
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
            "projects.create",
            "projects.get",
            "projects.import",
            "tasks.create",
            "tasks.get",
            "tasks.review",
            "versions.accept",
            "versions.discard",
            "versions.get",
        }
    )
