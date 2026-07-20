from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from fairy_core.application.service import CoreMethodNotFoundError, CoreService
from fairy_core.commanding.registry import build_default_registry
from fairy_core.contracts.methods import CORE_METHODS
from fairy_core.domain.errors import (
    InvalidTransitionError,
    ProjectBusyError,
    VersionConflictError,
)
from fairy_core.domain.execution import Artifact, ArtifactType, ArtifactVisibility
from fairy_core.domain.ids import new_id
from fairy_core.runtime.models import RuntimeExecutorError
from fairy_core.transports.stdio import build_local_service
from tests.runtime_support import RuntimeStack, build_runtime_stack


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


def test_core_service_exposes_project_lifecycle_and_unified_trash(tmp_path: Path) -> None:
    service = build_local_service(tmp_path)
    try:
        project = service.invoke(
            "projects.create",
            {"name": "History", "residency": "local_only"},
        )["project"]
        updated = service.invoke(
            "projects.update_metadata",
            {
                "project_id": project["id"],
                "name": "History workspace",
                "pinned": True,
                "expected_revision": project["metadata_revision"],
            },
        )
        archived = service.invoke(
            "projects.archive",
            {
                "project_id": updated["id"],
                "expected_revision": updated["metadata_revision"],
            },
        )

        assert archived["archived_at"] is not None
        assert archived["pinned_at"] is None
        assert archived["id"] not in {
            item["id"] for item in service.invoke("projects.list", {})["items"]
        }
        archived_items = service.invoke("projects.archived.list", {})["items"]
        assert archived_items[0]["project"]["id"] == archived["id"]
        assert archived_items[0]["thread_count"] == 1

        deleted = service.invoke(
            "projects.archived.delete",
            {
                "project_id": archived["id"],
                "expected_revision": archived["metadata_revision"],
                "user_confirmed": True,
            },
        )
        trash = service.invoke("trash.items.list", {})["items"]
        project_item = next(item for item in trash if item["item_id"] == deleted["id"])
        assert project_item["item_type"] == "project"
        assert project_item["thread_count"] == 1

        restore_project_request = {
            "item_type": "project",
            "item_id": deleted["id"],
            "expected_revision": deleted["metadata_revision"],
        }
        restored = service.invoke("trash.items.restore", restore_project_request)
        assert restored["status"] == "restored"
        assert service.invoke("trash.items.restore", restore_project_request) == restored
        restored_archived = service.invoke("projects.archived.list", {})["items"]
        assert restored_archived[0]["project"]["id"] == deleted["id"]

        restore_archive_request = {
            "project_id": deleted["id"],
            "expected_revision": deleted["metadata_revision"] + 1,
        }
        restored_project = service.invoke("projects.archived.restore", restore_archive_request)
        assert service.invoke("projects.archived.restore", restore_archive_request) == (
            restored_project
        )
        conversation = service.invoke(
            "conversations.create",
            {
                "project_id": restored_project["id"],
                "workspace_type": "project_chat",
            },
        )
        deleted_conversation = service.invoke(
            "conversations.delete",
            {
                "conversation_id": conversation["id"],
                "expected_revision": conversation["revision"],
                "user_confirmed": True,
            },
        )
        conversation_item = next(
            item
            for item in service.invoke("trash.items.list", {})["items"]
            if item["item_id"] == deleted_conversation["id"]
        )
        assert conversation_item["item_type"] == "project_conversation"
        assert conversation_item["source_project_title"] == "History workspace"
        restore_conversation_request = {
            "item_type": "project_conversation",
            "item_id": deleted_conversation["id"],
            "expected_revision": deleted_conversation["revision"],
        }
        restored_conversation = service.invoke(
            "trash.items.restore",
            restore_conversation_request,
        )
        assert service.invoke("trash.items.restore", restore_conversation_request) == (
            restored_conversation
        )
    finally:
        service.close()


def test_history_destructive_mutations_replay_without_duplicate_events(tmp_path: Path) -> None:
    service = build_local_service(tmp_path)
    try:
        project = service.invoke(
            "projects.create",
            {"name": "Replay history", "residency": "local_only"},
        )["project"]
        update_request = {
            "project_id": project["id"],
            "name": "Replay history updated",
            "expected_revision": project["metadata_revision"],
        }
        updated = service.invoke("projects.update_metadata", update_request)
        assert service.invoke("projects.update_metadata", update_request) == updated

        archive_request = {
            "project_id": project["id"],
            "expected_revision": updated["metadata_revision"],
        }
        archived = service.invoke("projects.archive", archive_request)
        assert service.invoke("projects.archive", archive_request) == archived

        delete_request = {
            "project_id": project["id"],
            "expected_revision": archived["metadata_revision"],
            "cancel_active": False,
            "user_confirmed": True,
        }
        deleted = service.invoke("projects.archived.delete", delete_request)
        assert service.invoke("projects.archived.delete", delete_request) == deleted

        purge_request = {
            "item_type": "project",
            "item_id": project["id"],
            "expected_revision": deleted["metadata_revision"],
            "user_confirmed": True,
        }
        purged = service.invoke("trash.items.purge", purge_request)
        replayed_purge = service.invoke("trash.items.purge", purge_request)
        assert purged["status"] == replayed_purge["status"] == "purged"
        assert replayed_purge["released_bytes"] == 0

        event_types = [
            event["event_type"]
            for event in service.invoke("events.list", {"cursor": 0, "limit": 100})["items"]
        ]
        assert event_types.count("project.updated") == 1
        assert event_types.count("project.archived") == 1
        assert event_types.count("project.deleted") == 1
        assert event_types.count("project.purged") == 1
    finally:
        service.close()


def test_permanent_trash_cleanup_releases_only_exclusive_workspaces(
    tmp_path: Path,
) -> None:
    service = build_local_service(tmp_path)
    try:
        scratch = service.invoke(
            "conversations.create",
            {"project_id": None, "workspace_type": "chat_scratch"},
        )
        scratch_root = tmp_path / "workspaces" / "projects" / scratch["workspace_id"]
        scratch_file = scratch_root / "versions" / scratch["base_version_id"] / "note.txt"
        scratch_file.write_text("scratch content", encoding="utf-8")
        deleted_scratch = service.invoke(
            "conversations.delete",
            {
                "conversation_id": scratch["id"],
                "expected_revision": scratch["revision"],
                "user_confirmed": True,
            },
        )
        scratch_item = next(
            item
            for item in service.invoke("trash.items.list", {})["items"]
            if item["item_id"] == scratch["id"]
        )
        assert scratch_item["estimated_bytes"] >= len("scratch content")
        scratch_purge = service.invoke(
            "trash.items.purge",
            {
                "item_type": "conversation",
                "item_id": scratch["id"],
                "expected_revision": deleted_scratch["revision"],
                "user_confirmed": True,
            },
        )
        assert scratch_purge["released_bytes"] >= len("scratch content")
        assert not scratch_root.exists()
        assert (
            service.invoke(
                "conversations.get",
                {"conversation_id": scratch["id"]},
            )["title"]
            == "Deleted chat"
        )

        created = service.invoke(
            "projects.create",
            {"name": "Shared files", "residency": "local_only"},
        )
        project = created["project"]
        project_root = tmp_path / "workspaces" / "projects" / project["workspace_id"]
        project_file = project_root / "versions" / created["initial_version"]["id"] / "shared.txt"
        project_file.write_text("shared content", encoding="utf-8")
        thread = service.invoke(
            "conversations.create",
            {"project_id": project["id"], "workspace_type": "project_chat"},
        )
        deleted_thread = service.invoke(
            "conversations.delete",
            {
                "conversation_id": thread["id"],
                "expected_revision": thread["revision"],
                "user_confirmed": True,
            },
        )
        thread_purge = service.invoke(
            "trash.items.purge",
            {
                "item_type": "project_conversation",
                "item_id": thread["id"],
                "expected_revision": deleted_thread["revision"],
                "user_confirmed": True,
            },
        )
        assert thread_purge["released_bytes"] == 0
        assert project_file.is_file()

        deleted_project = service.invoke(
            "projects.delete",
            {
                "project_id": project["id"],
                "expected_revision": project["metadata_revision"],
                "user_confirmed": True,
                "cancel_active": False,
            },
        )
        project_purge = service.invoke(
            "trash.items.purge",
            {
                "item_type": "project",
                "item_id": project["id"],
                "expected_revision": deleted_project["metadata_revision"],
                "user_confirmed": True,
            },
        )
        assert project_purge["released_bytes"] >= len("shared content")
        assert not project_root.exists()
        assert service.invoke("projects.get", {"project_id": project["id"]})["name"] == (
            "Deleted project"
        )

        event_types = {
            event["event_type"]
            for event in service.invoke("events.list", {"cursor": 0, "limit": 100})["items"]
        }
        assert {
            "conversation.deleted",
            "conversation.purged",
            "project.deleted",
            "project.purged",
        } <= event_types
    finally:
        service.close()


def test_trash_purge_all_respects_deleted_before_cutoff(tmp_path: Path) -> None:
    service = build_local_service(tmp_path)
    try:
        conversation = service.invoke(
            "conversations.create",
            {"project_id": None, "workspace_type": "chat_scratch"},
        )
        service.invoke(
            "conversations.delete",
            {
                "conversation_id": conversation["id"],
                "expected_revision": conversation["revision"],
                "user_confirmed": True,
            },
        )

        too_old = service.invoke(
            "trash.items.purge_all",
            {
                "user_confirmed": True,
                "deleted_before": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            },
        )
        assert too_old["purged_count"] == 0
        assert service.invoke("trash.items.list", {})["items"]

        eligible = service.invoke(
            "trash.items.purge_all",
            {
                "user_confirmed": True,
                "deleted_before": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            },
        )
        assert eligible["purged_count"] == 1
        assert service.invoke("trash.items.list", {})["items"] == []
    finally:
        service.close()


def test_trash_purge_rejects_active_conversation_before_removing_workspace(
    tmp_path: Path,
) -> None:
    service = build_local_service(tmp_path)
    try:
        conversation = service.invoke(
            "conversations.create",
            {"project_id": None, "workspace_type": "chat_scratch"},
        )
        workspace_root = tmp_path / "workspaces" / "scratch" / conversation["workspace_id"]
        workspace_root.mkdir(parents=True)
        content = workspace_root / "notes.txt"
        content.write_text("keep me", encoding="utf-8")

        with pytest.raises(InvalidTransitionError, match="must be deleted"):
            service.invoke(
                "trash.items.purge",
                {
                    "item_type": "conversation",
                    "item_id": conversation["id"],
                    "expected_revision": conversation["revision"],
                    "user_confirmed": True,
                },
            )

        assert content.read_text(encoding="utf-8") == "keep me"
    finally:
        service.close()


def test_trash_purge_rejects_stale_revision_before_removing_content(tmp_path: Path) -> None:
    service = build_local_service(tmp_path)
    try:
        conversation = service.invoke(
            "conversations.create",
            {"project_id": None, "workspace_type": "chat_scratch"},
        )
        workspace_root = tmp_path / "workspaces" / "projects" / conversation["workspace_id"]
        content = workspace_root / "versions" / conversation["base_version_id"] / "notes.txt"
        content.write_text("keep stale content", encoding="utf-8")
        deleted = service.invoke(
            "conversations.delete",
            {
                "conversation_id": conversation["id"],
                "expected_revision": conversation["revision"],
                "user_confirmed": True,
            },
        )

        with pytest.raises(VersionConflictError):
            service.invoke(
                "trash.items.purge",
                {
                    "item_type": "conversation",
                    "item_id": conversation["id"],
                    "expected_revision": deleted["revision"] - 1,
                    "user_confirmed": True,
                },
            )

        assert content.read_text(encoding="utf-8") == "keep stale content"
        assert (
            service.invoke(
                "conversations.get",
                {"conversation_id": conversation["id"]},
            )["purged_at"]
            is None
        )
    finally:
        service.close()


def test_project_purge_rejects_stale_revision_before_removing_workspace(
    tmp_path: Path,
) -> None:
    service = build_local_service(tmp_path)
    try:
        created = service.invoke(
            "projects.create",
            {"name": "Stale purge", "residency": "local_only"},
        )
        project = created["project"]
        workspace_root = tmp_path / "workspaces" / "projects" / project["workspace_id"]
        content = workspace_root / "versions" / created["initial_version"]["id"] / "notes.txt"
        content.write_text("keep project content", encoding="utf-8")
        deleted = service.invoke(
            "projects.delete",
            {
                "project_id": project["id"],
                "expected_revision": project["metadata_revision"],
                "user_confirmed": True,
                "cancel_active": False,
            },
        )

        with pytest.raises(VersionConflictError):
            service.invoke(
                "trash.items.purge",
                {
                    "item_type": "project",
                    "item_id": project["id"],
                    "expected_revision": deleted["metadata_revision"] - 1,
                    "user_confirmed": True,
                },
            )

        assert content.read_text(encoding="utf-8") == "keep project content"
        assert service.invoke("projects.get", {"project_id": project["id"]})["purged_at"] is None
    finally:
        service.close()


def test_automatic_trash_maintenance_skips_synced_projects(tmp_path: Path) -> None:
    service = build_local_service(tmp_path)
    try:
        deleted = []
        for name, residency in (("Local", "local_only"), ("Synced", "synced")):
            project = service.invoke(
                "projects.create",
                {"name": name, "residency": residency},
            )["project"]
            deleted.append(
                service.invoke(
                    "projects.delete",
                    {
                        "project_id": project["id"],
                        "expected_revision": project["metadata_revision"],
                        "user_confirmed": True,
                        "cancel_active": False,
                    },
                )
            )

        result = service.invoke(
            "trash.items.purge_all",
            {
                "user_confirmed": True,
                "deleted_before": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                "maintenance": True,
            },
        )

        assert result["purged_count"] == 1
        remaining = service.invoke("trash.items.list", {})["items"]
        assert [item["item_id"] for item in remaining] == [deleted[1]["id"]]
    finally:
        service.close()


def test_project_delete_requires_cancel_active_for_live_work(tmp_path: Path) -> None:
    stack = build_runtime_stack(tmp_path)
    service = CoreService(
        stack.core,
        unit_of_work_factory=stack.factory,
        registry=build_default_registry(),
        runtime_application=stack.runtime,
    )
    try:
        project_id = str(stack.task.task.project_id)
        project = service.invoke("projects.get", {"project_id": project_id})
        with pytest.raises(ProjectBusyError):
            service.invoke(
                "projects.delete",
                {
                    "project_id": project_id,
                    "expected_revision": project["metadata_revision"],
                    "user_confirmed": True,
                    "cancel_active": False,
                },
            )
        assert service.invoke("projects.get", {"project_id": project_id})["deleted_at"] is None
    finally:
        service.close()


def test_project_delete_stops_preview_before_tombstone(tmp_path: Path) -> None:
    stack = build_runtime_stack(tmp_path)
    service = CoreService(
        stack.core,
        unit_of_work_factory=stack.factory,
        registry=build_default_registry(),
        runtime_application=stack.runtime,
    )
    try:
        started = _start_runtime_preview(service, stack)
        project_id = str(stack.task.task.project_id)
        project = service.invoke("projects.get", {"project_id": project_id})
        deleted = service.invoke(
            "projects.delete",
            {
                "project_id": project_id,
                "expected_revision": project["metadata_revision"],
                "user_confirmed": True,
                "cancel_active": True,
            },
        )

        assert deleted["deleted_at"] is not None
        assert (
            service.invoke(
                "runtimes.get",
                {"runtime_id": started["runtime"]["id"]},
            )["status"]
            == "stopped"
        )
    finally:
        service.close()


def test_project_delete_stop_failure_keeps_project_active(tmp_path: Path) -> None:
    stack = build_runtime_stack(tmp_path)
    service = CoreService(
        stack.core,
        unit_of_work_factory=stack.factory,
        registry=build_default_registry(),
        runtime_application=stack.runtime,
    )
    try:
        _start_runtime_preview(service, stack)
        stack.executor.stop_failure = RuntimeExecutorError(
            "stop failed",
            error_code="WORKER_INTERRUPTED",
        )
        project_id = str(stack.task.task.project_id)
        project = service.invoke("projects.get", {"project_id": project_id})
        with pytest.raises(RuntimeExecutorError, match="stop failed"):
            service.invoke(
                "projects.delete",
                {
                    "project_id": project_id,
                    "expected_revision": project["metadata_revision"],
                    "user_confirmed": True,
                    "cancel_active": True,
                },
            )

        assert service.invoke("projects.get", {"project_id": project_id})["deleted_at"] is None
    finally:
        service.close()


def _start_runtime_preview(service: CoreService, stack: RuntimeStack) -> dict[str, object]:
    task = stack.task.task
    with stack.factory() as unit_of_work:
        workspace = unit_of_work.state.get_workspace(task.workspace_id)
    assert workspace is not None
    return service.invoke(
        "previews.start",
        {
            "task_id": str(task.id),
            "workspace_id": str(task.workspace_id),
            "version_id": str(task.target_version_id),
            "expected_workspace_revision": workspace.revision,
            "idempotency_key": "project-delete:preview:start",
        },
    )


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
        "assistant.turns.trace.list",
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
        "events.list",
        "events.state",
        "events.subscribe",
        "extensions.catalog.list",
        "edit_recipes.apply",
        "edit_recipes.create",
        "edit_recipes.discard",
        "edit_recipes.update",
        "files.open_stream",
        "files.cancel",
        "files.compare",
        "files.present",
        "files.probe",
        "file_sets.get",
        "file_sets.resolve",
        "execution_plans.create",
        "execution_plans.get",
        "health",
        "knowledge.graph.get",
        "knowledge.items.list",
        "knowledge.projects.overview",
        "memory.claims.get",
        "memory.claims.list",
        "memory.claims.promote",
        "memory.claims.resolve_conflict",
        "memory.claims.supersede",
        "memory.forget",
        "memory.observations.create",
        "memory.observations.list",
        "memory.projection.health",
        "memory.proposals.accept",
        "memory.proposals.list",
        "memory.proposals.reject",
        "memory.search",
        "memory.settings.get",
        "memory.settings.update",
        "memory.snapshots.get",
        "media.audio.generate",
        "media.images.generate",
        "media.jobs.list",
        "media.videos.cancel",
        "media.videos.get",
        "media.videos.start",
        "mcp.servers.accept",
        "mcp.servers.configure",
        "mcp.servers.delete",
        "mcp.servers.discover",
        "mcp.servers.list",
        "mcp.servers.set_enabled",
        "mcp.presets.install",
        "messages.list",
        "models.catalog.list",
        "models.catalog.refresh",
        "models.selection.get",
        "models.selection.update",
        "obsidian.health.get",
        "obsidian.sources.create",
        "obsidian.sources.items.list",
        "obsidian.sources.items.read",
        "obsidian.sources.list",
        "obsidian.sync.start",
        "projects.create",
        "projects.archive",
        "projects.archived.delete",
        "projects.archived.list",
        "projects.archived.restore",
        "projects.delete",
        "projects.get",
        "projects.import",
        "projects.list",
        "projects.update_metadata",
        "previews.get",
        "previews.resolve",
        "previews.start",
        "previews.stop",
        "permissions.get",
        "permissions.update",
        "providers.health",
        "providers.list",
        "realtime.memories.delete",
        "realtime.memories.list",
        "realtime.memories.save",
        "realtime.sessions.get",
        "realtime.sessions.list",
        "realtime.sessions.report",
        "realtime.sessions.start",
        "realtime.sessions.stop",
        "renderer_packs.health",
        "renderer_packs.install",
        "renderer_packs.list",
        "renderer_packs.remove",
        "renderer_packs.update",
        "runtimes.get",
        "runtimes.health",
        "skills.list",
        "skills.install",
        "skills.create",
        "skills.import.inspect",
        "skills.import.install",
        "skills.remove",
        "skills.set_enabled",
        "skills.update",
        "selections.create",
        "system.actions.execute",
        "tasks.archive",
        "tasks.create",
        "tasks.get",
        "tasks.list",
        "tasks.review",
        "tasks.update_metadata",
        "trash.items.list",
        "trash.items.purge",
        "trash.items.purge_all",
        "trash.items.restore",
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
