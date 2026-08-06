from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID

from fairy_core.runtime.models import RuntimeExecutorHealth
from fairy_core.runtime.wsl_health import WslSandboxHealthProbe
from fairy_core.sandbox.models import (
    SandboxRequest,
    SandboxResult,
    SandboxResultStatus,
    encode_request_frame,
)

_DISTRO = "FairySandbox"
_USER = "fairy"
_RUNNER = "/usr/local/bin/fairy-sandbox-runner"
_EXECUTOR = "wsl_fairy_sandbox"
_RUNNER_VERSION = "1.1.0"
_SAFE_ENVIRONMENT_KEYS = ("SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP")


class SandboxUnavailableError(RuntimeError):
    error_code = "SANDBOX_UNAVAILABLE"


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


class HealthProbe(Protocol):
    def health(self) -> RuntimeExecutorHealth: ...


class WslSandboxExecutor:
    def __init__(
        self,
        *,
        runner: ProcessRunner | None = None,
        health_probe: HealthProbe | None = None,
        wsl_executable: Path | None = None,
        host_environment: Mapping[str, str] | None = None,
    ) -> None:
        environment = dict(os.environ if host_environment is None else host_environment)
        system_root = environment.get("SYSTEMROOT", r"C:\Windows")
        self._wsl_executable = Path(wsl_executable or Path(system_root) / "System32" / "wsl.exe")
        self._runner = runner or SafeSubprocessRunner()
        self._health_probe = health_probe or WslSandboxHealthProbe(
            wsl_executable=self._wsl_executable,
            host_environment=environment,
        )
        self._environment = {
            key: environment[key] for key in _SAFE_ENVIRONMENT_KEYS if key in environment
        }
        self._creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    def health(self) -> RuntimeExecutorHealth:
        return self._health_probe.health()

    def execute(self, request: SandboxRequest) -> SandboxResult:
        health = self.health()
        if (
            not health.available
            or health.executor != _EXECUTOR
            or health.version != _RUNNER_VERSION
        ):
            raise SandboxUnavailableError("SANDBOX_UNAVAILABLE: attestation failed")
        if not self._wsl_executable.is_file():
            raise SandboxUnavailableError("SANDBOX_UNAVAILABLE: wsl.exe not found")
        try:
            result = self._runner.run(
                self._argv(),
                input_bytes=encode_request_frame(request),
                timeout_seconds=request.timeout_seconds + 15,
                environment=dict(self._environment),
                shell=False,
                creation_flags=self._creation_flags,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise SandboxUnavailableError(
                "SANDBOX_UNAVAILABLE: sandbox runner could not be reached"
            ) from error
        if result.returncode != 0:
            raise SandboxUnavailableError("SANDBOX_UNAVAILABLE: sandbox runner failed")
        values = _json_object(result.stdout)
        self._validate_response_binding(values, request)
        try:
            status = SandboxResultStatus(str(values["status"]))
            exit_code_value = values.get("exit_code")
            if exit_code_value is not None and (
                isinstance(exit_code_value, bool) or not isinstance(exit_code_value, int)
            ):
                raise ValueError("exit_code must be an integer or null")
            exit_code = exit_code_value
            output_truncated = values.get("output_truncated")
            if not isinstance(output_truncated, bool):
                raise ValueError("output_truncated must be a boolean")
            started_at = datetime.fromisoformat(str(values["started_at"]))
            finished_at = datetime.fromisoformat(str(values["finished_at"]))
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Sandbox runner result schema is invalid") from error
        stdout = _response_bytes(values, "stdout")
        stderr = _response_bytes(values, "stderr")
        return SandboxResult.create(
            request=request,
            executor=str(values["executor"]),
            executor_version=str(values["executor_version"]),
            status=status,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            output_truncated=output_truncated,
            started_at=started_at,
            finished_at=finished_at,
        )

    def cancel(self, job_id: UUID) -> None:
        if not self._wsl_executable.is_file():
            raise SandboxUnavailableError("SANDBOX_UNAVAILABLE: wsl.exe not found")
        result = self._runner.run(
            (*self._argv(), "--cancel", str(job_id)),
            input_bytes=b"",
            timeout_seconds=10,
            environment=dict(self._environment),
            shell=False,
            creation_flags=self._creation_flags,
        )
        if result.returncode != 0:
            raise SandboxUnavailableError("SANDBOX_UNAVAILABLE: cancellation failed")

    def _argv(self) -> tuple[str, ...]:
        return (
            str(self._wsl_executable),
            "--distribution",
            _DISTRO,
            "--user",
            _USER,
            "--exec",
            _RUNNER,
        )

    @staticmethod
    def _validate_response_binding(
        values: dict[str, object],
        request: SandboxRequest,
    ) -> None:
        if values.get("schema_version") != 1:
            raise ValueError("Sandbox runner schema is invalid")
        if values.get("executor") != _EXECUTOR or values.get("executor_version") != _RUNNER_VERSION:
            raise ValueError("Sandbox executor identity does not match attestation")
        if values.get("job_id") != str(request.job_id):
            raise ValueError("Sandbox job id does not match")
        if values.get("scope_digest") != request.scope_digest:
            raise ValueError("Sandbox Scope does not match")
        if values.get("workspace_generation") != request.workspace_generation:
            raise ValueError("Sandbox workspace generation does not match")
        if values.get("lease_fence") != request.lease_fence:
            raise ValueError("Sandbox lease fence does not match")


def _json_object(value: bytes) -> dict[str, object]:
    try:
        decoded = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Sandbox runner returned invalid JSON") from error
    if not isinstance(decoded, dict):
        raise ValueError("Sandbox runner result must be an object")
    return decoded


def _response_bytes(values: dict[str, object], key: str) -> bytes:
    encoded = values.get(f"{key}_base64")
    if encoded is not None:
        if not isinstance(encoded, str):
            raise ValueError(f"Sandbox runner {key} base64 must be a string")
        try:
            value = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError(f"Sandbox runner {key} base64 is invalid") from error
    else:
        text = values.get(key)
        if not isinstance(text, str):
            raise ValueError(f"Sandbox runner {key} must be a string")
        value = text.encode("utf-8")
    expected_hash = values.get(f"{key}_sha256")
    actual_hash = hashlib.sha256(value).hexdigest()
    if not isinstance(expected_hash, str) or not hmac.compare_digest(
        expected_hash,
        actual_hash,
    ):
        raise ValueError(f"Sandbox runner {key} hash does not match")
    return value


__all__ = [
    "ProcessResult",
    "SafeSubprocessRunner",
    "SandboxUnavailableError",
    "WslSandboxExecutor",
]
