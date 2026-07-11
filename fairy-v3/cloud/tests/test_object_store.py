from __future__ import annotations

import hashlib

import pytest

from fairy_cloud.storage.objects import (
    ImmutableObjectConflict,
    ObjectIntegrityError,
    S3ObjectStore,
)


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], dict] = {}

    def put_object(self, **request):
        identity = (request["Bucket"], request["Key"])
        if identity in self.objects and request.get("IfNoneMatch") == "*":
            error = RuntimeError("PreconditionFailed")
            error.response = {"Error": {"Code": "PreconditionFailed"}}
            raise error
        self.objects[identity] = request
        return {"ETag": "immutable"}

    def head_object(self, **request):
        stored = self.objects[(request["Bucket"], request["Key"])]
        return {
            "ContentLength": len(stored["Body"]),
            "Metadata": stored["Metadata"],
        }

    def get_object(self, **request):
        stored = self.objects[(request["Bucket"], request["Key"])]

        class Body:
            def read(self) -> bytes:
                return stored["Body"]

            def close(self) -> None:
                return None

        return {"Body": Body(), "ContentLength": len(stored["Body"])}


def test_snapshot_write_is_immutable_and_checksum_addressed() -> None:
    client = FakeS3Client()
    store = S3ObjectStore(client=client, bucket="fairy-objects")
    payload = b"compressed snapshot"

    location = store.put_version_snapshot(
        user_id="user-1",
        project_id="project-1",
        version_id="version-1",
        payload=payload,
    )

    request = client.objects[("fairy-objects", location.key)]
    assert location.sha256 == hashlib.sha256(payload).hexdigest()
    assert request["IfNoneMatch"] == "*"
    assert request["Metadata"]["sha256"] == location.sha256
    assert location.key == "users/user-1/projects/project-1/versions/version-1/snapshot.zst"

    with pytest.raises(ImmutableObjectConflict):
        store.put_version_snapshot(
            user_id="user-1",
            project_id="project-1",
            version_id="version-1",
            payload=b"different",
        )


@pytest.mark.parametrize("unsafe_id", ["../other", "a/b", "a\\b", "", "."])
def test_snapshot_keys_reject_path_like_identifiers(unsafe_id: str) -> None:
    store = S3ObjectStore(client=FakeS3Client(), bucket="fairy-objects")

    with pytest.raises(ValueError):
        store.put_version_snapshot(
            user_id="user-1",
            project_id=unsafe_id,
            version_id="version-1",
            payload=b"snapshot",
        )


def test_snapshot_manifest_is_verified_against_object_metadata() -> None:
    client = FakeS3Client()
    store = S3ObjectStore(client=client, bucket="fairy-objects")
    location = store.put_version_snapshot(
        user_id="user-1",
        project_id="project-1",
        version_id="version-1",
        payload=b"snapshot",
    )

    verified = store.verify_version_snapshot(
        user_id="user-1",
        project_id="project-1",
        version_id="version-1",
        expected_sha256=location.sha256,
        expected_size=location.size,
    )
    assert verified == location

    with pytest.raises(ObjectIntegrityError):
        store.verify_version_snapshot(
            user_id="user-1",
            project_id="project-1",
            version_id="version-1",
            expected_sha256="0" * 64,
            expected_size=location.size,
        )


def test_tenant_document_store_is_content_addressed_idempotent_and_read_verified() -> None:
    client = FakeS3Client()
    documents = S3ObjectStore(
        client=client,
        bucket="fairy-objects",
    ).document_blob_store("tenant-1")
    content = b"managed cloud evidence"
    digest = hashlib.sha256(content).hexdigest()

    first = documents.put(
        content_hash=digest,
        content=content,
        media_type="text/plain",
    )
    replay = documents.put(
        content_hash=digest,
        content=content,
        media_type="text/plain",
    )

    assert replay == first
    assert first.storage_location == (
        f"s3://fairy-objects/tenants/tenant-1/documents/sha256/{digest[:2]}/{digest}"
    )
    assert documents.read(first) == content

    key = first.storage_location.removeprefix("s3://fairy-objects/")
    client.objects[("fairy-objects", key)]["Body"] = b"tampered"
    with pytest.raises(ObjectIntegrityError, match="document"):
        documents.read(first)


def test_tenant_document_store_rejects_declared_hash_mismatch() -> None:
    documents = S3ObjectStore(
        client=FakeS3Client(),
        bucket="fairy-objects",
    ).document_blob_store("tenant-1")

    with pytest.raises(ObjectIntegrityError, match="declared hash"):
        documents.put(
            content_hash="0" * 64,
            content=b"different",
            media_type="text/plain",
        )
