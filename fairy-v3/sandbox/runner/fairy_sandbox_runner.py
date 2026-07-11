#!/usr/bin/env python3
from __future__ import annotations

import base64
import configparser
import getpass
import hashlib
import io
import json
import os
import re
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from uuid import UUID

RUNNER_VERSION = "1.0.0"
_ALLOWED_EXECUTORS = frozenset({"cloud_oci_worker", "wsl_fairy_sandbox"})
EXECUTOR = os.environ.get("FAIRY_SANDBOX_EXECUTOR", "wsl_fairy_sandbox")
if EXECUTOR not in _ALLOWED_EXECUTORS:
    raise RuntimeError("Sandbox executor identity is not allowed")
DEFAULT_ROOT = Path("/var/lib/fairy-sandbox")
WSL_CONFIG = Path("/etc/wsl.conf")
BWRAP = Path("/usr/bin/bwrap")
MAX_HEADER_BYTES = 1_000_000
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_EXTRACTED_BYTES = 256 * 1024 * 1024
MAX_FILES = 20_000
MAX_ARGUMENTS = 64
MAX_ARGUMENT_BYTES = 4_096
MAX_ENVIRONMENT = 32
MAX_ENVIRONMENT_VALUE_BYTES = 8_192
MAX_OUTPUT_BYTES = 1_048_576
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ENVIRONMENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_FORBIDDEN_ENVIRONMENT = frozenset(
    {
        "BASH_ENV",
        "ENV",
        "HOME",
        "IFS",
        "LD_LIBRARY_PATH",
        "LD_PRELOAD",
        "PATH",
        "PROMPT_COMMAND",
        "PYTHONHOME",
        "PYTHONPATH",
        "SHELLOPTS",
        "WSLENV",
    }
)


class RunnerProtocolError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RunnerRequest:
    job_id: UUID
    project_id: UUID | None
    conversation_id: UUID
    task_id: UUID
    version_id: UUID | None
    scope_digest: str
    workspace_generation: int
    lease_fence: int
    argv: tuple[str, ...]
    cwd: str
    environment: dict[str, str]
    timeout_seconds: int
    output_limit_bytes: int
    network_policy: str
    purpose: str
    archive_sha256: str


@dataclass(frozen=True, slots=True)
class ProcessOutcome:
    status: str
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    output_truncated: bool
    started_at: datetime
    finished_at: datetime


def parse_request_frame(frame: bytes) -> tuple[RunnerRequest, bytes]:
    if len(frame) < 4:
        raise RunnerProtocolError("request frame is truncated")
    header_length = struct.unpack(">I", frame[:4])[0]
    if not 2 <= header_length <= MAX_HEADER_BYTES:
        raise RunnerProtocolError("request header length is invalid")
    if len(frame) < 4 + header_length:
        raise RunnerProtocolError("request header is truncated")
    archive = frame[4 + header_length :]
    if not archive or len(archive) > MAX_ARCHIVE_BYTES:
        raise RunnerProtocolError("archive size is invalid")
    try:
        header = json.loads(frame[4 : 4 + header_length].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RunnerProtocolError("request header is invalid JSON") from error
    if not isinstance(header, dict) or header.get("schema_version") != 1:
        raise RunnerProtocolError("request schema is invalid")
    if header.get("archive_byte_length") != len(archive):
        raise RunnerProtocolError("archive byte length does not match")
    archive_sha256 = hashlib.sha256(archive).hexdigest()
    if header.get("archive_sha256") != archive_sha256:
        raise RunnerProtocolError("archive hash does not match")

    job_id = _uuid(header, "job_id")
    project_id = _optional_uuid(header, "project_id")
    conversation_id = _uuid(header, "conversation_id")
    task_id = _uuid(header, "task_id")
    version_id = _optional_uuid(header, "version_id")
    if (project_id is None) != (version_id is None):
        raise RunnerProtocolError("project and version bindings must match")
    scope_digest = header.get("scope_digest")
    if not isinstance(scope_digest, str) or _DIGEST.fullmatch(scope_digest) is None:
        raise RunnerProtocolError("scope digest is invalid")
    generation = _bounded_integer(header, "workspace_generation", 1, 2**63 - 1)
    fence = _bounded_integer(header, "lease_fence", 1, 2**63 - 1)
    argv = _argv(header.get("argv"))
    cwd = _cwd(header.get("cwd"))
    environment = _environment(header.get("environment"))
    timeout = _bounded_integer(header, "timeout_seconds", 1, 900)
    output_limit = _bounded_integer(
        header,
        "output_limit_bytes",
        1_024,
        MAX_OUTPUT_BYTES,
    )
    network_policy = header.get("network_policy")
    if network_policy not in {"none", "public"}:
        raise RunnerProtocolError("network policy is invalid")
    purpose = header.get("purpose")
    if purpose not in {"raw", "dependency", "review"}:
        raise RunnerProtocolError("purpose is invalid")
    if purpose == "review" and network_policy != "none":
        raise RunnerProtocolError("review purpose cannot request network access")
    if purpose == "dependency" and project_id is None:
        raise RunnerProtocolError("dependency purpose requires a Project Version")
    if purpose == "raw" and network_policy == "public" and project_id is not None:
        raise RunnerProtocolError(
            "raw public network is limited to a scratch Workspace"
        )
    return (
        RunnerRequest(
            job_id=job_id,
            project_id=project_id,
            conversation_id=conversation_id,
            task_id=task_id,
            version_id=version_id,
            scope_digest=scope_digest,
            workspace_generation=generation,
            lease_fence=fence,
            argv=argv,
            cwd=cwd,
            environment=environment,
            timeout_seconds=timeout,
            output_limit_bytes=output_limit,
            network_policy=network_policy,
            purpose=purpose,
            archive_sha256=archive_sha256,
        ),
        archive,
    )


def synchronize_workspace(
    request: RunnerRequest,
    archive: bytes,
    root: Path,
) -> Path:
    root = root.resolve(strict=False)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    scope_key = str(request.project_id or request.conversation_id)
    version_key = str(request.version_id or request.task_id)
    scope_root = root / "workspaces" / scope_key / version_key
    scope_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    current = _current_workspace(scope_root)
    if current is not None:
        current_generation, current_hash = current
        if request.workspace_generation < current_generation:
            raise RunnerProtocolError("workspace generation rollback is forbidden")
        if request.workspace_generation == current_generation:
            if request.archive_sha256 != current_hash:
                raise RunnerProtocolError("workspace generation hash does not match")
            target = scope_root / f"generation-{current_generation}"
            if not target.is_dir():
                raise RunnerProtocolError("current workspace generation is missing")
            return target

    target = scope_root / f"generation-{request.workspace_generation}"
    if target.exists():
        raise RunnerProtocolError("workspace generation target already exists")
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=scope_root))
    try:
        _extract_archive(archive, staging)
        _write_json_atomic(
            staging / ".fairy-sandbox-workspace.json",
            {
                "schema_version": 1,
                "generation": request.workspace_generation,
                "archive_sha256": request.archive_sha256,
                "scope_digest": request.scope_digest,
            },
        )
        os.replace(staging, target)
        _write_json_atomic(
            scope_root / "current.json",
            {
                "schema_version": 1,
                "generation": request.workspace_generation,
                "archive_sha256": request.archive_sha256,
            },
        )
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target


def build_isolation_command(request: RunnerRequest, workspace: Path) -> tuple[str, ...]:
    workspace = workspace.resolve(strict=False)
    sandbox_cwd = "/workspace" if request.cwd == "." else f"/workspace/{request.cwd}"
    command = [
        BWRAP.as_posix(),
        "--die-with-parent",
        "--new-session",
        "--unshare-pid",
        "--unshare-uts",
        "--unshare-ipc",
    ]
    if request.network_policy == "none":
        command.append("--unshare-net")
    command.extend(
        [
            "--clearenv",
            "--ro-bind",
            "/usr",
            "/usr",
            "--symlink",
            "usr/bin",
            "/bin",
            "--symlink",
            "usr/lib",
            "/lib",
            "--symlink",
            "usr/lib64",
            "/lib64",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--dir",
            "/home",
            "--dir",
            "/home/fairy",
            "--dir",
            "/etc",
            "--bind",
            str(workspace),
            "/workspace",
            "--setenv",
            "HOME",
            "/home/fairy",
            "--setenv",
            "PATH",
            "/usr/local/bin:/usr/bin:/bin",
            "--setenv",
            "LANG",
            "C.UTF-8",
        ]
    )
    if request.network_policy == "public":
        command.extend(
            (
                "--ro-bind",
                "/etc/resolv.conf",
                "/etc/resolv.conf",
                "--ro-bind-try",
                "/etc/hosts",
                "/etc/hosts",
                "--ro-bind-try",
                "/etc/nsswitch.conf",
                "/etc/nsswitch.conf",
                "--ro-bind",
                "/etc/ssl",
                "/etc/ssl",
            )
        )
    for name, value in sorted(request.environment.items()):
        command.extend(("--setenv", name, value))
    command.extend(("--chdir", sandbox_cwd, "--", *request.argv))
    return tuple(command)


class _BoundedOutput:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._total = 0
        self._stdout = bytearray()
        self._stderr = bytearray()
        self._lock = threading.Lock()
        self.truncated = threading.Event()

    def append(self, stream_name: str, value: bytes) -> None:
        with self._lock:
            remaining = max(0, self._limit - self._total)
            accepted = value[:remaining]
            destination = self._stdout if stream_name == "stdout" else self._stderr
            destination.extend(accepted)
            self._total += len(accepted)
            if len(accepted) < len(value):
                self.truncated.set()

    def values(self) -> tuple[bytes, bytes]:
        with self._lock:
            return bytes(self._stdout), bytes(self._stderr)


def run_bounded_process(
    argv: tuple[str, ...],
    *,
    cwd: Path,
    environment: dict[str, str],
    timeout_seconds: int,
    output_limit_bytes: int,
    cancellation_path: Path,
) -> ProcessOutcome:
    started_at = datetime.now(UTC)
    started = time.monotonic()
    if cancellation_path.is_file():
        return ProcessOutcome(
            status="cancelled",
            exit_code=None,
            stdout=b"",
            stderr=b"",
            output_truncated=False,
            started_at=started_at,
            finished_at=datetime.now(UTC),
        )
    options: dict[str, object] = {}
    if os.name == "posix":
        options["preexec_fn"] = _posix_resource_limiter(
            resource_limits(timeout_seconds=timeout_seconds)
        )
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        env=dict(environment),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        start_new_session=True,
        **options,
    )
    output = _BoundedOutput(output_limit_bytes)
    readers = (
        threading.Thread(
            target=_drain,
            args=(process.stdout, "stdout", output),
            daemon=True,
        ),
        threading.Thread(
            target=_drain,
            args=(process.stderr, "stderr", output),
            daemon=True,
        ),
    )
    for reader in readers:
        reader.start()
    status: str | None = None
    while process.poll() is None:
        if cancellation_path.is_file():
            status = "cancelled"
            break
        if output.truncated.is_set():
            status = "failed"
            break
        if time.monotonic() - started >= timeout_seconds:
            status = "timed_out"
            break
        time.sleep(0.02)
    if status is not None:
        _terminate_process_tree(process)
        exit_code = None
    else:
        exit_code = process.returncode
        status = "completed" if exit_code == 0 else "failed"
    for reader in readers:
        reader.join(timeout=2)
    stdout, stderr = output.values()
    if output.truncated.is_set() and status == "completed":
        status = "failed"
    return ProcessOutcome(
        status=status,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        output_truncated=output.truncated.is_set(),
        started_at=started_at,
        finished_at=datetime.now(UTC),
    )


def health_document() -> dict[str, object]:
    config = _wsl_configuration(WSL_CONFIG)
    runner_path = Path(__file__).resolve(strict=True)
    values: dict[str, object] = {
        "schema_version": 1,
        "executor": EXECUTOR,
        "runner_version": RUNNER_VERSION,
        "user": getpass.getuser(),
        "uid": os.getuid() if hasattr(os, "getuid") else 0,
        "default_user": config.get("user.default"),
        "config": {
            "automount.enabled": _config_boolean(config, "automount.enabled"),
            "automount.mountFsTab": _config_boolean(config, "automount.mountFsTab"),
            "interop.enabled": _config_boolean(config, "interop.enabled"),
            "interop.appendWindowsPath": _config_boolean(
                config,
                "interop.appendWindowsPath",
            ),
        },
        "runner_sha256": _file_sha256(runner_path),
        "config_sha256": _file_sha256(WSL_CONFIG),
        "bwrap_path": str(BWRAP),
        "bwrap_sha256": _file_sha256(BWRAP),
        "files": {
            "runner": _file_attestation(runner_path),
            "config": _file_attestation(WSL_CONFIG),
            "bwrap": _file_attestation(BWRAP),
        },
    }
    values["attestation_digest"] = hashlib.sha256(_canonical_json(values)).hexdigest()
    return values


def cancel_job(job_id: UUID, root: Path = DEFAULT_ROOT) -> None:
    path = root / "jobs" / str(job_id) / "cancel"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text("cancel\n", encoding="ascii")


def execute_frame(frame: bytes, root: Path = DEFAULT_ROOT) -> dict[str, object]:
    request, archive = parse_request_frame(frame)
    generation_workspace = synchronize_workspace(request, archive, root)
    job_root = root / "jobs" / str(request.job_id)
    job_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    workspace = materialize_job_workspace(generation_workspace, job_root)
    cwd = (workspace / request.cwd).resolve(strict=True)
    if not cwd.is_dir() or not cwd.is_relative_to(workspace):
        raise RunnerProtocolError(
            "cwd is not a directory in the synchronized workspace"
        )
    cancellation_path = job_root / "cancel"
    command = build_isolation_command(request, workspace)
    outcome = run_bounded_process(
        command,
        cwd=workspace,
        environment={},
        timeout_seconds=request.timeout_seconds,
        output_limit_bytes=request.output_limit_bytes,
        cancellation_path=cancellation_path,
    )
    return {
        "schema_version": 1,
        "executor": EXECUTOR,
        "executor_version": RUNNER_VERSION,
        "job_id": str(request.job_id),
        "scope_digest": request.scope_digest,
        "workspace_generation": request.workspace_generation,
        "lease_fence": request.lease_fence,
        "status": outcome.status,
        "exit_code": outcome.exit_code,
        "stdout": outcome.stdout.decode("utf-8", errors="replace"),
        "stderr": outcome.stderr.decode("utf-8", errors="replace"),
        "stdout_base64": base64.b64encode(outcome.stdout).decode("ascii"),
        "stderr_base64": base64.b64encode(outcome.stderr).decode("ascii"),
        "stdout_sha256": hashlib.sha256(outcome.stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(outcome.stderr).hexdigest(),
        "output_truncated": outcome.output_truncated,
        "started_at": outcome.started_at.isoformat(),
        "finished_at": outcome.finished_at.isoformat(),
    }


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        if Path(sys.argv[0]).name == "fairy-sandbox-health" or arguments == ["--json"]:
            _write_stdout_json(health_document())
            return 0
        if len(arguments) == 2 and arguments[0] == "--cancel":
            cancel_job(UUID(arguments[1]))
            _write_stdout_json({"cancelled": arguments[1]})
            return 0
        if arguments:
            raise RunnerProtocolError("runner arguments are invalid")
        frame = sys.stdin.buffer.read(4 + MAX_HEADER_BYTES + MAX_ARCHIVE_BYTES + 1)
        if len(frame) > 4 + MAX_HEADER_BYTES + MAX_ARCHIVE_BYTES:
            raise RunnerProtocolError("request frame exceeds the maximum size")
        _write_stdout_json(execute_frame(frame))
        return 0
    except (OSError, RunnerProtocolError, ValueError, zipfile.BadZipFile) as error:
        sys.stderr.write(f"SANDBOX_UNAVAILABLE: {error}\n")
        return 2


def _uuid(values: dict[str, object], key: str) -> UUID:
    value = values.get(key)
    if not isinstance(value, str):
        raise RunnerProtocolError(f"{key} must be a UUID")
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise RunnerProtocolError(f"{key} must be a UUID") from error
    if str(parsed) != value:
        raise RunnerProtocolError(f"{key} must be a canonical UUID")
    return parsed


def _optional_uuid(values: dict[str, object], key: str) -> UUID | None:
    return None if values.get(key) is None else _uuid(values, key)


def _bounded_integer(
    values: dict[str, object],
    key: str,
    minimum: int,
    maximum: int,
) -> int:
    value = values.get(key)
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise RunnerProtocolError(
            f"{key.replace('_', ' ')} is outside its allowed range"
        )
    return value


def _argv(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_ARGUMENTS:
        raise RunnerProtocolError("argv must be a bounded array")
    result: list[str] = []
    for item in value:
        if (
            not isinstance(item, str)
            or not item
            or "\0" in item
            or len(item.encode("utf-8")) > MAX_ARGUMENT_BYTES
        ):
            raise RunnerProtocolError("argv contains an invalid item")
        result.append(item)
    return tuple(result)


def _cwd(value: object) -> str:
    if not isinstance(value, str):
        raise RunnerProtocolError("cwd must be text")
    normalized = value.replace("\\", "/").strip() or "."
    path = PurePosixPath(normalized)
    if (
        path.is_absolute()
        or ".." in path.parts
        or ":" in normalized
        or "\0" in normalized
    ):
        raise RunnerProtocolError("cwd escapes the workspace")
    return path.as_posix()


def _environment(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or len(value) > MAX_ENVIRONMENT:
        raise RunnerProtocolError("environment must be a bounded object")
    result: dict[str, str] = {}
    for name, item in value.items():
        if (
            not isinstance(name, str)
            or _ENVIRONMENT_NAME.fullmatch(name) is None
            or name in _FORBIDDEN_ENVIRONMENT
            or name.startswith(("DYLD_", "LD_"))
            or not isinstance(item, str)
            or "\0" in item
            or len(item.encode("utf-8")) > MAX_ENVIRONMENT_VALUE_BYTES
        ):
            raise RunnerProtocolError("environment contains a forbidden entry")
        result[name] = item
    return dict(sorted(result.items()))


def _current_workspace(scope_root: Path) -> tuple[int, str] | None:
    current_path = scope_root / "current.json"
    declared = _generation_manifest(current_path) if current_path.is_file() else None
    complete: list[tuple[int, str]] = []
    for path in scope_root.glob("generation-*"):
        if path.is_symlink() or not path.is_dir():
            raise RunnerProtocolError("workspace generation path is invalid")
        suffix = path.name.removeprefix("generation-")
        if not suffix.isascii() or not suffix.isdigit() or int(suffix) < 1:
            raise RunnerProtocolError("workspace generation path is invalid")
        generation = _generation_manifest(path / ".fairy-sandbox-workspace.json")
        if generation is None or generation[0] != int(suffix):
            raise RunnerProtocolError("workspace generation manifest is invalid")
        complete.append(generation)
    if not complete:
        if declared is not None:
            raise RunnerProtocolError("current workspace generation is missing")
        return None
    latest = max(complete, key=lambda item: item[0])
    if declared is not None and declared not in complete:
        raise RunnerProtocolError("current workspace generation manifest is invalid")
    if declared != latest:
        _write_json_atomic(
            current_path,
            {
                "schema_version": 1,
                "generation": latest[0],
                "archive_sha256": latest[1],
            },
        )
    return latest


def _generation_manifest(path: Path) -> tuple[int, str] | None:
    if not path.is_file():
        return None
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RunnerProtocolError("workspace generation manifest is invalid") from error
    generation = values.get("generation") if isinstance(values, dict) else None
    archive_hash = values.get("archive_sha256") if isinstance(values, dict) else None
    if (
        isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation < 1
        or not isinstance(archive_hash, str)
        or _DIGEST.fullmatch(archive_hash) is None
    ):
        raise RunnerProtocolError("workspace generation manifest is invalid")
    return generation, archive_hash


def materialize_job_workspace(source: Path, job_root: Path) -> Path:
    target = job_root / "workspace"
    if target.exists():
        raise RunnerProtocolError("Sandbox job Workspace already exists")
    staging = job_root / f".workspace-{os.getpid()}-{time.monotonic_ns()}"
    try:
        if source.is_symlink():
            raise RunnerProtocolError("Sandbox generation contains a symlink")
        for path in source.rglob("*"):
            if path.is_symlink():
                raise RunnerProtocolError("Sandbox generation contains a symlink")
        shutil.copytree(source, staging, symlinks=False)
        for path in staging.rglob("*"):
            if path.is_symlink():
                raise RunnerProtocolError("Sandbox generation contains a symlink")
        os.replace(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target


def resource_limits(*, timeout_seconds: int) -> dict[str, int]:
    if isinstance(timeout_seconds, bool) or not 1 <= timeout_seconds <= 900:
        raise RunnerProtocolError("timeout seconds is outside its allowed range")
    return {
        "address_space_bytes": 2 * 1024 * 1024 * 1024,
        "core_bytes": 0,
        "cpu_seconds": timeout_seconds + 5,
        "file_bytes": 256 * 1024 * 1024,
        "open_files": 256,
        "processes": 512,
    }


def _posix_resource_limiter(limits: dict[str, int]):
    def apply() -> None:
        import resource

        configured = (
            (resource.RLIMIT_AS, limits["address_space_bytes"]),
            (resource.RLIMIT_CORE, limits["core_bytes"]),
            (resource.RLIMIT_CPU, limits["cpu_seconds"]),
            (resource.RLIMIT_FSIZE, limits["file_bytes"]),
            (resource.RLIMIT_NOFILE, limits["open_files"]),
            (resource.RLIMIT_NPROC, limits["processes"]),
        )
        for identifier, value in configured:
            resource.setrlimit(identifier, (value, value))

    return apply


def _extract_archive(value: bytes, destination: Path) -> None:
    try:
        archive = zipfile.ZipFile(io.BytesIO(value), "r")
    except zipfile.BadZipFile as error:
        raise RunnerProtocolError("workspace archive is not a valid ZIP") from error
    with archive:
        members = archive.infolist()
        if not members or len(members) > MAX_FILES:
            raise RunnerProtocolError("workspace archive file count is invalid")
        total_size = 0
        seen: set[str] = set()
        for member in members:
            normalized = _archive_path(member.filename)
            if normalized in seen:
                raise RunnerProtocolError("workspace archive contains duplicate paths")
            seen.add(normalized)
            mode = member.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if kind == stat.S_IFLNK:
                raise RunnerProtocolError("workspace archive symlink is forbidden")
            if kind not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise RunnerProtocolError("workspace archive special file is forbidden")
            if member.flag_bits & 0x1:
                raise RunnerProtocolError("encrypted workspace archive is forbidden")
            if member.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                raise RunnerProtocolError(
                    "workspace archive compression is unsupported"
                )
            total_size += member.file_size
            if total_size > MAX_EXTRACTED_BYTES:
                raise RunnerProtocolError("workspace archive expands beyond its limit")
        for member in members:
            normalized = _archive_path(member.filename)
            target = destination.joinpath(*PurePosixPath(normalized).parts)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True, mode=0o700)
                continue
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with archive.open(member, "r") as source, target.open("xb") as output:
                copied = shutil.copyfileobj(source, output, length=1024 * 1024)
            del copied
            if target.stat(follow_symlinks=False).st_size != member.file_size:
                raise RunnerProtocolError(
                    "workspace archive member size does not match"
                )


def _archive_path(value: str) -> str:
    if not value or "\0" in value or "\\" in value or ":" in value:
        raise RunnerProtocolError("workspace archive path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise RunnerProtocolError("workspace archive path escapes the workspace")
    return path.as_posix()


def _write_json_atomic(path: Path, values: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(_canonical_json(values) + b"\n")
    os.replace(temporary, path)


def _drain(stream: BinaryIO | None, name: str, output: _BoundedOutput) -> None:
    if stream is None:
        return
    try:
        while value := stream.read(65_536):
            output.append(name, value)
    finally:
        stream.close()


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    else:
        process.terminate()
    try:
        process.wait(timeout=0.5)
        return
    except subprocess.TimeoutExpired:
        pass
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
    else:
        process.kill()
    process.wait(timeout=2)


def _wsl_configuration(path: Path) -> dict[str, str]:
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(path, encoding="utf-8")
    values: dict[str, str] = {}
    for section in parser.sections():
        for key, value in parser.items(section):
            values[f"{section}.{key}"] = value
    return values


def _config_boolean(values: dict[str, str], key: str) -> bool | None:
    value = values.get(key)
    if value is None:
        return None
    if value.casefold() == "true":
        return True
    if value.casefold() == "false":
        return False
    return None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _file_attestation(path: Path) -> dict[str, int]:
    metadata = path.stat()
    return {
        "uid": int(metadata.st_uid),
        "mode": stat.S_IMODE(metadata.st_mode),
    }


def _canonical_json(values: dict[str, object]) -> bytes:
    return json.dumps(
        values,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _write_stdout_json(values: dict[str, object]) -> None:
    sys.stdout.write(_canonical_json(values).decode("ascii"))
    sys.stdout.write("\n")
    sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
