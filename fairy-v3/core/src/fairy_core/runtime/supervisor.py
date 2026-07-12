from __future__ import annotations

import json
import os
import re
import struct
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit
from uuid import UUID

from fairy_core.domain.execution import RuntimeKind
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
from fairy_core.runtime.ports import RuntimeExecutor
from fairy_core.runtime.unavailable import UnavailableRuntimeExecutor

_DISTRO = "FairySandbox"
_UV_VERSION = re.compile(r"^uv 0\.11\.28(?: \([A-Za-z0-9_-]+\))?$")
_USER = "fairy"
_RUNNER = "/usr/local/bin/fairy-runtime-supervisor"
_EXECUTOR = "wsl_fairy_runtime"
_RUNNER_VERSION = "1.0.0"
_MAX_HEADER_BYTES = 1_000_000
_MAX_RESPONSE_BYTES = 1_000_000
_SAFE_ENVIRONMENT_KEYS = ("SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP")
_HANDLE = re.compile(
    r"^wsl-dynamic:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}):([1-9][0-9]*)$"
)


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
        creation_flags: int,
    ) -> ProcessResult: ...


class SafeSubprocessRunner:
    def run(
        self,
        argv: tuple[str, ...],
        *,
        input_bytes: bytes,
        timeout_seconds: float,
        environment: dict[str, str],
        shell: bool,
        creation_flags: int,
    ) -> ProcessResult:
        completed = subprocess.run(
            argv,
            input=input_bytes,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
            env=environment,
            shell=shell,
            creationflags=creation_flags,
        )
        return ProcessResult(completed.returncode, completed.stdout, completed.stderr)


class RuntimeHealthProbe(Protocol):
    def health(self) -> RuntimeExecutorHealth: ...


class WslRuntimeHealthProbe:
    def __init__(
        self,
        *,
        runner: ProcessRunner | None = None,
        wsl_executable: Path | None = None,
        host_environment: Mapping[str, str] | None = None,
    ) -> None:
        environment = dict(os.environ if host_environment is None else host_environment)
        system_root = environment.get("SYSTEMROOT", r"C:\Windows")
        self._wsl_executable = Path(wsl_executable or Path(system_root) / "System32" / "wsl.exe")
        self._runner = runner or SafeSubprocessRunner()
        self._environment = {
            key: environment[key] for key in _SAFE_ENVIRONMENT_KEYS if key in environment
        }
        self._creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    def health(self) -> RuntimeExecutorHealth:
        if not self._wsl_executable.is_file():
            return _unavailable_health("wsl.exe not found")
        try:
            result = self._runner.run(
                _wsl_argv(self._wsl_executable, "health"),
                input_bytes=b"",
                timeout_seconds=5,
                environment=dict(self._environment),
                shell=False,
                creation_flags=self._creation_flags,
            )
            if result.returncode != 0:
                return _unavailable_health("Runtime supervisor health command failed")
            values = _json_object(result.stdout)
            toolchain = values.get("toolchain")
            if (
                values.get("schema_version") != 1
                or values.get("executor") != _EXECUTOR
                or values.get("runner_version") != _RUNNER_VERSION
                or values.get("user") != _USER
                or not _positive_integer(values.get("uid"))
                or values.get("sandbox_root") != "/var/lib/fairy-sandbox"
                or values.get("config")
                != {
                    "automount.enabled": False,
                    "automount.mountFsTab": False,
                    "interop.enabled": False,
                    "interop.appendWindowsPath": False,
                }
                or not isinstance(toolchain, dict)
                or toolchain.get("node") != "v24.18.0"
                or toolchain.get("pnpm") != "10.34.4"
                or toolchain.get("yarn") != "1.22.22"
                or not isinstance(toolchain.get("uv"), str)
                or _UV_VERSION.fullmatch(toolchain["uv"]) is None
            ):
                return _unavailable_health("Runtime supervisor attestation is invalid")
        except (OSError, subprocess.SubprocessError, ValueError):
            return _unavailable_health("Runtime supervisor attestation could not be verified")
        return RuntimeExecutorHealth(
            available=True,
            executor=_EXECUTOR,
            version=_RUNNER_VERSION,
            error_code=None,
            diagnostics=("FairySandbox dynamic Runtime supervisor attested",),
        )


class WslDynamicRuntimeExecutor:
    def __init__(
        self,
        *,
        runner: ProcessRunner | None = None,
        health_probe: RuntimeHealthProbe | None = None,
        wsl_executable: Path | None = None,
        host_environment: Mapping[str, str] | None = None,
    ) -> None:
        environment = dict(os.environ if host_environment is None else host_environment)
        system_root = environment.get("SYSTEMROOT", r"C:\Windows")
        self._wsl_executable = Path(wsl_executable or Path(system_root) / "System32" / "wsl.exe")
        self._runner = runner or SafeSubprocessRunner()
        self._health_probe = health_probe or WslRuntimeHealthProbe(
            runner=self._runner,
            wsl_executable=self._wsl_executable,
            host_environment=environment,
        )
        self._environment = {
            key: environment[key] for key in _SAFE_ENVIRONMENT_KEYS if key in environment
        }
        self._creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    def health(self) -> RuntimeExecutorHealth:
        return self._health_probe.health()

    def start_static(self, _request: StaticRuntimeStart) -> RuntimeStartResult:
        raise RuntimeExecutorError(
            "WSL Runtime supervisor does not serve static Previews",
            error_code="CAPABILITY_NOT_AVAILABLE",
        )

    def start_dynamic(self, request: DynamicRuntimeStart) -> RuntimeStartResult:
        if request.execution_target != "local" or request.kind is not RuntimeKind.WSL_PROJECT:
            raise RuntimeExecutorError(
                "WSL Runtime request does not match local Scope",
                error_code="SCOPE_MISMATCH",
            )
        self._require_health()
        values = self._call(
            "start",
            _encode_start_frame(request),
            timeout_seconds=request.startup_timeout_seconds + 20,
        )
        self._validate_start_binding(values, request)
        try:
            result = RuntimeStartResult(
                executor_handle=_required_string(values, "executor_handle"),
                host=_required_string(values, "host"),
                port=_required_integer(values, "port"),
                url=_required_string(values, "url"),
                state=ExecutorRuntimeState(_required_string(values, "state")),
                execution_target="local",
            )
            _validate_handle(
                result.executor_handle,
                runtime_id=request.runtime_id,
                lease_fence=request.lease_fence,
            )
            _validate_dynamic_url_path(result.url)
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeExecutorError(
                "Runtime supervisor returned invalid start metadata",
                error_code="SCOPE_MISMATCH",
            ) from error
        return result

    def probe(self, executor_handle: str) -> RuntimeProbeResult:
        runtime_id, lease_fence = _parse_handle(executor_handle)
        self._require_health()
        values = self._call(
            "probe",
            _encode_handle_request(executor_handle),
            timeout_seconds=10,
        )
        _validate_common_response(values, action="probe")
        try:
            response_handle = _required_string(values, "executor_handle")
            _validate_handle(
                response_handle,
                runtime_id=runtime_id,
                lease_fence=lease_fence,
            )
            if _required_uuid(values, "runtime_id") != runtime_id:
                raise ValueError("Runtime identity changed")
            if _required_integer(values, "lease_fence") != lease_fence:
                raise ValueError("Runtime fence changed")
            state = ExecutorRuntimeState(_required_string(values, "state"))
            endpoint = _optional_endpoint(values)
            result = RuntimeProbeResult(
                executor_handle=response_handle,
                state=state,
                host=endpoint[0],
                port=endpoint[1],
                url=endpoint[2],
                execution_target="local",
            )
            if result.url is not None:
                _validate_dynamic_url_path(result.url)
            return result
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeExecutorError(
                "Runtime supervisor returned invalid probe metadata",
                error_code="SCOPE_MISMATCH",
            ) from error

    def recovery_handle(self, target: RuntimeRecoveryTarget) -> str:
        if target.kind is not RuntimeKind.WSL_PROJECT or target.execution_target != "local":
            raise RuntimeExecutorError(
                "WSL Runtime recovery target is invalid",
                error_code="SCOPE_MISMATCH",
            )
        return f"wsl-dynamic:{target.runtime_id}:{target.lease_fence}"

    def stop(self, executor_handle: str) -> RuntimeStopResult:
        runtime_id, lease_fence = _parse_handle(executor_handle)
        self._require_health()
        values = self._call(
            "stop",
            _encode_handle_request(executor_handle),
            timeout_seconds=15,
        )
        _validate_common_response(values, action="stop")
        try:
            response_handle = _required_string(values, "executor_handle")
            _validate_handle(
                response_handle,
                runtime_id=runtime_id,
                lease_fence=lease_fence,
            )
            if _required_uuid(values, "runtime_id") != runtime_id:
                raise ValueError("Runtime identity changed")
            if _required_integer(values, "lease_fence") != lease_fence:
                raise ValueError("Runtime fence changed")
            return RuntimeStopResult(stopped=values["stopped"] is True)
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeExecutorError(
                "Runtime supervisor returned invalid stop metadata",
                error_code="SCOPE_MISMATCH",
            ) from error

    def _require_health(self) -> None:
        health = self.health()
        if (
            not health.available
            or health.executor != _EXECUTOR
            or health.version != _RUNNER_VERSION
        ):
            raise RuntimeExecutorError(
                "Dynamic WSL Runtime supervisor is unavailable",
                error_code=health.error_code or "SANDBOX_UNAVAILABLE",
            )
        if not self._wsl_executable.is_file():
            raise RuntimeExecutorError(
                "wsl.exe is unavailable",
                error_code="SANDBOX_UNAVAILABLE",
            )

    def _call(
        self,
        action: str,
        input_bytes: bytes,
        *,
        timeout_seconds: float,
    ) -> dict[str, object]:
        try:
            result = self._runner.run(
                _wsl_argv(self._wsl_executable, action),
                input_bytes=input_bytes,
                timeout_seconds=timeout_seconds,
                environment=dict(self._environment),
                shell=False,
                creation_flags=self._creation_flags,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise RuntimeExecutorError(
                "Dynamic WSL Runtime supervisor could not be reached",
                error_code="SANDBOX_UNAVAILABLE",
            ) from error
        if result.returncode != 0:
            raise RuntimeExecutorError(
                "Dynamic WSL Runtime supervisor failed",
                error_code="WORKER_INTERRUPTED",
            )
        if len(result.stdout) > _MAX_RESPONSE_BYTES:
            raise RuntimeExecutorError(
                "Dynamic WSL Runtime response exceeds the protocol limit",
                error_code="WORKER_INTERRUPTED",
            )
        try:
            return _json_object(result.stdout)
        except ValueError as error:
            raise RuntimeExecutorError(
                "Dynamic WSL Runtime response is invalid",
                error_code="WORKER_INTERRUPTED",
            ) from error

    @staticmethod
    def _validate_start_binding(
        values: dict[str, object],
        request: DynamicRuntimeStart,
    ) -> None:
        _validate_common_response(values, action="start")
        expected: dict[str, object] = {
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
                "Runtime supervisor response does not match Core Scope",
                error_code="SCOPE_MISMATCH",
            )


class RoutedRuntimeExecutor:
    def __init__(
        self,
        *,
        static: RuntimeExecutor,
        local_dynamic: RuntimeExecutor,
        cloud_dynamic: RuntimeExecutor | None = None,
    ) -> None:
        self._static = static
        self._local_dynamic = local_dynamic
        self._cloud_dynamic = cloud_dynamic or UnavailableRuntimeExecutor(
            executor="cloud_oci_runtime",
            diagnostic="Cloud dynamic Runtime is unavailable in the local Core",
        )

    def health(self) -> RuntimeExecutorHealth:
        return self._static.health()

    def health_for(self, kind: RuntimeKind) -> RuntimeExecutorHealth:
        if kind is RuntimeKind.STATIC_SITE:
            return self._static.health()
        if kind is RuntimeKind.WSL_PROJECT:
            return self._local_dynamic.health()
        return self._cloud_dynamic.health()

    def start_static(self, request: StaticRuntimeStart) -> RuntimeStartResult:
        return self._static.start_static(request)

    def start_dynamic(self, request: DynamicRuntimeStart) -> RuntimeStartResult:
        if request.execution_target == "local":
            return self._local_dynamic.start_dynamic(request)
        if request.execution_target == "cloud":
            return self._cloud_dynamic.start_dynamic(request)
        raise RuntimeExecutorError(
            "Dynamic Runtime execution target is invalid",
            error_code="SCOPE_MISMATCH",
        )

    def recovery_handle(self, target: RuntimeRecoveryTarget) -> str:
        if target.kind is RuntimeKind.STATIC_SITE:
            return self._static.recovery_handle(target)
        if target.kind is RuntimeKind.WSL_PROJECT:
            return self._local_dynamic.recovery_handle(target)
        return self._cloud_dynamic.recovery_handle(target)

    def probe(self, executor_handle: str) -> RuntimeProbeResult:
        return self._delegate_for_handle(executor_handle).probe(executor_handle)

    def stop(self, executor_handle: str) -> RuntimeStopResult:
        return self._delegate_for_handle(executor_handle).stop(executor_handle)

    def _delegate_for_handle(self, executor_handle: str) -> RuntimeExecutor:
        if executor_handle.startswith("static:"):
            return self._static
        if executor_handle.startswith("wsl-dynamic:"):
            return self._local_dynamic
        if executor_handle.startswith("cloud-dynamic:"):
            return self._cloud_dynamic
        raise RuntimeExecutorError(
            "Runtime executor handle has no configured route",
            error_code="SCOPE_MISMATCH",
        )


def _encode_start_frame(request: DynamicRuntimeStart) -> bytes:
    header = {
        "schema_version": 1,
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
    }
    encoded = _canonical_json(header)
    if len(encoded) > _MAX_HEADER_BYTES:
        raise RuntimeExecutorError(
            "Dynamic Runtime request header exceeds the protocol limit",
            error_code="SCOPE_MISMATCH",
        )
    return struct.pack(">I", len(encoded)) + encoded + request.workspace_archive


def _encode_handle_request(executor_handle: str) -> bytes:
    return _canonical_json({"schema_version": 1, "executor_handle": executor_handle})


def _wsl_argv(executable: Path, action: str) -> tuple[str, ...]:
    if action not in {"health", "start", "probe", "stop"}:
        raise ValueError("Runtime supervisor action is invalid")
    return (
        str(executable),
        "--distribution",
        _DISTRO,
        "--user",
        _USER,
        "--exec",
        _RUNNER,
        action,
    )


def _validate_common_response(values: dict[str, object], *, action: str) -> None:
    if (
        values.get("schema_version") != 1
        or values.get("executor") != _EXECUTOR
        or values.get("executor_version") != _RUNNER_VERSION
        or values.get("action") != action
    ):
        raise RuntimeExecutorError(
            "Runtime supervisor response identity does not match",
            error_code="SCOPE_MISMATCH",
        )


def _parse_handle(executor_handle: str) -> tuple[UUID, int]:
    if not isinstance(executor_handle, str):
        raise RuntimeExecutorError(
            "Runtime executor handle is invalid",
            error_code="SCOPE_MISMATCH",
        )
    match = _HANDLE.fullmatch(executor_handle)
    if match is None:
        raise RuntimeExecutorError(
            "Runtime executor handle is invalid",
            error_code="SCOPE_MISMATCH",
        )
    return UUID(match.group(1)), int(match.group(2))


def _validate_handle(executor_handle: str, *, runtime_id: UUID, lease_fence: int) -> None:
    if _parse_handle(executor_handle) != (runtime_id, lease_fence):
        raise ValueError("Runtime executor handle binding changed")


def _validate_dynamic_url_path(url: str) -> None:
    if urlsplit(url).path != "/":
        raise ValueError("Runtime supervisor URL must use the server root")


def _optional_endpoint(
    values: dict[str, object],
) -> tuple[str | None, int | None, str | None]:
    endpoint = (values.get("host"), values.get("port"), values.get("url"))
    if endpoint == (None, None, None):
        return None, None, None
    return (
        _required_string(values, "host"),
        _required_integer(values, "port"),
        _required_string(values, "url"),
    )


def _required_string(values: dict[str, object], key: str) -> str:
    value = values[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _required_integer(values: dict[str, object], key: str) -> int:
    value = values[key]
    if not _positive_integer(value):
        raise ValueError(f"{key} must be a positive integer")
    return value


def _required_uuid(values: dict[str, object], key: str) -> UUID:
    value = _required_string(values, key)
    parsed = UUID(value)
    if str(parsed) != value:
        raise ValueError(f"{key} must be a canonical UUID")
    return parsed


def _positive_integer(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value > 0


def _json_object(value: bytes) -> dict[str, object]:
    try:
        decoded = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Runtime supervisor returned invalid JSON") from error
    if not isinstance(decoded, dict):
        raise ValueError("Runtime supervisor result must be an object")
    return decoded


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _unavailable_health(diagnostic: str) -> RuntimeExecutorHealth:
    return RuntimeExecutorHealth(
        available=False,
        executor=_EXECUTOR,
        version=None,
        error_code="SANDBOX_UNAVAILABLE",
        diagnostics=(diagnostic,),
    )


__all__ = [
    "ProcessResult",
    "RoutedRuntimeExecutor",
    "SafeSubprocessRunner",
    "WslDynamicRuntimeExecutor",
    "WslRuntimeHealthProbe",
]
