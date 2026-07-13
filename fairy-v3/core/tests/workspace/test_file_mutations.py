from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest

from fairy_core.contracts.models import FileMutation, FileMutationOperation
from fairy_core.workspace.filesystem import FileSystemWorkspaceProvisioner
from fairy_core.workspace.mutations import encode_mutation


def test_strict_file_mutations_are_atomic_and_support_binary_content(tmp_path: Path) -> None:
    workspace = FileSystemWorkspaceProvisioner(tmp_path / "managed")
    root = workspace.create_initial_version("workspace", "version")
    (root / "old.txt").write_text("old", encoding="utf-8")
    (root / "delete.txt").write_text("remove", encoding="utf-8")

    mutations = (
        FileMutation(
            operation=FileMutationOperation.UPDATE,
            path="old.txt",
            content="updated",
            expected_hash=_hash(b"old"),
        ),
        FileMutation(
            operation=FileMutationOperation.RENAME,
            path="delete.txt",
            destination_path="archive/kept.txt",
            expected_hash=_hash(b"remove"),
        ),
        FileMutation(
            operation=FileMutationOperation.CREATE,
            path="image.bin",
            content_base64=base64.b64encode(b"\x00\x01\xff").decode("ascii"),
        ),
    )

    workspace.apply_changeset(
        project_id="workspace",
        version_id="version",
        mutations=tuple(
            (
                mutation.path,
                encode_mutation(mutation, expected_workspace_revision=4),
            )
            for mutation in mutations
        ),
    )

    assert (root / "old.txt").read_text(encoding="utf-8") == "updated"
    assert not (root / "delete.txt").exists()
    assert (root / "archive/kept.txt").read_text(encoding="utf-8") == "remove"
    assert (root / "image.bin").read_bytes() == b"\x00\x01\xff"


def test_hash_conflict_rejects_the_whole_file_batch(tmp_path: Path) -> None:
    workspace = FileSystemWorkspaceProvisioner(tmp_path / "managed")
    root = workspace.create_initial_version("workspace", "version")
    (root / "one.txt").write_text("one", encoding="utf-8")
    (root / "two.txt").write_text("two", encoding="utf-8")
    mutations = (
        FileMutation(
            operation=FileMutationOperation.UPDATE,
            path="one.txt",
            content="changed",
            expected_hash=_hash(b"one"),
        ),
        FileMutation(
            operation=FileMutationOperation.DELETE,
            path="two.txt",
            expected_hash="0" * 64,
        ),
    )

    with pytest.raises(ValueError, match="hash conflict"):
        workspace.apply_changeset(
            project_id="workspace",
            version_id="version",
            mutations=tuple(
                (
                    mutation.path,
                    encode_mutation(mutation, expected_workspace_revision=0),
                )
                for mutation in mutations
            ),
        )

    assert (root / "one.txt").read_text(encoding="utf-8") == "one"
    assert (root / "two.txt").read_text(encoding="utf-8") == "two"


def _hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
