from __future__ import annotations

from pathlib import Path

from fairy_core.workspace.rust_worker import RustWorkspaceProvisioner


class RecordingTransport:
    def __init__(self, managed_root: Path) -> None:
        self.managed_root = managed_root
        self.calls: list[tuple[str, dict[str, object]]] = []

    def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
        self.calls.append((method, params))
        if method == "workspace.create_scratch":
            root = (
                self.managed_root
                / "scratch"
                / str(params["conversation_id"])
                / str(params["task_id"])
            )
            return {"root": str(root)}
        root = (
            self.managed_root
            / "projects"
            / str(params["project_id"])
            / "versions"
            / str(params["version_id"])
        )
        if method == "workspace.checkpoint":
            return {"commit": "a" * 40}
        if method == "workspace.diff":
            return {"diff": "M README.md"}
        if method == "workspace.discard":
            return {"discarded": True}
        if method == "workspace.write_text":
            return {"path": str(root / str(params["relative_path"]))}
        return {"root": str(root)}


def test_adapter_maps_project_lifecycle_to_worker_protocol(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    transport = RecordingTransport(tmp_path / "managed")
    adapter = RustWorkspaceProvisioner(transport, tmp_path / "managed")

    imported = adapter.create_initial_version("project-1", "version-base", source=source)
    draft = adapter.fork_version(
        project_id="project-1",
        version_id="version-draft",
        parent_version_id="version-base",
    )
    written = adapter.write_text(
        project_id="project-1",
        version_id="version-draft",
        relative_path="README.md",
        content="draft",
    )
    diff = adapter.diff(project_id="project-1", version_id="version-draft")
    commit = adapter.checkpoint(
        project_id="project-1",
        version_id="version-draft",
        message="Task complete",
    )
    adapter.discard_version(project_id="project-1", version_id="version-draft")

    assert imported.name == "version-base"
    assert draft.name == "version-draft"
    assert written.name == "README.md"
    assert diff == "M README.md"
    assert commit == "a" * 40
    assert [method for method, _params in transport.calls] == [
        "workspace.import",
        "workspace.fork",
        "workspace.write_text",
        "workspace.diff",
        "workspace.checkpoint",
        "workspace.discard",
    ]


def test_adapter_supports_empty_projects_and_scratch(tmp_path: Path) -> None:
    transport = RecordingTransport(tmp_path / "managed")
    adapter = RustWorkspaceProvisioner(transport, tmp_path / "managed")

    project = adapter.create_initial_version("project-1", "version-base")
    scratch = adapter.create_scratch("conversation-1", "task-1")

    assert project.name == "version-base"
    assert scratch.name == "task-1"
    assert transport.calls[0][0] == "workspace.create_empty"
    assert transport.calls[1][0] == "workspace.create_scratch"
