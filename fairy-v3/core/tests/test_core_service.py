from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from fairy_core.application.service import CoreMethodNotFoundError, CoreService
from fairy_core.commanding.registry import build_default_registry
from fairy_core.contracts.methods import CORE_METHODS
from fairy_core.domain.execution import Artifact, ArtifactType, ArtifactVisibility
from fairy_core.domain.ids import new_id
from fairy_core.transports.stdio import build_local_service
from tests.runtime_support import build_runtime_stack


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


def test_core_service_is_provider_free_without_composition(tmp_path: Path) -> None:
    service = build_local_service(tmp_path)
    try:
        assert service.invoke("providers.list", {}) == {"items": []}
        assert service.invoke("providers.health", {}) == {"items": []}
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


def test_core_service_exposes_runtime_preview_and_artifact_contracts(tmp_path: Path) -> None:
    stack = build_runtime_stack(tmp_path)
    service = CoreService(
        stack.core,
        unit_of_work_factory=stack.factory,
        registry=build_default_registry(),
        runtime_application=stack.runtime,
    )
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
        content_hash="a" * 64,
        metadata={"kind": "static_site"},
    )
    with stack.factory() as unit_of_work:
        unit_of_work.state.append_artifact(artifact)
        workspace = unit_of_work.state.get_workspace(stack.task.task.workspace_id)
        assert workspace is not None
        unit_of_work.commit()
    preview_scope = {
        "task_id": str(stack.task.task.id),
        "workspace_id": str(stack.task.task.workspace_id),
        "version_id": str(stack.task.task.target_version_id),
        "expected_workspace_revision": workspace.revision,
    }

    started = service.invoke(
        "previews.start",
        {
            **preview_scope,
            "idempotency_key": "service:preview:start",
        },
    )
    runtime_id = started["runtime"]["id"]
    preview_id = started["preview"]["id"]

    assert started["preview"]["status"] == "ready"
    assert service.invoke("runtimes.get", {"runtime_id": runtime_id})["status"] == "running"
    assert (
        service.invoke(
            "runtimes.health",
            {"task_id": str(stack.task.task.id)},
        )["executor"]["available"]
        is True
    )
    assert service.invoke("previews.get", {"preview_id": preview_id}) == started
    assert (
        service.invoke(
            "previews.resolve",
            {
                "task_id": preview_scope["task_id"],
                "workspace_id": preview_scope["workspace_id"],
                "version_id": preview_scope["version_id"],
            },
        )["preview"]["id"]
        == preview_id
    )
    listed = service.invoke(
        "artifacts.list",
        {"task_id": str(stack.task.task.id)},
    )["items"]
    assert {item["id"] for item in listed} >= {str(artifact.id)}
    assert any(item["metadata"].get("preview_id") == preview_id for item in listed)
    assert service.invoke("artifacts.read", {"artifact_id": str(artifact.id)}) in listed
    stopped = service.invoke(
        "previews.stop",
        {
            **preview_scope,
            "preview_id": preview_id,
            "idempotency_key": "service:preview:stop",
        },
    )
    assert stopped["status"] == "stopped"


def test_local_service_reports_unconfigured_runtime_fail_closed(tmp_path: Path) -> None:
    service = build_local_service(tmp_path)
    try:
        project = service.invoke(
            "projects.create",
            {"name": "No worker", "residency": "local_only"},
        )
        with pytest.raises(KeyError):
            service.invoke("runtimes.health", {"task_id": str(new_id())})

        conversation = service.invoke(
            "conversations.create",
            {
                "project_id": project["project"]["id"],
                "workspace_type": "project_chat",
            },
        )
        task = service.invoke(
            "tasks.create",
            {
                "conversation_id": conversation["id"],
                "user_request": "Inspect runtime",
                "operation_mode": "continue_current_chat_draft",
                "execution_target": "local",
                "idempotency_key": "runtime:unavailable:task",
            },
        )["task"]
        health = service.invoke("runtimes.health", {"task_id": task["id"]})

        assert health["executor"]["available"] is False
        assert health["executor"]["error_code"] == "SANDBOX_UNAVAILABLE"
    finally:
        service.close()


def test_core_method_catalog_is_the_single_public_method_authority() -> None:
    assert set(CORE_METHODS) == {
        "approvals.decide",
        "approvals.list",
        "annotations.list",
        "annotations.update",
        "asset_sets.create",
        "asset_sets.list",
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
        "edit_recipes.apply",
        "edit_recipes.create",
        "edit_recipes.discard",
        "edit_recipes.update",
        "files.open_stream",
        "files.cancel",
        "files.present",
        "files.probe",
        "file_sets.get",
        "file_sets.resolve",
        "execution_plans.create",
        "execution_plans.get",
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
        "renderer_packs.health",
        "renderer_packs.install",
        "renderer_packs.list",
        "renderer_packs.remove",
        "renderer_packs.update",
        "runtimes.get",
        "runtimes.health",
        "skills.list",
        "selections.create",
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
        "workspaces.export",
        "workspaces.files.list",
        "workspaces.files.mutate",
        "workspaces.files.read",
        "workspaces.get",
    }
    assert all(method.name == name for name, method in CORE_METHODS.items())
