from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from fairy_core.runtime.models import RuntimeExecutorHealth

_EXECUTOR = "wsl_fairy_sandbox"
_ERROR_CODE = "SANDBOX_UNAVAILABLE"
_DISTRO = "FairySandbox"
_USER = "fairy"
_HEALTH_RUNNER = "/usr/local/bin/fairy-sandbox-health"
_SAFE_ENVIRONMENT_KEYS = ("SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP")
_REQUIRED_CONFIG = {
    "automount.enabled": False,
    "automount.mountFsTab": False,
    "interop.enabled": False,
    "interop.appendWindowsPath": False,
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UV_VERSION = re.compile(r"^uv 0\.11\.28(?: \([A-Za-z0-9_-]+\))?$")


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
        timeout_seconds: float,
        environment: dict[str, str],
        shell: bool,
        creation_flags: int,
    ) -> ProcessResult:
        completed = subprocess.run(
            argv,
            capture_output=True,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=timeout_seconds,
            env=environment,
            shell=shell,
            creationflags=creation_flags,
        )
        return ProcessResult(completed.returncode, completed.stdout, completed.stderr)


class WslSandboxHealthProbe:
    def __init__(
        self,
        *,
        runner: ProcessRunner | None = None,
        wsl_executable: Path | None = None,
        expected_runner_version: str = "1.0.0",
        host_environment: Mapping[str, str] | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        environment = dict(os.environ if host_environment is None else host_environment)
        system_root = environment.get("SYSTEMROOT", r"C:\Windows")
        self._runner = runner or SafeSubprocessRunner()
        self._wsl_executable = Path(wsl_executable or Path(system_root) / "System32" / "wsl.exe")
        self._expected_runner_version = expected_runner_version
        self._environment = {
            key: environment[key] for key in _SAFE_ENVIRONMENT_KEYS if key in environment
        }
        self._timeout_seconds = timeout_seconds
        self._creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    def health(self) -> RuntimeExecutorHealth:
        if not self._wsl_executable.is_file():
            return self._unavailable("wsl.exe not found")
        try:
            status = self._run("--status")
            if status.returncode != 0:
                return self._unavailable("wsl --status failed")
            distributions = self._run("--list", "--verbose")
            if distributions.returncode != 0:
                return self._unavailable("wsl --list --verbose failed")
            wsl_version = _find_distro_version(_decode_output(distributions.stdout))
            if wsl_version is None:
                return self._unavailable("FairySandbox distribution is not installed")
            if wsl_version != 2:
                return self._unavailable("FairySandbox must use WSL 2")
            attestation = self._run(
                "--distribution",
                _DISTRO,
                "--user",
                _USER,
                "--exec",
                _HEALTH_RUNNER,
                "--json",
            )
            if attestation.returncode != 0:
                return self._unavailable("FairySandbox health runner failed")
            values = json.loads(_decode_output(attestation.stdout))
            diagnostic = self._validate_attestation(values)
            if diagnostic is not None:
                return self._unavailable(diagnostic)
        except (
            OSError,
            subprocess.SubprocessError,
            UnicodeError,
            json.JSONDecodeError,
            ValueError,
        ):
            return self._unavailable("FairySandbox attestation could not be verified")
        return RuntimeExecutorHealth(
            available=True,
            executor=_EXECUTOR,
            version=self._expected_runner_version,
            error_code=None,
            diagnostics=("FairySandbox WSL 2 attestation verified",),
        )

    def _run(self, *arguments: str) -> ProcessResult:
        return self._runner.run(
            (str(self._wsl_executable), *arguments),
            timeout_seconds=self._timeout_seconds,
            environment=dict(self._environment),
            shell=False,
            creation_flags=self._creation_flags,
        )

    def _validate_attestation(self, values: object) -> str | None:
        if not isinstance(values, dict) or values.get("schema_version") != 1:
            return "FairySandbox health schema is invalid"
        if values.get("executor") != _EXECUTOR:
            return "FairySandbox executor identity does not match"
        if values.get("runner_version") != self._expected_runner_version:
            return "FairySandbox runner version does not match"
        uid = values.get("uid")
        if (
            values.get("user") != _USER
            or values.get("default_user") != _USER
            or isinstance(uid, bool)
            or not isinstance(uid, int)
            or uid <= 0
        ):
            return "FairySandbox must use the non-root fairy user"
        config = values.get("config")
        if not isinstance(config, dict) or any(
            config.get(key) is not expected for key, expected in _REQUIRED_CONFIG.items()
        ):
            return "FairySandbox wsl.conf isolation keys are incomplete"
        toolchain = values.get("toolchain")
        if (
            not isinstance(toolchain, dict)
            or toolchain.get("node") != "v24.18.0"
            or toolchain.get("pnpm") != "10.34.4"
            or toolchain.get("yarn") != "1.22.22"
            or not isinstance(toolchain.get("uv"), str)
            or _UV_VERSION.fullmatch(toolchain["uv"]) is None
        ):
            return "FairySandbox toolchain attestation does not match"
        if values.get("bwrap_path") != "/usr/bin/bwrap":
            return "FairySandbox bubblewrap identity does not match"
        for key in ("runner_sha256", "config_sha256", "bwrap_sha256"):
            value = values.get(key)
            if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
                return f"FairySandbox {key} is invalid"
        files = values.get("files")
        if not isinstance(files, dict):
            return "FairySandbox root-owned file attestation is missing"
        for name in ("runner", "config", "bwrap"):
            file = files.get(name)
            if not isinstance(file, dict):
                return "FairySandbox root-owned file attestation is incomplete"
            uid = file.get("uid")
            mode = file.get("mode")
            if uid != 0 or isinstance(mode, bool) or not isinstance(mode, int):
                return "FairySandbox files must be root-owned"
            if mode < 0 or mode > 0o7777 or mode & 0o022:
                return "FairySandbox files must not be group/world writable"
            if name in {"runner", "bwrap"} and mode & 0o111 == 0:
                return "FairySandbox executable attestation is invalid"
        attestation_digest = values.get("attestation_digest")
        if not isinstance(attestation_digest, str) or _SHA256.fullmatch(attestation_digest) is None:
            return "FairySandbox attestation digest is invalid"
        signed_values = dict(values)
        del signed_values["attestation_digest"]
        expected_digest = hashlib.sha256(
            json.dumps(
                signed_values,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        if not hmac.compare_digest(attestation_digest, expected_digest):
            return "FairySandbox attestation digest does not match"
        return None

    @staticmethod
    def _unavailable(diagnostic: str) -> RuntimeExecutorHealth:
        return RuntimeExecutorHealth(
            available=False,
            executor=_EXECUTOR,
            version=None,
            error_code=_ERROR_CODE,
            diagnostics=(diagnostic,),
        )


def _decode_output(value: bytes) -> str:
    if value.startswith((b"\xff\xfe", b"\xfe\xff")):
        return value.decode("utf-16").lstrip("\ufeff").strip()
    if value and value.count(b"\x00") > len(value) // 4:
        return value.decode("utf-16-le").lstrip("\ufeff").strip()
    return value.decode("utf-8").lstrip("\ufeff").strip()


def _find_distro_version(output: str) -> int | None:
    for line in output.splitlines():
        columns = line.lstrip("* ").split()
        if len(columns) >= 3 and columns[0] == _DISTRO:
            try:
                return int(columns[-1])
            except ValueError:
                return None
    return None
