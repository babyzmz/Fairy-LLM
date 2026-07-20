from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from fairy_core.workspace.filesystem import FileSystemWorkspaceProvisioner


class _FailBeforeWriteWorkspace(FileSystemWorkspaceProvisioner):
    def write_text(self, **_kwargs: object) -> Path:
        raise OSError("stop after journal recovery")


def test_workspace_size_and_purge_cover_current_and_legacy_roots(tmp_path: Path) -> None:
    managed_root = tmp_path / "managed"
    workspace = FileSystemWorkspaceProvisioner(managed_root)
    current = workspace.create_initial_version("workspace-1", "version-1")
    legacy = workspace.create_scratch("workspace-1", "task-1")
    (current / "current.bin").write_bytes(b"current")
    (legacy / "legacy.bin").write_bytes(b"legacy")

    assert workspace.workspace_size("workspace-1") == 13
    assert workspace.purge_workspace("workspace-1") == 13
    assert workspace.workspace_size("workspace-1") == 0
    assert not (managed_root / "projects" / "workspace-1").exists()
    assert not (managed_root / "scratch" / "workspace-1").exists()
    assert workspace.purge_workspace("workspace-1") == 0


def test_workspace_lifecycle_rejects_path_shaped_identifiers(tmp_path: Path) -> None:
    workspace = FileSystemWorkspaceProvisioner(tmp_path / "managed")

    with pytest.raises(ValueError, match="invalid path characters"):
        workspace.purge_workspace("../outside")


def test_changeset_recovers_a_prepared_journal_after_process_exit(tmp_path: Path) -> None:
    managed_root = tmp_path / "managed"
    source = tmp_path / "source"
    source.mkdir()
    (source / "one.txt").write_text("one-base", encoding="utf-8")
    (source / "two.txt").write_text("two-base", encoding="utf-8")
    workspace = FileSystemWorkspaceProvisioner(managed_root)
    version_root = workspace.create_initial_version("project", "version", source=source)
    core_root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(
            None,
            (str(core_root / "src"), environment.get("PYTHONPATH", "")),
        )
    )
    script = """
import os
import sys
from pathlib import Path
from fairy_core.workspace.filesystem import FileSystemWorkspaceProvisioner

class CrashAfterFirstWrite(FileSystemWorkspaceProvisioner):
    def write_text(self, **kwargs):
        result = super().write_text(**kwargs)
        os._exit(23)

workspace = CrashAfterFirstWrite(Path(sys.argv[1]))
workspace.apply_changeset(
    project_id="project",
    version_id="version",
    mutations=(("one.txt", "one-draft"), ("two.txt", "two-draft")),
)
"""

    crashed = subprocess.run(
        [sys.executable, "-c", script, str(managed_root)],
        cwd=core_root,
        env=environment,
        check=False,
    )

    assert crashed.returncode == 23
    assert (version_root / "one.txt").read_text(encoding="utf-8") == "one-draft"
    assert (version_root / "two.txt").read_text(encoding="utf-8") == "two-base"

    recovering = _FailBeforeWriteWorkspace(managed_root)
    with pytest.raises(OSError, match="stop after journal recovery"):
        recovering.apply_changeset(
            project_id="project",
            version_id="version",
            mutations=(("one.txt", "next"),),
        )

    assert (version_root / "one.txt").read_text(encoding="utf-8") == "one-base"
    assert (version_root / "two.txt").read_text(encoding="utf-8") == "two-base"
