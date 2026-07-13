from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fairy_core.domain.execution import RuntimeKind
from fairy_core.runtime.models import (
    DynamicRuntimeStart,
    ExecutorRuntimeState,
    RuntimeExecutorError,
    RuntimeExecutorHealth,
)

from fairy_cloud.runtime.executor import CloudRuntimeExecutor
from fairy_cloud.runtime.models import CloudRuntimeLease, CloudRuntimeStatus
from fairy_cloud.runtime.tokens import CloudPreviewBinding, CloudPreviewSigner


class FakeStore:
    def __init__(self, lease: CloudRuntimeLease) -> None:
        self.lease = lease
        self.enqueued: list[DynamicRuntimeStart] = []
        self.stop_requests: list[tuple[object, int]] = []
        self.routes: list[tuple[CloudPreviewBinding, str]] = []

    def health(self) -> RuntimeExecutorHealth:
        return RuntimeExecutorHealth(
            available=True,
            executor="cloud_oci_runtime",
            version="1.0.0",
            error_code=None,
            diagnostics=("fixture",),
        )

    def enqueue(self, request: DynamicRuntimeStart) -> CloudRuntimeLease:
        self.enqueued.append(request)
        return self.lease

    def get(self, runtime_id):
        return self.lease if runtime_id == self.lease.runtime_id else None

    def request_stop(self, runtime_id, *, request_lease_fence: int) -> CloudRuntimeLease:
        self.stop_requests.append((runtime_id, request_lease_fence))
        self.lease = replace(
            self.lease,
            status=CloudRuntimeStatus.STOPPED,
            internal_url=None,
            updated_at=datetime.now(UTC),
        )
        return self.lease

    def register_route(self, binding: CloudPreviewBinding, token_hash: str) -> None:
        self.routes.append((binding, token_hash))


def test_cloud_runtime_returns_only_opaque_signed_https_proxy_url(tmp_path: Path) -> None:
    request = _request(tmp_path)
    lease = _lease(request)
    store = FakeStore(lease)
    signer = CloudPreviewSigner(b"k" * 32)
    executor = CloudRuntimeExecutor(
        tenant_id="tenant-a",
        store=store,
        preview_base_url="https://preview.fairy.test",
        signer=signer,
        sleep=lambda _seconds: None,
    )

    result = executor.start_dynamic(request)

    assert result.state is ExecutorRuntimeState.RUNNING
    assert result.execution_target == "cloud"
    assert result.executor_handle == f"cloud-dynamic:{request.runtime_id}:7"
    assert result.host.endswith(".preview.fairy.test")
    assert result.port == 443
    assert result.url == f"https://{result.host}/"
    assert "runtime-worker" not in result.url
    assert lease.internal_url not in result.url
    token = result.host.removesuffix(".preview.fairy.test")
    binding = CloudPreviewBinding(
        tenant_id="tenant-a",
        runtime_id=request.runtime_id,
        preview_id=request.preview_id,
        task_id=request.task_id,
        version_id=request.version_id,
        lease_fence=request.lease_fence,
        expires_at=lease.expires_at,
    )
    signer.verify(token, binding, now=lease.created_at)
    assert binding.tenant_id == "tenant-a"
    assert binding.runtime_id == request.runtime_id
    assert binding.preview_id == request.preview_id
    assert binding.task_id == request.task_id
    assert binding.version_id == request.version_id
    assert binding.lease_fence == request.lease_fence
    assert store.routes == [(binding, hashlib.sha256(token.encode("ascii")).hexdigest())]


def test_cloud_runtime_probe_stop_and_target_binding(tmp_path: Path) -> None:
    request = _request(tmp_path)
    store = FakeStore(_lease(request))
    executor = CloudRuntimeExecutor(
        tenant_id="tenant-a",
        store=store,
        preview_base_url="https://preview.fairy.test",
        signer=CloudPreviewSigner(b"k" * 32),
        sleep=lambda _seconds: None,
    )
    handle = f"cloud-dynamic:{request.runtime_id}:{request.lease_fence}"

    probe = executor.probe(handle)
    stopped = executor.stop(handle)

    assert probe.state is ExecutorRuntimeState.RUNNING
    assert probe.execution_target == "cloud"
    assert stopped.stopped is True
    assert store.stop_requests == [(request.runtime_id, request.lease_fence)]

    store.lease = replace(store.lease, request_lease_fence=8)
    with pytest.raises(RuntimeExecutorError) as captured:
        executor.probe(handle)
    assert captured.value.error_code == "SCOPE_MISMATCH"


def test_cloud_runtime_lease_round_trips_projectless_workspace(tmp_path: Path) -> None:
    request = replace(_request(tmp_path), project_id=None, workspace_id=uuid4())

    lease = _lease(request)
    restored = lease.to_request()

    assert lease.project_id is None
    assert lease.workspace_id == request.workspace_id
    assert restored.project_id is None
    assert restored.workspace_id == request.workspace_id
    assert lease.matches(restored)


def test_cloud_preview_token_rejects_tampering_tenant_and_expiry() -> None:
    signer = CloudPreviewSigner(b"k" * 32)
    now = datetime.now(UTC)
    binding = CloudPreviewBinding(
        tenant_id="tenant-a",
        runtime_id=uuid4(),
        preview_id=uuid4(),
        task_id=uuid4(),
        version_id=uuid4(),
        lease_fence=3,
        expires_at=now + timedelta(minutes=5),
    )
    token = signer.issue(binding)

    assert (
        signer.verify(
            token,
            binding,
            now=now,
            expected_tenant_id="tenant-a",
        )
        is None
    )
    with pytest.raises(ValueError, match="tenant"):
        signer.verify(token, binding, now=now, expected_tenant_id="tenant-b")
    with pytest.raises(ValueError, match="signature"):
        signer.verify(
            ("a" if token[0] != "a" else "b") + token[1:],
            binding,
            now=now,
        )
    with pytest.raises(ValueError, match="expired"):
        signer.verify(token, binding, now=binding.expires_at)


def test_cloud_runtime_requests_fenced_cleanup_when_route_registration_fails(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)

    class FailingRouteStore(FakeStore):
        def register_route(self, binding: CloudPreviewBinding, token_hash: str) -> None:
            del binding, token_hash
            raise RuntimeError("route unavailable")

    store = FailingRouteStore(_lease(request))
    executor = CloudRuntimeExecutor(
        tenant_id="tenant-a",
        store=store,
        preview_base_url="https://preview.fairy.test",
        signer=CloudPreviewSigner(b"k" * 32),
        sleep=lambda _seconds: None,
    )

    with pytest.raises(RuntimeError, match="route unavailable"):
        executor.start_dynamic(request)

    assert store.stop_requests == [(request.runtime_id, request.lease_fence)]


def _request(tmp_path: Path) -> DynamicRuntimeStart:
    archive = b"PK\x03\x04cloud-runtime"
    return DynamicRuntimeStart(
        project_id=uuid4(),
        conversation_id=uuid4(),
        task_id=uuid4(),
        version_id=uuid4(),
        runtime_id=uuid4(),
        preview_id=uuid4(),
        project_root=tmp_path,
        execution_target="cloud",
        kind=RuntimeKind.CLOUD_OCI,
        adapter="vite",
        scope_digest="a" * 64,
        workspace_generation=4,
        lease_fence=7,
        argv=(
            "node_modules/.bin/vite",
            "--host",
            "127.0.0.1",
            "--port",
            "{port}",
            "--strictPort",
        ),
        cwd=".",
        readiness_path="/",
        startup_timeout_seconds=45,
        dependency_key="b" * 64,
        workspace_archive=archive,
        archive_sha256=hashlib.sha256(archive).hexdigest(),
    )


def _lease(request: DynamicRuntimeStart) -> CloudRuntimeLease:
    now = datetime.now(UTC)
    return CloudRuntimeLease.create(
        tenant_id="tenant-a",
        request=request,
        status=CloudRuntimeStatus.RUNNING,
        internal_url=f"http://runtime:8082/internal/{request.runtime_id}/",
        worker_id="runtime-worker-1",
        lease_fence=2,
        expires_at=now + timedelta(hours=1),
        now=now,
    )
