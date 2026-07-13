from __future__ import annotations

import json
import struct
import subprocess
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit
from uuid import UUID

from fairy_core.domain.execution import RuntimeKind
from fairy_core.runtime.models import (
    DynamicRuntimeStart,
    ExecutorRuntimeState,
    RuntimeExecutorError,
    RuntimeProbeResult,
    RuntimeStartResult,
    RuntimeStopResult,
)

_RUNNER = "/usr/local/bin/fairy-runtime-supervisor"
_EXECUTOR = "cloud_oci_runtime"
_VERSION = "1.0.0"
_MAX_RESPONSE_BYTES = 1_000_000
_ENVIRONMENT = {
    "FAIRY_RUNTIME_MODE": "cloud",
    "HOME": "/tmp",
    "LANG": "C.UTF-8",
    "PATH": "/usr/local/bin:/usr/bin:/bin",
}


@dataclass(frozen=True, slots=True)
class ProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class ProcessRunner(Protocol):
    def run(
        self,
        argv: tuple[str, ...],
        *,
        input_bytes: bytes,
        timeout_seconds: float,
        environment: dict[str, str],
        shell: bool,
    ) -> ProcessResult: ...


class SafeProcessRunner:
    def run(
        self,
        argv: tuple[str, ...],
        *,
        input_bytes: bytes,
        timeout_seconds: float,
        environment: dict[str, str],
        shell: bool,
    ) -> ProcessResult:
        completed = subprocess.run(
            argv,
            input=input_bytes,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
            env=environment,
            shell=shell,
        )
        return ProcessResult(completed.returncode, completed.stdout, completed.stderr)


class OciDynamicRuntimeSupervisor:
    def __init__(
        self,
        *,
        runner: ProcessRunner | None = None,
        runner_path: str = _RUNNER,
    ) -> None:
        if runner_path != _RUNNER:
            raise ValueError("OCI Runtime supervisor path must be the attested fixed path")
        self._runner = runner or SafeProcessRunner()
        self._runner_path = runner_path

    def start_dynamic(self, request: DynamicRuntimeStart) -> RuntimeStartResult:
        if request.execution_target != "cloud" or request.kind is not RuntimeKind.CLOUD_OCI:
            raise RuntimeExecutorError(
                "OCI Runtime request does not match cloud Scope",
                error_code="SCOPE_MISMATCH",
            )
        values = self._call(
            "start",
            _encode_start(request),
            timeout_seconds=request.startup_timeout_seconds + 20,
        )
        _validate_identity(values, "start")
        expected = {
            "project_id": str(request.project_id),
            "conversation_id": str(request.conversation_id),
            "task_id": str(request.task_id),
            "version_id": str(request.version_id),
            "runtime_id": str(request.runtime_id),
            "preview_id": str(request.preview_id),
            "scope_digest": request.scope_digest,
            "workspace_generation": request.workspace_generation,
            "lease_fence": request.lease_fence,
            "dependency_key": request.dependency_key,
        }
        if any(values.get(key) != value for key, value in expected.items()):
            raise RuntimeExecutorError(
                "OCI Runtime response does not match Core Scope",
                error_code="SCOPE_MISMATCH",
            )
        try:
            result = RuntimeStartResult(
                executor_handle=_string(values, "executor_handle"),
                host=_string(values, "host"),
                port=_integer(values, "port"),
                url=_string(values, "url"),
                state=ExecutorRuntimeState(_string(values, "state")),
                execution_target="local",
            )
            _validate_handle(result.executor_handle, request.runtime_id, request.lease_fence)
            if urlsplit(result.url).path != "/":
                raise ValueError("internal Runtime URL must use root")
            return result
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeExecutorError(
                "OCI Runtime supervisor returned invalid start metadata",
                error_code="SCOPE_MISMATCH",
            ) from error

    def probe(self, executor_handle: str) -> RuntimeProbeResult:
        runtime_id, fence = _parse_handle(executor_handle)
        values = self._call("probe", _encode_handle(executor_handle), timeout_seconds=10)
        _validate_identity(values, "probe")
        try:
            _validate_handle(_string(values, "executor_handle"), runtime_id, fence)
            if UUID(_string(values, "runtime_id")) != runtime_id:
                raise ValueError("Runtime identity changed")
            state = ExecutorRuntimeState(_string(values, "state"))
            endpoint = _endpoint(values)
            return RuntimeProbeResult(
                executor_handle=executor_handle,
                state=state,
                host=endpoint[0],
                port=endpoint[1],
                url=endpoint[2],
            )
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeExecutorError(
                "OCI Runtime supervisor returned invalid probe metadata",
                error_code="SCOPE_MISMATCH",
            ) from error

    def stop(self, executor_handle: str) -> RuntimeStopResult:
        runtime_id, fence = _parse_handle(executor_handle)
        values = self._call("stop", _encode_handle(executor_handle), timeout_seconds=15)
        _validate_identity(values, "stop")
        try:
            _validate_handle(_string(values, "executor_handle"), runtime_id, fence)
            if UUID(_string(values, "runtime_id")) != runtime_id:
                raise ValueError("Runtime identity changed")
            return RuntimeStopResult(stopped=values["stopped"] is True)
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeExecutorError(
                "OCI Runtime supervisor returned invalid stop metadata",
                error_code="SCOPE_MISMATCH",
            ) from error

    def _call(
        self,
        action: str,
        content: bytes,
        *,
        timeout_seconds: float,
    ) -> dict[str, object]:
        try:
            result = self._runner.run(
                (self._runner_path, action),
                input_bytes=content,
                timeout_seconds=timeout_seconds,
                environment=dict(_ENVIRONMENT),
                shell=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise RuntimeExecutorError(
                "OCI Runtime supervisor could not be reached",
                error_code="SANDBOX_UNAVAILABLE",
            ) from error
        if result.returncode != 0 or len(result.stdout) > _MAX_RESPONSE_BYTES:
            raise RuntimeExecutorError(
                "OCI Runtime supervisor failed",
                error_code="WORKER_INTERRUPTED",
            )
        try:
            value = json.loads(result.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeExecutorError(
                "OCI Runtime supervisor response is invalid",
                error_code="WORKER_INTERRUPTED",
            ) from error
        if not isinstance(value, dict):
            raise RuntimeExecutorError(
                "OCI Runtime supervisor response must be an object",
                error_code="WORKER_INTERRUPTED",
            )
        return value


def _encode_start(request: DynamicRuntimeStart) -> bytes:
    header = {
        "schema_version": 2,
        "project_id": str(request.project_id),
        "conversation_id": str(request.conversation_id),
        "task_id": str(request.task_id),
        "version_id": str(request.version_id),
        "runtime_id": str(request.runtime_id),
        "preview_id": str(request.preview_id),
        "execution_target": request.execution_target,
        "kind": request.kind.value,
        "adapter": request.adapter,
        "scope_digest": request.scope_digest,
        "workspace_generation": request.workspace_generation,
        "lease_fence": request.lease_fence,
        "argv": list(request.argv),
        "cwd": request.cwd,
        "readiness_path": request.readiness_path,
        "startup_timeout_seconds": request.startup_timeout_seconds,
        "dependency_key": request.dependency_key,
        "archive_byte_length": len(request.workspace_archive),
        "archive_sha256": request.archive_sha256,
        "services": [
            {
                "service_id": service.service_id,
                "adapter": service.adapter,
                "argv": list(service.argv),
                "cwd": service.cwd,
                "readiness_path": service.readiness_path,
                "startup_timeout_seconds": service.startup_timeout_seconds,
                "depends_on": list(service.depends_on),
            }
            for service in request.services
        ],
        "public_service_id": request.public_service_id,
    }
    encoded = _json_bytes(header)
    return struct.pack(">I", len(encoded)) + encoded + request.workspace_archive


def _encode_handle(executor_handle: str) -> bytes:
    _parse_handle(executor_handle)
    return _json_bytes({"schema_version": 1, "executor_handle": executor_handle})


def _validate_identity(values: dict[str, object], action: str) -> None:
    if (
        values.get("schema_version") != 1
        or values.get("executor") != _EXECUTOR
        or values.get("executor_version") != _VERSION
        or values.get("action") != action
    ):
        raise RuntimeExecutorError(
            "OCI Runtime supervisor identity does not match attestation",
            error_code="SCOPE_MISMATCH",
        )


def _parse_handle(value: str) -> tuple[UUID, int]:
    parts = value.split(":") if isinstance(value, str) else []
    if len(parts) != 3 or parts[0] != "cloud-dynamic" or not parts[2].isdigit():
        raise RuntimeExecutorError(
            "OCI Runtime executor handle is invalid",
            error_code="SCOPE_MISMATCH",
        )
    try:
        runtime_id = UUID(parts[1])
        fence = int(parts[2])
    except ValueError as error:
        raise RuntimeExecutorError(
            "OCI Runtime executor handle is invalid",
            error_code="SCOPE_MISMATCH",
        ) from error
    if str(runtime_id) != parts[1] or fence < 1:
        raise RuntimeExecutorError(
            "OCI Runtime executor handle is invalid",
            error_code="SCOPE_MISMATCH",
        )
    return runtime_id, fence


def _validate_handle(value: str, runtime_id: UUID, fence: int) -> None:
    if _parse_handle(value) != (runtime_id, fence):
        raise ValueError("OCI Runtime handle binding changed")


def _endpoint(
    values: dict[str, object],
) -> tuple[str | None, int | None, str | None]:
    raw = (values.get("host"), values.get("port"), values.get("url"))
    if raw == (None, None, None):
        return None, None, None
    return _string(values, "host"), _integer(values, "port"), _string(values, "url")


def _string(values: dict[str, object], key: str) -> str:
    value = values[key]
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be non-empty text")
    return value


def _integer(values: dict[str, object], key: str) -> int:
    value = values[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{key} must be a positive integer")
    return value


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


__all__ = ["OciDynamicRuntimeSupervisor", "ProcessResult", "SafeProcessRunner"]
