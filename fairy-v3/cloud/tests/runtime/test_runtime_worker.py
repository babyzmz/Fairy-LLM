from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fairy_core.domain.errors import WorkerFenceError
from fairy_core.runtime.models import (
    ExecutorRuntimeState,
    RuntimeProbeResult,
    RuntimeStartResult,
    RuntimeStopResult,
)

from fairy_cloud.runtime.models import (
    CloudRuntimeClaim,
    CloudRuntimeClaimAction,
    CloudRuntimeStatus,
)
from fairy_cloud.workers.runtime import CloudRuntimeWorker

from .test_cloud_runtime import _lease, _request


class FakeStore:
    def __init__(self, claim: CloudRuntimeClaim | None) -> None:
        self.claim = claim
        self.running: list[CloudRuntimeClaim] = []
        self.stopped: list[CloudRuntimeClaim] = []
        self.interrupted: list[tuple[CloudRuntimeClaim, str]] = []
        self.heartbeats: list[str] = []
        self.lease_seconds: list[int] = []

    async def expire_due(self) -> int:
        return 0

    async def claim_next(self, *, owner_id: str, lease_seconds: int):
        del owner_id
        self.lease_seconds.append(lease_seconds)
        claim, self.claim = self.claim, None
        return claim

    async def mark_running(self, claim: CloudRuntimeClaim, *, worker_id: str) -> None:
        assert worker_id == "runtime-worker-1"
        self.running.append(claim)

    async def mark_stopped(self, claim: CloudRuntimeClaim) -> None:
        self.stopped.append(claim)

    async def mark_interrupted(
        self,
        claim: CloudRuntimeClaim,
        *,
        error_code: str = "WORKER_INTERRUPTED",
    ) -> None:
        self.interrupted.append((claim, error_code))

    async def heartbeat(self, *, owner_id: str) -> None:
        self.heartbeats.append(owner_id)


class FakeSupervisor:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.starts = []
        self.stops = []

    def start_dynamic(self, request):
        self.starts.append(request)
        if self.fail:
            raise RuntimeError("process failed")
        return RuntimeStartResult(
            executor_handle=f"cloud-dynamic:{request.runtime_id}:{request.lease_fence}",
            host="127.0.0.1",
            port=43125,
            url="http://127.0.0.1:43125/",
            state=ExecutorRuntimeState.RUNNING,
            execution_target="local",
        )

    def probe(self, handle: str):
        return RuntimeProbeResult(
            executor_handle=handle,
            state=ExecutorRuntimeState.RUNNING,
            host="127.0.0.1",
            port=43125,
            url="http://127.0.0.1:43125/",
        )

    def stop(self, handle: str):
        self.stops.append(handle)
        return RuntimeStopResult(stopped=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action",
    (CloudRuntimeClaimAction.START, CloudRuntimeClaimAction.RECOVER),
)
async def test_runtime_worker_starts_or_recovers_idempotent_supervisor(
    tmp_path: Path,
    action: CloudRuntimeClaimAction,
) -> None:
    request = _request(tmp_path)
    claim = _claim(request, action)
    store = FakeStore(claim)
    supervisor = FakeSupervisor()
    worker = CloudRuntimeWorker(
        store=store,
        supervisor=supervisor,
        owner_id="runtime-worker-1",
    )

    cycle = await worker.run_once()

    assert cycle.claimed == 1 and cycle.running == 1
    assert supervisor.starts == [claim.lease.to_request()]
    assert store.running == [claim]
    assert store.heartbeats == ["runtime-worker-1"]
    assert store.lease_seconds == [180]


@pytest.mark.asyncio
async def test_runtime_worker_stops_with_core_request_fence(tmp_path: Path) -> None:
    request = _request(tmp_path)
    claim = _claim(request, CloudRuntimeClaimAction.STOP)
    store = FakeStore(claim)
    supervisor = FakeSupervisor()
    worker = CloudRuntimeWorker(
        store=store,
        supervisor=supervisor,
        owner_id="runtime-worker-1",
    )

    cycle = await worker.run_once()

    assert cycle.stopped == 1
    assert supervisor.stops == [f"cloud-dynamic:{request.runtime_id}:{request.lease_fence}"]
    assert store.stopped == [claim]


@pytest.mark.asyncio
async def test_runtime_worker_persists_interruption_on_process_failure(tmp_path: Path) -> None:
    request = _request(tmp_path)
    claim = _claim(request, CloudRuntimeClaimAction.START)
    store = FakeStore(claim)
    worker = CloudRuntimeWorker(
        store=store,
        supervisor=FakeSupervisor(fail=True),
        owner_id="runtime-worker-1",
    )

    cycle = await worker.run_once()

    assert cycle.interrupted == 1
    assert store.interrupted == [(claim, "WORKER_INTERRUPTED")]


@pytest.mark.asyncio
async def test_runtime_worker_does_not_overwrite_a_newer_worker_fence(tmp_path: Path) -> None:
    request = _request(tmp_path)
    claim = _claim(request, CloudRuntimeClaimAction.START)

    class LostFenceStore(FakeStore):
        async def mark_running(
            self,
            claim: CloudRuntimeClaim,
            *,
            worker_id: str,
        ) -> None:
            del claim, worker_id
            raise WorkerFenceError("newer worker owns the lease")

        async def mark_interrupted(
            self,
            claim: CloudRuntimeClaim,
            *,
            error_code: str = "WORKER_INTERRUPTED",
        ) -> None:
            del claim, error_code
            pytest.fail("a stale worker must not mark the Runtime interrupted")

    store = LostFenceStore(claim)
    worker = CloudRuntimeWorker(
        store=store,
        supervisor=FakeSupervisor(),
        owner_id="runtime-worker-1",
    )

    cycle = await worker.run_once()

    assert cycle.claimed == 1
    assert cycle.running == 0
    assert cycle.stopped == 0
    assert cycle.interrupted == 0
    assert store.interrupted == []


def _claim(request, action: CloudRuntimeClaimAction) -> CloudRuntimeClaim:
    lease = _lease(request)
    status = {
        CloudRuntimeClaimAction.START: CloudRuntimeStatus.STARTING,
        CloudRuntimeClaimAction.RECOVER: CloudRuntimeStatus.RUNNING,
        CloudRuntimeClaimAction.STOP: CloudRuntimeStatus.STOPPING,
    }[action]
    return CloudRuntimeClaim(
        replace(
            lease,
            status=status,
            internal_url=(lease.internal_url if status is CloudRuntimeStatus.RUNNING else None),
            lease_owner="runtime-worker-1",
            lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
        ),
        action,
    )
