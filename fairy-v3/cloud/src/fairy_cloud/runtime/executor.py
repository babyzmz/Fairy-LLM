from __future__ import annotations

import hashlib
import re
import time
from contextlib import suppress
from typing import Protocol
from urllib.parse import urlsplit
from uuid import UUID

from fairy_core.domain.execution import RuntimeKind
from fairy_core.persistence.tenant import normalize_tenant_id
from fairy_core.runtime.models import (
    DynamicRuntimeStart,
    ExecutorRuntimeState,
    RuntimeExecutorError,
    RuntimeExecutorHealth,
    RuntimeProbeResult,
    RuntimeRecoveryTarget,
    RuntimeStartResult,
    RuntimeStopResult,
    StaticRuntimeStart,
)

from fairy_cloud.runtime.models import CloudRuntimeLease, CloudRuntimeStatus
from fairy_cloud.runtime.tokens import CloudPreviewBinding, CloudPreviewSigner

_HANDLE = re.compile(
    r"^cloud-dynamic:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}):([1-9][0-9]*)$"
)


class CloudRuntimeStore(Protocol):
    def health(self) -> RuntimeExecutorHealth: ...

    def enqueue(self, request: DynamicRuntimeStart) -> CloudRuntimeLease: ...

    def get(self, runtime_id: UUID) -> CloudRuntimeLease | None: ...

    def request_stop(
        self,
        runtime_id: UUID,
        *,
        request_lease_fence: int,
    ) -> CloudRuntimeLease: ...

    def register_route(self, binding: CloudPreviewBinding, token_hash: str) -> None: ...


class CloudRuntimeExecutor:
    def __init__(
        self,
        *,
        tenant_id: str,
        store: CloudRuntimeStore,
        preview_base_url: str,
        signer: CloudPreviewSigner,
        poll_seconds: float = 0.05,
        sleep=time.sleep,
        monotonic=time.monotonic,
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError("Cloud Runtime polling interval must be positive")
        parsed = urlsplit(preview_base_url.rstrip("/"))
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 443}
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Cloud Runtime Preview base URL must be an HTTPS origin")
        self._tenant_id = normalize_tenant_id(tenant_id)
        self._store = store
        self._base_domain = parsed.hostname
        self._signer = signer
        self._poll_seconds = poll_seconds
        self._sleep = sleep
        self._monotonic = monotonic

    def health(self) -> RuntimeExecutorHealth:
        return self._store.health()

    def start_static(self, _request: StaticRuntimeStart) -> RuntimeStartResult:
        raise RuntimeExecutorError(
            "Cloud Runtime executor does not serve local static Previews",
            error_code="CAPABILITY_NOT_AVAILABLE",
        )

    def start_dynamic(self, request: DynamicRuntimeStart) -> RuntimeStartResult:
        if request.execution_target != "cloud" or request.kind is not RuntimeKind.CLOUD_OCI:
            raise RuntimeExecutorError(
                "Cloud Runtime request does not match cloud Scope",
                error_code="SCOPE_MISMATCH",
            )
        self._require_health()
        lease = self._store.enqueue(request)
        self._validate_request_binding(lease, request)
        try:
            lease = self._await(
                request.runtime_id,
                expected={CloudRuntimeStatus.RUNNING},
                timeout_seconds=request.startup_timeout_seconds + 20,
            )
            self._validate_request_binding(lease, request)
            url = self._proxy_url(lease)
        except Exception:
            self._request_stop_best_effort(request.runtime_id, request.lease_fence)
            raise
        return RuntimeStartResult(
            executor_handle=_handle(lease),
            host=urlsplit(url).hostname or "",
            port=443,
            url=url,
            state=ExecutorRuntimeState.RUNNING,
            execution_target="cloud",
        )

    def probe(self, executor_handle: str) -> RuntimeProbeResult:
        runtime_id, request_fence = _parse_handle(executor_handle)
        lease = self._require_lease(runtime_id, request_fence)
        state = {
            CloudRuntimeStatus.RUNNING: ExecutorRuntimeState.RUNNING,
            CloudRuntimeStatus.STOPPED: ExecutorRuntimeState.STOPPED,
        }.get(lease.status, ExecutorRuntimeState.INTERRUPTED)
        if state is ExecutorRuntimeState.RUNNING:
            url = self._proxy_url(lease)
            return RuntimeProbeResult(
                executor_handle=executor_handle,
                state=state,
                host=urlsplit(url).hostname or "",
                port=443,
                url=url,
                execution_target="cloud",
            )
        return RuntimeProbeResult(
            executor_handle=executor_handle,
            state=state,
            host=None,
            port=None,
            url=None,
            execution_target="cloud",
        )

    def recovery_handle(self, target: RuntimeRecoveryTarget) -> str:
        if target.kind is not RuntimeKind.CLOUD_OCI or target.execution_target != "cloud":
            raise RuntimeExecutorError(
                "Cloud Runtime recovery target is invalid",
                error_code="SCOPE_MISMATCH",
            )
        return f"cloud-dynamic:{target.runtime_id}:{target.lease_fence}"

    def stop(self, executor_handle: str) -> RuntimeStopResult:
        runtime_id, request_fence = _parse_handle(executor_handle)
        self._require_lease(runtime_id, request_fence)
        self._store.request_stop(
            runtime_id,
            request_lease_fence=request_fence,
        )
        self._await(
            runtime_id,
            expected={CloudRuntimeStatus.STOPPED},
            timeout_seconds=30,
        )
        return RuntimeStopResult(stopped=True)

    def _require_health(self) -> None:
        health = self.health()
        if (
            not health.available
            or health.executor != "cloud_oci_runtime"
            or health.version != "1.0.0"
        ):
            raise RuntimeExecutorError(
                "Cloud OCI Runtime worker is unavailable",
                error_code=health.error_code or "SANDBOX_UNAVAILABLE",
            )

    def _await(
        self,
        runtime_id: UUID,
        *,
        expected: set[CloudRuntimeStatus],
        timeout_seconds: float,
    ) -> CloudRuntimeLease:
        deadline = self._monotonic() + timeout_seconds
        while True:
            lease = self._store.get(runtime_id)
            if lease is None:
                raise RuntimeExecutorError(
                    "Cloud Runtime lease disappeared",
                    error_code="WORKER_INTERRUPTED",
                )
            if lease.status in expected:
                return lease
            if lease.status.terminal:
                raise RuntimeExecutorError(
                    "Cloud Runtime worker failed",
                    error_code=lease.error_code or "WORKER_INTERRUPTED",
                )
            if self._monotonic() >= deadline:
                raise RuntimeExecutorError(
                    "Cloud Runtime worker timed out",
                    error_code="WORKER_INTERRUPTED",
                )
            self._sleep(self._poll_seconds)

    def _require_lease(self, runtime_id: UUID, request_fence: int) -> CloudRuntimeLease:
        lease = self._store.get(runtime_id)
        if lease is None:
            raise RuntimeExecutorError(
                "Cloud Runtime lease was not found",
                error_code="WORKER_INTERRUPTED",
            )
        if lease.tenant_id != self._tenant_id or lease.request_lease_fence != request_fence:
            raise RuntimeExecutorError(
                "Cloud Runtime handle does not match its durable lease",
                error_code="SCOPE_MISMATCH",
            )
        return lease

    def _request_stop_best_effort(self, runtime_id: UUID, request_fence: int) -> None:
        with suppress(Exception):
            self._store.request_stop(
                runtime_id,
                request_lease_fence=request_fence,
            )

    @staticmethod
    def _validate_request_binding(
        lease: CloudRuntimeLease,
        request: DynamicRuntimeStart,
    ) -> None:
        if not lease.matches(request):
            raise RuntimeExecutorError(
                "Cloud Runtime lease does not match Core Scope",
                error_code="SCOPE_MISMATCH",
            )

    def _proxy_url(self, lease: CloudRuntimeLease) -> str:
        if lease.status is not CloudRuntimeStatus.RUNNING or lease.internal_url is None:
            raise RuntimeExecutorError(
                "Cloud Runtime has no ready proxy target",
                error_code="WORKER_INTERRUPTED",
            )
        binding = CloudPreviewBinding(
            tenant_id=lease.tenant_id,
            runtime_id=lease.runtime_id,
            preview_id=lease.preview_id,
            task_id=lease.task_id,
            version_id=lease.version_id,
            lease_fence=lease.request_lease_fence,
            expires_at=lease.expires_at,
        )
        token = self._signer.issue(binding)
        self._store.register_route(
            binding,
            hashlib.sha256(token.encode("ascii")).hexdigest(),
        )
        return f"https://{token}.{self._base_domain}/"


def _handle(lease: CloudRuntimeLease) -> str:
    return f"cloud-dynamic:{lease.runtime_id}:{lease.request_lease_fence}"


def _parse_handle(value: str) -> tuple[UUID, int]:
    if not isinstance(value, str):
        raise RuntimeExecutorError(
            "Cloud Runtime executor handle is invalid",
            error_code="SCOPE_MISMATCH",
        )
    match = _HANDLE.fullmatch(value)
    if match is None:
        raise RuntimeExecutorError(
            "Cloud Runtime executor handle is invalid",
            error_code="SCOPE_MISMATCH",
        )
    return UUID(match.group(1)), int(match.group(2))


__all__ = ["CloudRuntimeExecutor", "CloudRuntimeStore"]
