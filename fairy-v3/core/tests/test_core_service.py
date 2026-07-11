from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from fairy_core.application.service import CoreMethodNotFoundError
from fairy_core.contracts.methods import CORE_METHODS
from fairy_core.transports.stdio import build_local_service


def test_core_service_owns_validation_handlers_and_response_serialization(tmp_path: Path) -> None:
    service = build_local_service(tmp_path)
    try:
        health = service.invoke("health", {})
        project = service.invoke(
            "projects.create",
            {"name": "Service project", "residency": "local_only"},
        )

        assert health == {
            "status": "ok",
            "service": "fairy-core",
            "protocol": "core-service-v1",
        }
        assert project["project"]["name"] == "Service project"
        assert isinstance(project["project"]["id"], str)
    finally:
        service.close()


def test_core_service_rejects_unknown_methods_and_invalid_params(tmp_path: Path) -> None:
    service = build_local_service(tmp_path)
    try:
        with pytest.raises(CoreMethodNotFoundError, match=r"shell\.execute"):
            service.invoke("shell.execute", {})
        with pytest.raises(ValidationError):
            service.invoke("projects.get", {"project_id": "not-a-uuid"})
    finally:
        service.close()


def test_core_service_exposes_typed_workspace_collection_pages(tmp_path: Path) -> None:
    service = build_local_service(tmp_path)
    try:
        projects = [
            service.invoke(
                "projects.create",
                {"name": f"Project {index}", "residency": "local_only"},
            )["project"]
            for index in range(3)
        ]
        first_page = service.invoke("projects.list", {"limit": 2})
        second_page = service.invoke(
            "projects.list",
            {"limit": 2, "cursor": first_page["next_cursor"]},
        )
        conversation = service.invoke(
            "conversations.create",
            {
                "project_id": projects[0]["id"],
                "workspace_type": "project_chat",
            },
        )
        fetched_conversation = service.invoke(
            "conversations.get",
            {"conversation_id": conversation["id"]},
        )
        task = service.invoke(
            "tasks.create",
            {
                "conversation_id": conversation["id"],
                "user_request": "Build a page",
                "operation_mode": "continue_current_chat_draft",
                "execution_target": "local",
                "idempotency_key": "collections:task",
            },
        )["task"]
        approval = service.invoke(
            "changesets.propose",
            {
                "task_id": task["id"],
                "files": [{"path": "index.html", "content": "<h1>Page</h1>"}],
                "reason": "Create page",
                "idempotency_key": "collections:changeset",
            },
        )["approval"]

        assert len(first_page["items"]) == 2
        assert first_page["next_cursor"] is not None
        assert len(second_page["items"]) == 1
        assert second_page["next_cursor"] is None
        assert fetched_conversation == conversation
        conversation_items = service.invoke(
            "conversations.list",
            {"project_id": projects[0]["id"]},
        )["items"]
        assert conversation["id"] in {item["id"] for item in conversation_items}
        assert len(conversation_items) == 2
        task_items = service.invoke(
            "tasks.list",
            {"conversation_id": conversation["id"]},
        )["items"]
        assert [item["id"] for item in task_items] == [task["id"]]
        assert (
            len(
                service.invoke(
                    "versions.list",
                    {"project_id": projects[0]["id"]},
                )["items"]
            )
            == 2
        )
        assert service.invoke(
            "approvals.list",
            {"task_id": task["id"]},
        )["items"] == [approval]
        with pytest.raises(ValidationError):
            service.invoke("projects.list", {"limit": 0})
    finally:
        service.close()


def test_core_method_catalog_is_the_single_public_method_authority() -> None:
    assert set(CORE_METHODS) == {
        "approvals.decide",
        "approvals.list",
        "capabilities.get",
        "changesets.propose",
        "conversations.create",
        "conversations.get",
        "conversations.list",
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
        "projects.create",
        "projects.get",
        "projects.import",
        "projects.list",
        "tasks.create",
        "tasks.get",
        "tasks.list",
        "tasks.review",
        "versions.accept",
        "versions.discard",
        "versions.get",
        "versions.list",
    }
    assert all(method.name == name for name, method in CORE_METHODS.items())
