from __future__ import annotations

import hashlib

import pytest

from fairy_capabilities.documents.extract import (
    DocumentBlobIntegrityError,
    ManagedFileDocumentStore,
)


def test_managed_store_copies_content_without_modifying_source(tmp_path) -> None:
    source = tmp_path / "source.md"
    source.write_bytes(b"# Fairy\n\nProject-first.")
    source_stat = source.stat()
    content = source.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    store = ManagedFileDocumentStore(tmp_path / "managed")

    first = store.put(content_hash=digest, content=content, media_type="text/markdown")
    second = store.put(content_hash=digest, content=content, media_type="text/markdown")

    assert first == second
    assert first.storage_location == f"managed://sha256/{digest}"
    assert str(tmp_path) not in first.storage_location
    assert store.read(first) == content
    assert source.read_bytes() == content
    assert source.stat().st_mtime_ns == source_stat.st_mtime_ns


def test_managed_store_rejects_declared_hash_mismatch(tmp_path) -> None:
    store = ManagedFileDocumentStore(tmp_path / "managed")

    with pytest.raises(DocumentBlobIntegrityError, match="declared hash"):
        store.put(
            content_hash="0" * 64,
            content=b"different",
            media_type="text/plain",
        )


def test_managed_store_detects_tampering_on_read(tmp_path) -> None:
    content = b"immutable document"
    digest = hashlib.sha256(content).hexdigest()
    root = tmp_path / "managed"
    store = ManagedFileDocumentStore(root)
    blob = store.put(content_hash=digest, content=content, media_type="text/plain")
    stored_path = root / "sha256" / digest[:2] / digest
    stored_path.write_bytes(b"tampered")

    with pytest.raises(DocumentBlobIntegrityError, match="integrity"):
        store.read(blob)
