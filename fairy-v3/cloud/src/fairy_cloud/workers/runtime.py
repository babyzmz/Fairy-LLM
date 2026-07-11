from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

import uvicorn
from fairy_core.domain.errors import WorkerFenceError
from fairy_core.runtime.http_review import HttpRuntimeReviewer
from fairy_core.runtime.models import (
    DynamicRuntimeStart,
    ExecutorRuntimeState,
    RuntimeProbeResult,
    RuntimeStartResult,
    RuntimeStopResult,
)
from fairy_core.runtime.review import RuntimeReviewer
from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import create_async_engine
from starlette.responses import JSONResponse, Response

from fairy_cloud.runtime.models import (
    CloudRuntimeClaim,
    CloudRuntimeClaimAction,
    CloudRuntimeReviewBinding,
)
from fairy_cloud.runtime.proxy import RuntimeInternalGateway
from fairy_cloud.runtime.repository import AsyncCloudRuntimeRepository
from fairy_cloud.runtime.review import CloudRuntimeReviewWire
from fairy_cloud.runtime.supervisor import OciDynamicRuntimeSupervisor
from fairy_cloud.settings import RuntimeWorkerSettings

logger = logging.getLogger(__name__)


class RuntimeWorkerStore(Protocol):
    async def expire_due(self) -> int: ...

    async def claim_next(
        self,
        *,
        owner_id: str,
        lease_seconds: int,
    ) -> CloudRuntimeClaim | None: ...

    async def mark_running(
        self,
        claim: CloudRuntimeClaim,
        *,
        worker_id: str,
    ) -> None: ...

    async def mark_stopped(self, claim: CloudRuntimeClaim) -> None: ...

    async def mark_interrupted(
        self,
        claim: CloudRuntimeClaim,
        *,
        error_code: str = "WORKER_INTERRUPTED",
    ) -> None: ...

    async def heartbeat(self, *, owner_id: str) -> None: ...

    async def active_handle(self, runtime_id: UUID) -> str: ...

    async def active_review_binding(
        self,
        runtime_id: UUID,
    ) -> CloudRuntimeReviewBinding: ...


class RuntimeProcessSupervisor(Protocol):
    def start_dynamic(self, request: DynamicRuntimeStart) -> RuntimeStartResult: ...

    def probe(self, executor_handle: str) -> RuntimeProbeResult: ...

    def stop(self, executor_handle: str) -> RuntimeStopResult: ...


@dataclass(frozen=True, slots=True)
class RuntimeWorkerCycle:
    claimed: int = 0
    running: int = 0
    stopped: int = 0
    interrupted: int = 0


class CloudRuntimeWorker:
    def __init__(
        self,
        *,
        store: RuntimeWorkerStore,
        supervisor: RuntimeProcessSupervisor,
        owner_id: str,
        lease_seconds: int = 180,
    ) -> None:
        if not owner_id.strip() or lease_seconds < 1:
            raise ValueError("Cloud Runtime worker identity and lease are required")
        self._store = store
        self._supervisor = supervisor
        self._owner_id = owner_id
        self._lease_seconds = lease_seconds

    async def run_once(self) -> RuntimeWorkerCycle:
        expired = await self._store.expire_due()
        cycle = RuntimeWorkerCycle(interrupted=expired)
        try:
            claim = await self._store.claim_next(
                owner_id=self._owner_id,
                lease_seconds=self._lease_seconds,
            )
            if claim is None:
                return cycle
            if claim.action is CloudRuntimeClaimAction.STOP:
                return await self._stop(claim, cycle)
            return await self._start_or_recover(claim, cycle)
        finally:
            await self._store.heartbeat(owner_id=self._owner_id)

    async def _start_or_recover(
        self,
        claim: CloudRuntimeClaim,
        cycle: RuntimeWorkerCycle,
    ) -> RuntimeWorkerCycle:
        request = claim.lease.to_request()
        try:
            result = await asyncio.to_thread(self._supervisor.start_dynamic, request)
            expected_handle = f"cloud-dynamic:{request.runtime_id}:{request.lease_fence}"
            if (
                result.executor_handle != expected_handle
                or result.state is not ExecutorRuntimeState.RUNNING
                or result.host != "127.0.0.1"
                or result.execution_target != "local"
            ):
                raise ValueError("Runtime supervisor rebound the internal endpoint")
        except Exception as error:
            return await self._record_interruption(claim, cycle, error, action="start/recovery")
        try:
            await self._store.mark_running(claim, worker_id=self._owner_id)
            return RuntimeWorkerCycle(
                claimed=cycle.claimed + 1,
                running=cycle.running + 1,
                stopped=cycle.stopped,
                interrupted=cycle.interrupted,
            )
        except WorkerFenceError:
            logger.info("Cloud Runtime start/recovery result lost its worker fence")
            return RuntimeWorkerCycle(
                claimed=cycle.claimed + 1,
                running=cycle.running,
                stopped=cycle.stopped,
                interrupted=cycle.interrupted,
            )

    async def _stop(
        self,
        claim: CloudRuntimeClaim,
        cycle: RuntimeWorkerCycle,
    ) -> RuntimeWorkerCycle:
        handle = f"cloud-dynamic:{claim.lease.runtime_id}:{claim.lease.request_lease_fence}"
        try:
            result = await asyncio.to_thread(self._supervisor.stop, handle)
            if result.stopped is not True:
                raise ValueError("Runtime supervisor did not confirm stop")
        except Exception as error:
            return await self._record_interruption(claim, cycle, error, action="stop")
        try:
            await self._store.mark_stopped(claim)
            return RuntimeWorkerCycle(
                claimed=cycle.claimed + 1,
                running=cycle.running,
                stopped=cycle.stopped + 1,
                interrupted=cycle.interrupted,
            )
        except WorkerFenceError:
            logger.info("Cloud Runtime stop result lost its worker fence")
            return RuntimeWorkerCycle(
                claimed=cycle.claimed + 1,
                running=cycle.running,
                stopped=cycle.stopped,
                interrupted=cycle.interrupted,
            )

    async def _record_interruption(
        self,
        claim: CloudRuntimeClaim,
        cycle: RuntimeWorkerCycle,
        error: Exception,
        *,
        action: str,
    ) -> RuntimeWorkerCycle:
        logger.exception("Cloud Runtime %s failed", action, exc_info=error)
        interrupted = 0
        try:
            await self._store.mark_interrupted(
                claim,
                error_code=str(getattr(error, "error_code", "WORKER_INTERRUPTED")),
            )
            interrupted = 1
        except WorkerFenceError:
            logger.info("Cloud Runtime interruption lost its worker fence")
        return RuntimeWorkerCycle(
            claimed=cycle.claimed + 1,
            running=cycle.running,
            stopped=cycle.stopped,
            interrupted=cycle.interrupted + interrupted,
        )

    async def run_forever(
        self,
        *,
        stop: asyncio.Event,
        poll_seconds: float,
        heartbeat_path: Path,
    ) -> None:
        while not stop.is_set():
            try:
                await self.run_once()
                heartbeat_path.touch()
            except Exception:
                logger.exception("Cloud Runtime worker cycle failed")
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=poll_seconds)


class RuntimeTargetResolver:
    def __init__(
        self,
        *,
        store: RuntimeWorkerStore,
        supervisor: RuntimeProcessSupervisor,
    ) -> None:
        self._store = store
        self._supervisor = supervisor

    async def resolve_internal(self, runtime_id: str) -> str:
        parsed = UUID(runtime_id)
        if str(parsed) != runtime_id:
            raise ValueError("Runtime id must be canonical")
        handle = await self._store.active_handle(parsed)
        result = await asyncio.to_thread(self._supervisor.probe, handle)
        if (
            result.state is not ExecutorRuntimeState.RUNNING
            or result.executor_handle != handle
            or result.host != "127.0.0.1"
            or result.port is None
            or result.url is None
        ):
            raise RuntimeError("Runtime supervisor endpoint is not healthy")
        return result.url

    async def resolve_review(self, request: CloudRuntimeReviewWire) -> str:
        binding = await self._store.active_review_binding(request.runtime_id)
        expected = {
            "runtime_id": request.runtime_id,
            "preview_id": request.preview_id,
            "project_id": request.project_id,
            "conversation_id": request.conversation_id,
            "task_id": request.task_id,
            "version_id": request.version_id,
            "scope_digest": request.scope_digest,
            "workspace_generation": request.workspace_generation,
        }
        if any(getattr(binding, name) != value for name, value in expected.items()):
            raise ValueError("Runtime Review binding does not match the active lease")
        return await self.resolve_internal(str(request.runtime_id))


def create_runtime_worker_app(
    *,
    worker: CloudRuntimeWorker,
    resolver: RuntimeTargetResolver,
    poll_seconds: float,
    heartbeat_path: Path,
    gateway_key: str,
    reviewer: RuntimeReviewer,
    on_close=None,
) -> FastAPI:
    stop = asyncio.Event()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = asyncio.create_task(
            worker.run_forever(
                stop=stop,
                poll_seconds=poll_seconds,
                heartbeat_path=heartbeat_path,
            )
        )
        try:
            yield
        finally:
            stop.set()
            await task
            if on_close is not None:
                await on_close()

    app = FastAPI(title="Fairy Runtime Worker", lifespan=lifespan)
    RuntimeInternalGateway(resolver=resolver, gateway_key=gateway_key).install(app)

    @app.get("/internal-review/health")
    async def review_health(request: Request) -> Response:
        if not _gateway_authorized(request, gateway_key):
            return _review_unavailable()
        health = reviewer.health()
        return JSONResponse(
            {
                "schema_version": 1,
                "http_available": health.http_available,
                "browser_available": health.browser_available,
                "diagnostics": list(health.diagnostics),
            }
        )

    @app.post("/internal-review/{runtime_id}/health")
    async def check_runtime(
        runtime_id: str,
        body: CloudRuntimeReviewWire,
        request: Request,
    ) -> Response:
        if not _gateway_authorized(request, gateway_key) or runtime_id != str(body.runtime_id):
            return _review_unavailable()
        try:
            url = await resolver.resolve_review(body)
            result = await asyncio.to_thread(reviewer.check, body.local_request(url))
        except Exception:
            return _review_unavailable()
        return JSONResponse(asdict(result))

    @app.post("/internal-review/{runtime_id}/capture")
    async def capture_runtime(
        runtime_id: str,
        body: CloudRuntimeReviewWire,
        request: Request,
    ) -> Response:
        if not _gateway_authorized(request, gateway_key) or runtime_id != str(body.runtime_id):
            return _review_unavailable()
        try:
            url = await resolver.resolve_review(body)
            capture = await asyncio.to_thread(reviewer.capture, body.local_request(url))
        except Exception:
            return _review_unavailable()
        return Response(
            content=capture.png,
            media_type="image/png",
            headers={
                "X-Fairy-Image-Width": str(capture.width),
                "X-Fairy-Image-Height": str(capture.height),
            },
        )

    @app.get("/ready")
    async def ready() -> dict[str, str]:
        return {"status": "ready"}

    return app


def _gateway_authorized(request: Request, key: str) -> bool:
    supplied = request.headers.get("X-Fairy-Runtime-Gateway-Key")
    return supplied is not None and hmac.compare_digest(supplied, key)


def _review_unavailable() -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"detail": {"code": "RUNTIME_NOT_AVAILABLE"}},
    )


def _attestation_digest(paths: tuple[Path, ...]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        resolved = path.resolve(strict=True)
        metadata = resolved.stat()
        if metadata.st_uid != 0 or metadata.st_mode & 0o022:
            raise RuntimeError(f"Runtime dependency is not root-owned: {resolved}")
        digest.update(resolved.as_posix().encode("utf-8"))
        with resolved.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def run() -> None:
    settings = RuntimeWorkerSettings()
    runner_path = Path("/usr/local/bin/fairy-runtime-supervisor")
    attestation = _attestation_digest(
        (
            runner_path,
            Path("/usr/bin/bwrap"),
            Path("/usr/bin/chromium"),
            Path("/usr/local/bin/node"),
            Path("/usr/local/bin/pnpm"),
            Path("/usr/local/bin/yarn"),
            Path("/usr/local/bin/uv"),
        )
    )
    engine = create_async_engine(settings.postgres_dsn, pool_pre_ping=True)
    store = AsyncCloudRuntimeRepository(
        engine,
        attestation_digest=attestation,
        gateway_base_url=settings.runtime_gateway_base_url,
    )
    supervisor = OciDynamicRuntimeSupervisor()
    reviewer = HttpRuntimeReviewer(
        browser_executable=Path("/usr/bin/chromium"),
        browser_scratch_root=Path("/tmp/fairy-runtime-browser"),
        host_environment={"TEMP": "/tmp", "TMP": "/tmp"},
    )
    worker = CloudRuntimeWorker(
        store=store,
        supervisor=supervisor,
        owner_id=settings.runtime_owner_id,
        lease_seconds=settings.runtime_lease_seconds,
    )
    app = create_runtime_worker_app(
        worker=worker,
        resolver=RuntimeTargetResolver(store=store, supervisor=supervisor),
        poll_seconds=settings.runtime_poll_seconds,
        heartbeat_path=settings.runtime_heartbeat_path,
        gateway_key=settings.runtime_gateway_key.get_secret_value(),
        reviewer=reviewer,
        on_close=engine.dispose,
    )
    uvicorn.run(
        app,
        host=settings.runtime_host,
        port=settings.runtime_port,
        proxy_headers=False,
        server_header=False,
    )


__all__ = [
    "CloudRuntimeWorker",
    "RuntimeProcessSupervisor",
    "RuntimeTargetResolver",
    "RuntimeWorkerCycle",
    "RuntimeWorkerStore",
    "create_runtime_worker_app",
]


if __name__ == "__main__":
    run()
