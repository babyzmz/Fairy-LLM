from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import UUID

import pytest

from fairy_core.transports.stdio import build_local_service
from fairy_core.workspace.object_store import (
    AssetMutation,
    FileSystemWorkspaceObjectStore,
    FileTooLargeError,
    ObjectQuotaExceededError,
    WorkspaceStoragePolicy,
)


def test_object_store_streams_atomically_and_deduplicates(tmp_path: Path) -> None:
    store = FileSystemWorkspaceObjectStore(
        tmp_path / "managed",
        policy=WorkspaceStoragePolicy(
            max_file_bytes=32,
            max_workspace_bytes=64,
            derivative_cache_bytes=64,
            min_free_bytes=1,
        ),
    )
    source = tmp_path / "source.bin"
    source.write_bytes(b"fairy-object")
    expected = hashlib.sha256(source.read_bytes()).hexdigest()

    first = store.put_file(source, expected_hash=expected)
    second = store.put_file(source, expected_hash=expected)
    target = tmp_path / "version" / "asset.bin"
    store.materialize(first, target)

    assert first == second
    assert first.storage_path.read_bytes() == b"fairy-object"
    assert target.read_bytes() == b"fairy-object"
    assert not tuple((tmp_path / "managed" / ".transactions" / "objects").iterdir())


def test_object_store_enforces_file_and_workspace_quotas(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"0123456789")
    store = FileSystemWorkspaceObjectStore(
        tmp_path / "managed",
        policy=WorkspaceStoragePolicy(
            max_file_bytes=8,
            max_workspace_bytes=16,
            derivative_cache_bytes=16,
            min_free_bytes=1,
        ),
    )
    with pytest.raises(FileTooLargeError):
        store.put_file(source)

    workspace_limited = FileSystemWorkspaceObjectStore(
        tmp_path / "managed-2",
        policy=WorkspaceStoragePolicy(
            max_file_bytes=12,
            max_workspace_bytes=12,
            derivative_cache_bytes=16,
            min_free_bytes=1,
        ),
    )
    with pytest.raises(ObjectQuotaExceededError):
        workspace_limited.put_file(source, workspace_bytes=3)


def test_asset_mutation_fences_paths_and_existing_targets(tmp_path: Path) -> None:
    source = tmp_path / "asset.bin"
    source.write_bytes(b"asset")
    digest = hashlib.sha256(b"asset").hexdigest()

    mutation = AssetMutation(
        operation="update",
        path="media/asset.bin",
        source=source,
        expected_source_hash=digest,
        expected_target_hash=digest,
    )

    assert mutation.path == "media/asset.bin"
    with pytest.raises(ValueError, match="Workspace-relative"):
        AssetMutation(operation="create", path="../escape.bin", source=source)
    with pytest.raises(ValueError, match="cannot expect"):
        AssetMutation(
            operation="create",
            path="asset.bin",
            source=source,
            expected_target_hash=digest,
        )


def test_binary_workspace_reads_use_revocable_loopback_ranges(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    payload = bytes(range(64))
    (source / "asset.bin").write_bytes(payload)
    service = build_local_service(tmp_path / "data")
    try:
        project = service.invoke(
            "projects.import",
            {"name": "Binary", "residency": "local_only", "source_path": str(source)},
        )
        workspace_id = project["project"]["workspace_id"]
        version_id = project["initial_version"]["id"]
        inline = service.invoke(
            "workspaces.files.read",
            {"workspace_id": workspace_id, "version_id": version_id, "path": "asset.bin"},
        )
        session = service.invoke(
            "files.open_stream",
            {"workspace_id": workspace_id, "version_id": version_id, "path": "asset.bin"},
        )

        assert inline["text"] is None
        assert inline["stream_required"] is True
        request = Request(session["url"], headers={"Range": "bytes=8-15"})
        with urlopen(request, timeout=2) as response:
            assert response.status == 206
            assert response.headers["Content-Range"] == "bytes 8-15/64"
            assert response.read() == payload[8:16]

        service._application._workspaces.revoke_read_session(  # type: ignore[attr-defined]
            UUID(session["session_id"])
        )
        with pytest.raises(HTTPError):
            urlopen(session["url"], timeout=2)
    finally:
        service.close()
