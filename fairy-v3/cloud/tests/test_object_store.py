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
