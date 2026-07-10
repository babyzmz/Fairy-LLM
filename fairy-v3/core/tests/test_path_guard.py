from __future__ import annotations

import os
from pathlib import Path

import pytest

from fairy_core.domain.errors import ScopeViolationError
from fairy_core.security.path_guard import PathGuard


def test_allows_relative_path_inside_allowed_root(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    guard = PathGuard(project_root=root, allowed_roots=(root,), forbidden_roots=())

    resolved = guard.validate_write("src/app.ts")

    assert resolved == (root / "src" / "app.ts").resolve(strict=False)


@pytest.mark.parametrize(
    "candidate",
    (
        "../outside.txt",
        r"\\server\share\secret.txt",
        r"\\?\C:\Windows\System32\drivers\etc\hosts",
        "src/file.txt:secret-stream",
    ),
)
def test_rejects_windows_path_escape_forms(tmp_path: Path, candidate: str) -> None:
    root = tmp_path / "project"
    root.mkdir()
    guard = PathGuard(project_root=root, allowed_roots=(root,), forbidden_roots=())

    with pytest.raises(ScopeViolationError) as exc_info:
        guard.validate_write(candidate)

    assert exc_info.value.code == "PATH_OUT_OF_SCOPE"


def test_forbidden_root_wins_over_broader_allowed_root(tmp_path: Path) -> None:
    root = tmp_path / "project"
    forbidden = root / ".git"
    forbidden.mkdir(parents=True)
    guard = PathGuard(
        project_root=root,
        allowed_roots=(root,),
        forbidden_roots=(forbidden,),
    )

    with pytest.raises(ScopeViolationError):
        guard.validate_write(".git/config")


def test_existing_symlink_cannot_escape_project_root(tmp_path: Path) -> None:
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    link = root / "linked"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    guard = PathGuard(project_root=root, allowed_roots=(root,), forbidden_roots=())

    with pytest.raises(ScopeViolationError):
        guard.validate_write("linked/stolen.txt")


def test_revalidation_detects_parent_path_identity_change(tmp_path: Path) -> None:
    root = tmp_path / "project"
    parent = root / "src"
    parent.mkdir(parents=True)
    guard = PathGuard(project_root=root, allowed_roots=(root,), forbidden_roots=())
    lease = guard.issue_write_lease("src/app.ts")
    parent.rmdir()
    parent.mkdir()

    with pytest.raises(ScopeViolationError) as exc_info:
        guard.revalidate_write_lease(lease)

    assert exc_info.value.code == "PATH_IDENTITY_CHANGED"
