from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from fairy_core.domain.errors import IdempotencyConflictError, VersionConflictError
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from fairy_core.transports.stdio import build_local_service
from fairy_core.workspace.index import ProjectIndexer


def _project_task(service, source: Path) -> tuple[dict[str, object], dict[str, object]]:
    project = service.invoke(
        "projects.import",
        {
            "name": "Indexed project",
            "residency": "local_only",
            "source_path": str(source),
        },
    )
    conversation = service.invoke(
        "conversations.create",
        {"project_id": project["project"]["id"], "workspace_type": "project_chat"},
    )
    task = service.invoke(
        "tasks.create",
        {
            "conversation_id": conversation["id"],
            "user_request": "Inspect the managed project",
            "operation_mode": "continue_current_chat_draft",
            "execution_target": "local",
            "idempotency_key": "workspace:index:task",
        },
    )
    return project, task


def _source_tree(root: Path) -> None:
    (root / "src").mkdir(parents=True)
    (root / "package.json").write_text(
        '{"name":"atlas","scripts":{"test":"vitest"},"dependencies":{"react":"19"}}',
        encoding="utf-8",
    )
    (root / "pyproject.toml").write_text(
        '[project]\nname = "atlas-core"\ndependencies = ["pydantic"]\n',
        encoding="utf-8",
    )
    (root / "src" / "main.py").write_text(
        "import os\nfrom fairy_core import api\n\n"
        "class App:\n    pass\n\n"
        "def run():\n    return os.name\n",
        encoding="utf-8",
    )
    (root / "src" / "ui.ts").write_text(
        'import React from "react";\nexport const App = () => React.createElement("main");\n',
        encoding="utf-8",
    )
    (root / "src" / "lib.rs").write_text(
        "use std::path::Path;\npub struct Runner;\npub fn launch() {}\n",
        encoding="utf-8",
    )
    (root / "asset.bin").write_bytes(b"\x00\x01\x02")
    (root / "large.txt").write_bytes(b"x" * 1_000_001)
    (root / ".env").write_text("OPENROUTER_API_KEY=secret", encoding="utf-8")


def test_task_workspace_and_project_index_are_bound_once_and_deterministic(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    _source_tree(source)
    service = build_local_service(tmp_path / "data")
    try:
        _project, context = _project_task(service, source)
        task = context["task"]
        version = context["target_version"]
        assert version is not None

        with service._unit_of_work_factory() as unit_of_work:  # type: ignore[attr-defined]
            workspace = unit_of_work.workspaces.get(task["id"])
            index = unit_of_work.project_indexes.get(version["id"])
            assert workspace is not None
            assert index is not None
            other_root = tmp_path / "other-root"
            other_root.mkdir()
            with pytest.raises(IdempotencyConflictError):
                unit_of_work.workspaces.bind_once(
                    task_id=task["id"],
                    project_id=task["project_id"],
                    conversation_id=task["conversation_id"],
                    version_id=version["id"],
                    root=other_root,
                    editable_files=("**/*",),
                    reference_files=(),
                    constraints={},
                )

        assert str(workspace.version_id) == version["id"]
        assert workspace.root == Path(version["project_root"]).resolve()
        assert index.generation == 1
        assert [item.path for item in index.files] == sorted(item.path for item in index.files)
        assert ".env" not in {item.path for item in index.files}
        assert index.file("asset.bin").kind == "binary"
        assert index.file("large.txt").kind == "oversized"
        assert index.file("src/main.py").imports == ("fairy_core", "os")
        assert index.file("src/main.py").symbols == ("App", "run")
        assert index.file("src/ui.ts").imports == ("react",)
        assert index.file("src/ui.ts").exports == ("App",)
        assert index.file("src/lib.rs").imports == ("std",)
        assert index.file("src/lib.rs").exports == ("Runner", "launch")
        assert index.file("package.json").summary["name"] == "atlas"

        rebuilt = ProjectIndexer().build(
            project_id=workspace.project_id,
            workspace_id=workspace.workspace_id,
            version_id=workspace.version_id,
            root=workspace.root,
            generation=2,
        )
        assert rebuilt.source_hash == index.source_hash
        assert rebuilt.files == index.files

        with service._unit_of_work_factory() as unit_of_work:  # type: ignore[attr-defined]
            unit_of_work.project_indexes.replace_generation(
                rebuilt,
                expected_generation=1,
            )
        with service._unit_of_work_factory() as unit_of_work:  # type: ignore[attr-defined]
            rolled_back = unit_of_work.project_indexes.get(version["id"])
        assert rolled_back is not None and rolled_back.generation == 1

        engine = service._unit_of_work_factory._engine  # type: ignore[attr-defined]
        isolated_factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="other-tenant")
        with isolated_factory() as isolated:
            assert isolated.workspaces.get(task["id"]) is None
            assert isolated.project_indexes.get(version["id"]) is None
    finally:
        service.close()


def test_project_index_rejects_stale_generation_and_escaped_symlinks(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _source_tree(source)
    service = build_local_service(tmp_path / "data")
    try:
        _project, context = _project_task(service, source)
        version = context["target_version"]
        outside = tmp_path / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        link = Path(version["project_root"]) / "src" / "outside-link.txt"
        try:
            link.symlink_to(outside)
        except OSError:
            pytest.skip("symlink creation is unavailable")
        with service._unit_of_work_factory() as unit_of_work:  # type: ignore[attr-defined]
            current = unit_of_work.project_indexes.get(version["id"])
            assert current is not None
            replacement = ProjectIndexer().build(
                project_id=current.project_id,
                workspace_id=current.workspace_id,
                version_id=current.version_id,
                root=Path(version["project_root"]),
                generation=current.generation + 1,
            )
            assert "src/outside-link.txt" not in {item.path for item in replacement.files}
            with pytest.raises(IdempotencyConflictError):
                unit_of_work.project_indexes.replace_generation(
                    replacement,
                    expected_generation=current.generation - 1,
                )
    finally:
        service.close()


def test_scratch_task_persists_a_versioned_workspace_binding(tmp_path: Path) -> None:
    service = build_local_service(tmp_path / "data")
    try:
        conversation = service.invoke(
            "conversations.create",
            {"project_id": None, "workspace_type": "chat_scratch"},
        )
        task = service.invoke(
            "tasks.create",
            {
                "conversation_id": conversation["id"],
                "user_request": "Answer without a Project",
                "operation_mode": "answer",
                "execution_target": "local",
                "idempotency_key": "workspace:scratch",
            },
        )["task"]

        with service._unit_of_work_factory() as unit_of_work:  # type: ignore[attr-defined]
            workspace = unit_of_work.workspaces.get(task["id"])

        assert workspace is not None
        assert workspace.project_id is None
        assert workspace.workspace_id == UUID(conversation["workspace_id"])
        assert workspace.version_id is not None
        assert workspace.root.is_dir()
    finally:
        service.close()


def test_scratch_workspace_applies_and_reads_a_changeset_without_a_project(
    tmp_path: Path,
) -> None:
    service = build_local_service(tmp_path / "data")
    try:
        conversation = service.invoke(
            "conversations.create",
            {"project_id": None, "workspace_type": "chat_scratch"},
        )
        context = service.invoke(
            "tasks.create",
            {
                "conversation_id": conversation["id"],
                "user_request": "Create a greeting file",
                "operation_mode": "continue_current_chat_draft",
                "execution_target": "local",
                "idempotency_key": "workspace:scratch-changeset-task",
            },
        )
        pending = service.invoke(
            "changesets.propose",
            {
                "task_id": context["task"]["id"],
                "files": [{"path": "hello.txt", "content": "hello Fairy\n"}],
                "reason": "Create the requested greeting",
                "idempotency_key": "workspace:scratch-changeset",
            },
        )
        decision = service.invoke(
            "approvals.decide",
            {"approval_id": pending["approval"]["id"], "approved": True},
        )
        applied = decision["changeset"]

        assert decision["assistant_turn_id"] is None
        assert decision["resume_requested"] is False

        workspace_id = conversation["workspace_id"]
        version_id = context["target_version"]["id"]
        files = service.invoke(
            "workspaces.files.list",
            {"workspace_id": workspace_id, "version_id": version_id},
        )
        content = service.invoke(
            "workspaces.files.read",
            {
                "workspace_id": workspace_id,
                "version_id": version_id,
                "path": "hello.txt",
            },
        )

        assert applied["project_id"] is None
        assert applied["workspace_id"] == workspace_id
        assert [item["path"] for item in files["items"]] == ["hello.txt"]
        assert content["text"] == "hello Fairy\n"
        assert content["stream_required"] is False
    finally:
        service.close()


def test_workspace_file_api_versions_create_rename_delete_and_export(tmp_path: Path) -> None:
    service = build_local_service(tmp_path / "data")
    try:
        conversation = service.invoke(
            "conversations.create",
            {"project_id": None, "workspace_type": "chat_scratch"},
        )
        workspace = service.invoke(
            "workspaces.get",
            {"workspace_id": conversation["workspace_id"]},
        )
        with pytest.raises(VersionConflictError, match="expected Workspace revision"):
            service.invoke(
                "workspaces.files.mutate",
                {
                    "workspace_id": workspace["id"],
                    "conversation_id": conversation["id"],
                    "expected_workspace_revision": workspace["revision"] + 1,
                    "files": [{"operation": "create", "path": "stale.txt", "content": "stale"}],
                    "reason": "Reject stale mutation",
                    "idempotency_key": "workspace:file-stale",
                    "user_confirmed": True,
                },
            )
        created = service.invoke(
            "workspaces.files.mutate",
            {
                "workspace_id": workspace["id"],
                "conversation_id": conversation["id"],
                "expected_workspace_revision": workspace["revision"],
                "files": [
                    {
                        "operation": "create",
                        "path": "notes.txt",
                        "content": "durable notes\n",
                    }
                ],
                "reason": "Upload notes.txt",
                "idempotency_key": "workspace:file-create",
                "user_confirmed": True,
            },
        )
        first_version = created["target_version"]["id"]
        first_file = service.invoke(
            "workspaces.files.list",
            {"workspace_id": workspace["id"], "version_id": first_version},
        )["items"][0]
        renamed = service.invoke(
            "workspaces.files.mutate",
            {
                "workspace_id": workspace["id"],
                "conversation_id": conversation["id"],
                "expected_workspace_revision": workspace["revision"],
                "files": [
                    {
                        "operation": "rename",
                        "path": "notes.txt",
                        "destination_path": "archive/notes.txt",
                        "expected_hash": first_file["content_hash"],
                    }
                ],
                "reason": "Rename notes.txt",
                "idempotency_key": "workspace:file-rename",
                "user_confirmed": True,
            },
        )
        second_version = renamed["target_version"]["id"]
        exported = service.invoke(
            "workspaces.export",
            {
                "workspace_id": workspace["id"],
                "version_id": second_version,
                "filename": "notes-workspace.zip",
            },
        )

        assert created["changeset"]["status"] == "applied"
        assert created["approval"]["decided_by"] == "user"
        assert [
            item["path"]
            for item in service.invoke(
                "workspaces.files.list",
                {"workspace_id": workspace["id"], "version_id": second_version},
            )["items"]
        ] == ["archive/notes.txt"]
        assert exported["filename"] == "notes-workspace.zip"
        assert exported["media_type"] == "application/zip"
        assert exported["byte_length"] > 0
    finally:
        service.close()


def test_autonomous_scratch_changeset_applies_without_user_approval(tmp_path: Path) -> None:
    service = build_local_service(tmp_path / "data")
    try:
        service.invoke(
            "permissions.update",
            {
                "profile": "autonomous",
                "capability_overrides": {},
                "expected_revision": 0,
                "idempotency_key": "workspace:autonomous-settings",
            },
        )
        conversation = service.invoke(
            "conversations.create",
            {"project_id": None, "workspace_type": "chat_scratch"},
        )
        context = service.invoke(
            "tasks.create",
            {
                "conversation_id": conversation["id"],
                "user_request": "Create an autonomous file",
                "operation_mode": "continue_current_chat_draft",
                "execution_target": "local",
                "idempotency_key": "workspace:autonomous-task",
            },
        )
        pending = service.invoke(
            "changesets.propose",
            {
                "task_id": context["task"]["id"],
                "files": [{"path": "auto.txt", "content": "applied\n"}],
                "reason": "Exercise autonomous changeset policy",
                "idempotency_key": "workspace:autonomous-changeset",
            },
        )

        assert pending["changeset"]["status"] == "applied"
        assert pending["approval"]["decision"] == "approved"
        assert pending["approval"]["decided_by"] == "policy:autonomous"
    finally:
        service.close()
