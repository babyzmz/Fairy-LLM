from __future__ import annotations

import hashlib
import os
from uuid import uuid4

import boto3
import pytest

from fairy_cloud.storage.objects import ImmutableObjectConflict, S3ObjectStore

S3_ENDPOINT = os.environ.get("FAIRY_TEST_S3_ENDPOINT")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not S3_ENDPOINT, reason="FAIRY_TEST_S3_ENDPOINT is not set"),
]


def test_s3_snapshot_is_conditionally_immutable() -> None:
    assert S3_ENDPOINT is not None
    bucket = os.environ.get("FAIRY_TEST_S3_BUCKET", "fairy-objects")
    client = boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=os.environ.get("FAIRY_S3_ACCESS_KEY", "fairy-dev"),
        aws_secret_access_key=os.environ.get("FAIRY_S3_SECRET_KEY", "fairy-dev-secret"),
        region_name="us-east-1",
    )
    store = S3ObjectStore(client=client, bucket=bucket)
    version_id = f"version-{uuid4().hex}"
    location = store.put_version_snapshot(
        user_id="integration-user",
        project_id="integration-project",
        version_id=version_id,
        payload=b"snapshot",
    )
    try:
        with pytest.raises(ImmutableObjectConflict):
            store.put_version_snapshot(
                user_id="integration-user",
                project_id="integration-project",
                version_id=version_id,
                payload=b"replacement",
            )
    finally:
        client.delete_object(Bucket=bucket, Key=location.key)


def test_s3_document_blob_is_content_addressed_and_read_verified() -> None:
    assert S3_ENDPOINT is not None
    bucket = os.environ.get("FAIRY_TEST_S3_BUCKET", "fairy-objects")
    client = boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=os.environ.get("FAIRY_S3_ACCESS_KEY", "fairy-dev"),
        aws_secret_access_key=os.environ.get("FAIRY_S3_SECRET_KEY", "fairy-dev-secret"),
        region_name="us-east-1",
    )
    tenant_id = f"integration-{uuid4().hex}"
    documents = S3ObjectStore(client=client, bucket=bucket).document_blob_store(tenant_id)
    content = b"managed integration document"
    digest = hashlib.sha256(content).hexdigest()
    blob = documents.put(
        content_hash=digest,
        content=content,
        media_type="text/plain",
    )
    key = blob.storage_location.removeprefix(f"s3://{bucket}/")
    try:
        assert documents.read(blob) == content
        assert (
            documents.put(
                content_hash=digest,
                content=content,
                media_type="text/plain",
            )
            == blob
        )
    finally:
        client.delete_object(Bucket=bucket, Key=key)
