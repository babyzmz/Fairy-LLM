from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import signal
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from fairy_core.domain.errors import WorkerFenceError
from fairy_core.sandbox.models import (
    SandboxNetworkPolicy,
    SandboxPurpose,
    SandboxRequest,
    SandboxResult,
    SandboxResultStatus,
    encode_request_frame,
)
from sqlalchemy.ext.asyncio import create_async_engine

from fairy_cloud.execution.models import ExecutionClaim, ExecutionClaimAction
from fairy_cloud.execution.repository import (
    AsyncExecutionJobRepository,
    ExecutionCancelledBeforeSpawn,
)

logger = logging.getLogger(__name__)
_RUNNER = "/usr/local/bin/fairy-sandbox-runner"
_EXECUTOR = "cloud_oci_worker"
_EXECUTOR_VERSION = "1.0.0"
_PROCESS_ENVIRONMENT = {
    "FAIRY_SANDBOX_EXECUTOR": _EXECUTOR,
    "HOME": "/tmp",
    "LANG": "C.UTF-8",
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
}


class ExecutionWorkerStore(Protocol):
    async def claim_next(
        self,
        *,
        owner_id: str,
        lease_seconds: int,
    ) -> ExecutionClaim | None: ...

    async def mark_spawned(self, claim: ExecutionClaim) -> None: ...

    async def cancellation_requested(self, claim: ExecutionClaim) -> bool: ...

    async def mark_cancelled(self, claim: ExecutionClaim) -> None: ...

    async def record_result(self, claim: ExecutionClaim, result: SandboxResult) -> None: ...

    async def finalize_result(self, claim: ExecutionClaim) -> None: ...

    async def mark_interrupted(self, claim: ExecutionClaim) -> None: ...

    async def reconcile_expired(self) -> int: ...

    async def heartbeat(self, *, owner_id: str) -> None: ...


class SandboxProcessRunner(Protocol):
    async def execute(
        self,
        request: SandboxRequest,
        cancellation_requested: Callable[[], Awaitable[bool]],
    ) -> SandboxResult: ...


@dataclass(frozen=True, slots=True)
class WorkerCycle:
    claimed: int = 0
    completed: int = 0
    interrupted: int = 0
    cancelled: int = 0
    failed: int = 0


class ExecutionWorker:
    def __init__(
        self,
        *,
        store: ExecutionWorkerStore,
        runner: SandboxProcessRunner,
        owner_id: str,
        lease_seconds: int = 30,
    ) -> None:
        if not owner_id.strip():
            raise ValueError("execution worker owner is required")
        if lease_seconds < 1:
            raise ValueError("execution worker lease must be positive")
        self._store = store
        self._runner = runner
        self._owner_id = owner_id
        self._lease_seconds = lease_seconds

    async def run_once(self) -> WorkerCycle:
        reconciled = await self._store.reconcile_expired()
        cycle = WorkerCycle(interrupted=reconciled)
        try:
            claim = await self._store.claim_next(
                owner_id=self._owner_id,
                lease_seconds=self._lease_seconds,
            )
            if claim is None:
                return cycle
            if claim.action is ExecutionClaimAction.FINALIZE:
                try:
                    await self._store.finalize_result(claim)
                except Exception:
                    logger.exception("Execution result finalization failed")
                    return _replace_cycle(cycle, claimed=1, failed=1)
                return _replace_cycle(cycle, claimed=1, completed=1)
            return await self._execute_claim(claim, cycle)
        finally:
            await self._store.heartbeat(owner_id=self._owner_id)

    async def _execute_claim(
        self,
        claim: ExecutionClaim,
        cycle: WorkerCycle,
    ) -> WorkerCycle:
        request = claim.job.to_request()
        if (
            request.network_policy is SandboxNetworkPolicy.PUBLIC
            and request.purpose is not SandboxPurpose.DEPENDENCY
        ):
            await self._store.mark_interrupted(claim)
            return _replace_cycle(cycle, claimed=1, interrupted=cycle.interrupted + 1)
        try:
            if await self._store.cancellation_requested(claim):
                await self._store.mark_cancelled(claim)
                return _replace_cycle(cycle, claimed=1, cancelled=1)
            await self._store.mark_spawned(claim)
        except ExecutionCancelledBeforeSpawn:
            await self._store.mark_cancelled(claim)
            return _replace_cycle(cycle, claimed=1, cancelled=1)
        except Exception:
            logger.exception("Execution claim lost before process spawn")
            return _replace_cycle(cycle, claimed=1, failed=1)

        try:
            result = await self._runner.execute(
                request,
                lambda: self._store.cancellation_requested(claim),
            )
        except Exception:
            logger.exception("Execution process stopped without a durable result")
            try:
                await self._store.mark_interrupted(claim)
            except Exception:
                logger.exception("Execution interruption could not be persisted")
            return _replace_cycle(cycle, claimed=1, interrupted=cycle.interrupted + 1)

        try:
            await self._store.record_result(claim, result)
            await self._store.finalize_result(claim)
        except WorkerFenceError:
            logger.exception("Execution result lost its durable fence")
            return _replace_cycle(cycle, claimed=1, failed=1)
        except Exception:
            logger.exception("Execution result was recorded but not finalized")
            return _replace_cycle(cycle, claimed=1, failed=1)

        if result.status is SandboxResultStatus.CANCELLED:
            return _replace_cycle(cycle, claimed=1, cancelled=1)
        return _replace_cycle(cycle, claimed=1, completed=1)

    async def run_forever(
        self,
        *,
        stop: asyncio.Event,
        poll_seconds: float,
        heartbeat_path: Path,
    ) -> None:
        while not stop.is_set():
            await self.run_once()
            heartbeat_path.touch()
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=poll_seconds)


class FramedSandboxProcessRunner:
    """Invokes the fixed, root-owned Sandbox runner without inheriting service secrets."""

    def __init__(
        self,
        *,
        runner_path: str = _RUNNER,
        cancellation_poll_seconds: float = 0.1,
    ) -> None:
        if not runner_path.startswith("/"):
            raise ValueError("Sandbox runner path must be absolute")
        if cancellation_poll_seconds <= 0:
            raise ValueError("cancellation poll must be positive")
        self._runner_path = runner_path
        self._cancellation_poll_seconds = cancellation_poll_seconds

    async def execute(
        self,
        request: SandboxRequest,
        cancellation_requested: Callable[[], Awaitable[bool]],
    ) -> SandboxResult:
        process = await asyncio.create_subprocess_exec(
            self._runner_path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=dict(_PROCESS_ENVIRONMENT),
        )
        communicate = asyncio.create_task(process.communicate(encode_request_frame(request)))
        deadline = asyncio.get_running_loop().time() + request.timeout_seconds + 15
        cancellation_sent = False
        while not communicate.done():
            if not cancellation_sent and await cancellation_requested():
                await self._cancel(request)
                cancellation_sent = True
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                process.kill()
                await communicate
                raise RuntimeError("Sandbox runner exceeded its bounded deadline")
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    asyncio.shield(communicate),
                    timeout=min(self._cancellation_poll_seconds, remaining),
                )
        stdout, stderr = await communicate
        if process.returncode != 0:
            diagnostic = stderr.decode("utf-8", errors="replace")[:512]
            raise RuntimeError(f"Sandbox runner failed: {diagnostic}")
        return decode_sandbox_result(stdout, request)

    async def _cancel(self, request: SandboxRequest) -> None:
        process = await asyncio.create_subprocess_exec(
            self._runner_path,
            "--cancel",
            str(request.job_id),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env=dict(_PROCESS_ENVIRONMENT),
        )
        if await process.wait() != 0:
            raise RuntimeError("Sandbox runner cancellation failed")


def _replace_cycle(cycle: WorkerCycle, **changes: int) -> WorkerCycle:
    values = {
        "claimed": cycle.claimed,
        "completed": cycle.completed,
        "interrupted": cycle.interrupted,
        "cancelled": cycle.cancelled,
        "failed": cycle.failed,
    }
    values.update(changes)
    return WorkerCycle(**values)


def decode_sandbox_result(value: bytes, request: SandboxRequest) -> SandboxResult:
    try:
        values = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Sandbox runner returned invalid JSON") from error
    if not isinstance(values, dict):
        raise ValueError("Sandbox runner result must be an object")
    expected = {
        "schema_version": 1,
        "executor": _EXECUTOR,
        "executor_version": _EXECUTOR_VERSION,
        "job_id": str(request.job_id),
        "scope_digest": request.scope_digest,
        "workspace_generation": request.workspace_generation,
        "lease_fence": request.lease_fence,
    }
    if any(values.get(key) != expected_value for key, expected_value in expected.items()):
        raise WorkerFenceError("Sandbox result binding or executor attestation did not match")
    try:
        status = SandboxResultStatus(str(values["status"]))
        exit_code = values.get("exit_code")
        if exit_code is not None and (
            isinstance(exit_code, bool) or not isinstance(exit_code, int)
        ):
            raise ValueError("exit_code must be an integer or null")
        output_truncated = values["output_truncated"]
        if not isinstance(output_truncated, bool):
            raise ValueError("output_truncated must be a boolean")
        started_at = datetime.fromisoformat(str(values["started_at"]))
        finished_at = datetime.fromisoformat(str(values["finished_at"]))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Sandbox runner result schema is invalid") from error
    return SandboxResult.create(
        request=request,
        executor=_EXECUTOR,
        executor_version=_EXECUTOR_VERSION,
        status=status,
        exit_code=exit_code,
        stdout=_response_bytes(values, "stdout"),
        stderr=_response_bytes(values, "stderr"),
        output_truncated=output_truncated,
        started_at=started_at,
        finished_at=finished_at,
    )


def _response_bytes(values: dict[str, object], key: str) -> bytes:
    encoded = values.get(f"{key}_base64")
    if not isinstance(encoded, str):
        raise ValueError(f"Sandbox runner {key} base64 must be a string")
    try:
        value = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError(f"Sandbox runner {key} base64 is invalid") from error
    expected_hash = values.get(f"{key}_sha256")
    actual_hash = hashlib.sha256(value).hexdigest()
    if not isinstance(expected_hash, str) or not hmac.compare_digest(expected_hash, actual_hash):
        raise ValueError(f"Sandbox runner {key} hash does not match")
    return value


def _attestation_digest(paths: tuple[Path, ...]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        resolved = path.resolve(strict=True)
        metadata = resolved.stat()
        if metadata.st_uid != 0 or metadata.st_mode & 0o022:
            raise RuntimeError(f"Execution dependency is not root-owned and immutable: {resolved}")
        digest.update(resolved.as_posix().encode("utf-8"))
        with resolved.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()


async def _run() -> None:
    postgres_dsn = os.environ["FAIRY_POSTGRES_DSN"]
    owner_id = os.environ.get("FAIRY_EXECUTION_OWNER_ID", "fairy-execution-1")
    poll_seconds = float(os.environ.get("FAIRY_EXECUTION_POLL_SECONDS", "0.1"))
    heartbeat_path = Path(
        os.environ.get("FAIRY_EXECUTION_HEARTBEAT_PATH", "/tmp/fairy-execution-ready")
    )
    attestation = _attestation_digest((Path(_RUNNER), Path("/usr/bin/bwrap")))
    engine = create_async_engine(postgres_dsn, pool_pre_ping=True)
    store = AsyncExecutionJobRepository(engine, attestation_digest=attestation)
    worker = ExecutionWorker(
        store=store,
        runner=FramedSandboxProcessRunner(),
        owner_id=owner_id,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for handled_signal in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(handled_signal, stop.set)
    try:
        await worker.run_forever(
            stop=stop,
            poll_seconds=poll_seconds,
            heartbeat_path=heartbeat_path,
        )
    finally:
        await engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run())


if __name__ == "__main__":
    main()


__all__ = [
    "ExecutionWorker",
    "ExecutionWorkerStore",
    "FramedSandboxProcessRunner",
    "SandboxProcessRunner",
    "WorkerCycle",
    "decode_sandbox_result",
    "main",
]
