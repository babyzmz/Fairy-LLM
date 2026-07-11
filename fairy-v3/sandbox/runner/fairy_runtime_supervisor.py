#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import http.client
import io
import json
import os
import platform
import re
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
import zipfile
from configparser import ConfigParser, Error as ConfigError
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol
from uuid import UUID

RUNNER_VERSION = "1.0.0"
RUNTIME_MODE = os.environ.get("FAIRY_RUNTIME_MODE", "local")
if RUNTIME_MODE not in {"local", "cloud"}:
    raise RuntimeError("Runtime supervisor mode is invalid")
EXECUTOR = "wsl_fairy_runtime" if RUNTIME_MODE == "local" else "cloud_oci_runtime"
EXECUTION_TARGET = "local" if RUNTIME_MODE == "local" else "cloud"
RUNTIME_KIND = "wsl_project" if RUNTIME_MODE == "local" else "cloud_oci"
HANDLE_PREFIX = "wsl-dynamic" if RUNTIME_MODE == "local" else "cloud-dynamic"
DEFAULT_ROOT = Path("/var/lib/fairy-sandbox")
BWRAP = Path("/usr/bin/bwrap")
WSL_CONFIG = Path("/etc/wsl.conf")
MAX_HEADER_BYTES = 1_000_000
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_EXTRACTED_BYTES = 256 * 1024 * 1024
MAX_FILES = 20_000
MAX_ARGUMENTS = 64
MAX_ARGUMENT_BYTES = 4_096
PORT_TOKEN = "{port}"
SECCOMP_FD_TOKEN = "{seccomp_fd}"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_READINESS_PATH = re.compile(r"^/(?:[A-Za-z0-9._~-]+/)*[A-Za-z0-9._~-]*$")
_ASGI_ENTRY = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*:"
    r"[A-Za-z_][A-Za-z0-9_]*$"
)
_HANDLE = re.compile(
    rf"^{HANDLE_PREFIX}:([0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}):([1-9][0-9]*)$"
)
_START_KEYS = frozenset(
    {
        "schema_version",
        "project_id",
        "conversation_id",
        "task_id",
        "version_id",
        "runtime_id",
        "preview_id",
        "execution_target",
        "kind",
        "adapter",
        "scope_digest",
        "workspace_generation",
        "lease_fence",
        "argv",
        "cwd",
        "readiness_path",
        "startup_timeout_seconds",
        "dependency_key",
        "archive_byte_length",
        "archive_sha256",
    }
)


class RuntimeProtocolError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RuntimeRequest:
    project_id: UUID
    conversation_id: UUID
    task_id: UUID
    version_id: UUID
    runtime_id: UUID
    preview_id: UUID
    adapter: str
    scope_digest: str
    workspace_generation: int
    lease_fence: int
    argv: tuple[str, ...]
    cwd: str
    readiness_path: str
    startup_timeout_seconds: int
    dependency_key: str
    archive_sha256: str

    @property
    def executor_handle(self) -> str:
        return f"{HANDLE_PREFIX}:{self.runtime_id}:{self.lease_fence}"

    @property
    def fingerprint(self) -> str:
        values = {
            "runtime_policy": "loopback-seccomp-v1",
            "project_id": str(self.project_id),
            "conversation_id": str(self.conversation_id),
            "task_id": str(self.task_id),
            "version_id": str(self.version_id),
            "runtime_id": str(self.runtime_id),
            "preview_id": str(self.preview_id),
            "adapter": self.adapter,
            "scope_digest": self.scope_digest,
            "workspace_generation": self.workspace_generation,
            "lease_fence": self.lease_fence,
            "argv": list(self.argv),
            "cwd": self.cwd,
            "readiness_path": self.readiness_path,
            "startup_timeout_seconds": self.startup_timeout_seconds,
            "dependency_key": self.dependency_key,
            "archive_sha256": self.archive_sha256,
        }
        return hashlib.sha256(_canonical_json(values)).hexdigest()


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    pid: int
    start_ticks: int
    port: int


class ProcessController(Protocol):
    def allocate_port(self) -> int: ...

    def start(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        startup_timeout_seconds: int,
        readiness_path: str,
        log_path: Path,
        port: int,
    ) -> ProcessIdentity: ...

    def is_running(
        self,
        pid: int,
        start_ticks: int,
        port: int,
        readiness_path: str,
    ) -> bool: ...

    def stop(self, pid: int, start_ticks: int) -> None: ...


class PosixProcessController:
    def allocate_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            return int(listener.getsockname()[1])

    def start(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        startup_timeout_seconds: int,
        readiness_path: str,
        log_path: Path,
        port: int,
    ) -> ProcessIdentity:
        log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        read_fd = os.memfd_create("fairy-runtime-seccomp", flags=0)
        os.write(read_fd, _network_filter())
        os.lseek(read_fd, 0, os.SEEK_SET)
        resolved_argv = tuple(
            str(read_fd) if value == SECCOMP_FD_TOKEN else value for value in argv
        )
        if SECCOMP_FD_TOKEN in resolved_argv or resolved_argv == argv:
            os.close(read_fd)
            raise RuntimeProtocolError("Runtime seccomp descriptor binding is invalid")
        try:
            with log_path.open("ab", buffering=0) as log:
                process = subprocess.Popen(
                    resolved_argv,
                    cwd=cwd,
                    env={},
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    shell=False,
                    start_new_session=True,
                    pass_fds=(read_fd,),
                )
        finally:
            os.close(read_fd)
        try:
            start_ticks = _process_start_ticks(process.pid)
            deadline = time.monotonic() + startup_timeout_seconds
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeProtocolError(
                        "Runtime process exited before readiness"
                    )
                if _http_ready(port, readiness_path):
                    return ProcessIdentity(process.pid, start_ticks, port)
                time.sleep(0.1)
            raise RuntimeProtocolError("Runtime readiness timed out")
        except BaseException:
            _terminate_process(process.pid, _optional_start_ticks(process.pid))
            raise

    def is_running(
        self,
        pid: int,
        start_ticks: int,
        port: int,
        readiness_path: str,
    ) -> bool:
        return _optional_start_ticks(pid) == start_ticks and _http_ready(
            port,
            readiness_path,
        )

    def stop(self, pid: int, start_ticks: int) -> None:
        _terminate_process(pid, start_ticks)


def parse_start_frame(frame: bytes) -> tuple[RuntimeRequest, bytes]:
    if len(frame) < 4:
        raise RuntimeProtocolError("Runtime request frame is truncated")
    header_length = struct.unpack(">I", frame[:4])[0]
    if not 2 <= header_length <= MAX_HEADER_BYTES:
        raise RuntimeProtocolError("Runtime request header length is invalid")
    if len(frame) < 4 + header_length:
        raise RuntimeProtocolError("Runtime request header is truncated")
    archive = frame[4 + header_length :]
    if not archive or len(archive) > MAX_ARCHIVE_BYTES:
        raise RuntimeProtocolError("Runtime archive size is invalid")
    try:
        header = json.loads(frame[4 : 4 + header_length].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeProtocolError("Runtime request header is invalid JSON") from error
    if (
        not isinstance(header, dict)
        or set(header) != _START_KEYS
        or header.get("schema_version") != 1
    ):
        raise RuntimeProtocolError("Runtime request schema is invalid")
    if header.get("archive_byte_length") != len(archive):
        raise RuntimeProtocolError("Runtime archive byte length does not match")
    archive_sha256 = hashlib.sha256(archive).hexdigest()
    if header.get("archive_sha256") != archive_sha256:
        raise RuntimeProtocolError("Runtime archive hash does not match")
    if (
        header.get("execution_target") != EXECUTION_TARGET
        or header.get("kind") != RUNTIME_KIND
    ):
        raise RuntimeProtocolError("Runtime target does not match the supervisor mode")

    adapter = header.get("adapter")
    if adapter not in {"vite", "next", "astro", "python_asgi"}:
        raise RuntimeProtocolError("Runtime adapter is invalid")
    argv = _argv(header.get("argv"))
    _validate_adapter_argv(str(adapter), argv)
    cwd = header.get("cwd")
    if cwd != ".":
        raise RuntimeProtocolError("Runtime cwd must be the project root")
    readiness_path = header.get("readiness_path")
    if (
        not isinstance(readiness_path, str)
        or _READINESS_PATH.fullmatch(readiness_path) is None
        or ".." in readiness_path.split("/")
    ):
        raise RuntimeProtocolError("Runtime readiness path is invalid")
    scope_digest = _digest(header, "scope_digest")
    dependency_key = _digest(header, "dependency_key")
    return (
        RuntimeRequest(
            project_id=_uuid(header, "project_id"),
            conversation_id=_uuid(header, "conversation_id"),
            task_id=_uuid(header, "task_id"),
            version_id=_uuid(header, "version_id"),
            runtime_id=_uuid(header, "runtime_id"),
            preview_id=_uuid(header, "preview_id"),
            adapter=str(adapter),
            scope_digest=scope_digest,
            workspace_generation=_bounded_integer(
                header,
                "workspace_generation",
                1,
                2**63 - 1,
            ),
            lease_fence=_bounded_integer(header, "lease_fence", 1, 2**63 - 1),
            argv=argv,
            cwd=".",
            readiness_path=readiness_path,
            startup_timeout_seconds=_bounded_integer(
                header,
                "startup_timeout_seconds",
                1,
                120,
            ),
            dependency_key=dependency_key,
            archive_sha256=archive_sha256,
        ),
        archive,
    )


def start_runtime(
    request: RuntimeRequest,
    archive: bytes,
    root: Path = DEFAULT_ROOT,
    processes: ProcessController | None = None,
) -> dict[str, object]:
    runtime_root = _runtime_root(root, request.runtime_id)
    with _runtime_lock(runtime_root / "runtime.lock"):
        return _start_runtime_locked(request, archive, root, processes)


def _start_runtime_locked(
    request: RuntimeRequest,
    archive: bytes,
    root: Path = DEFAULT_ROOT,
    processes: ProcessController | None = None,
) -> dict[str, object]:
    controller = processes or PosixProcessController()
    resolved_root = Path(root).resolve(strict=False)
    resolved_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    layer = _dependency_layer(resolved_root, request.dependency_key, request.adapter)
    runtime_root = resolved_root / "runtimes" / str(request.runtime_id)
    runtime_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_path = runtime_root / "state.json"
    existing = _read_state(state_path)
    if existing is not None:
        existing_fence = _state_integer(existing, "lease_fence")
        if request.lease_fence < existing_fence:
            raise RuntimeProtocolError("Runtime lease fence is stale")
        if request.lease_fence == existing_fence:
            if existing.get("fingerprint") != request.fingerprint:
                raise RuntimeProtocolError("Runtime lease fence fingerprint changed")
            if _state_is_running(existing, controller):
                return _response(existing, action="start", runtime_state="running")
        elif _state_is_running(existing, controller):
            controller.stop(
                _state_integer(existing, "pid"),
                _state_integer(existing, "start_ticks"),
            )

    workspace = runtime_root / f"workspace-{request.lease_fence}"
    if workspace.exists():
        shutil.rmtree(workspace)
    staging = Path(tempfile.mkdtemp(prefix=".workspace-", dir=runtime_root))
    try:
        _extract_archive(archive, staging)
        os.replace(staging, workspace)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    port = controller.allocate_port()
    command = build_isolation_command(request, workspace, layer, port)
    identity = controller.start(
        command,
        cwd=workspace,
        startup_timeout_seconds=request.startup_timeout_seconds,
        readiness_path=request.readiness_path,
        log_path=runtime_root / f"runtime-{request.lease_fence}.log",
        port=port,
    )
    if identity.port != port:
        controller.stop(identity.pid, identity.start_ticks)
        raise RuntimeProtocolError("Runtime process rebound its allocated port")
    state = {
        "schema_version": 1,
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
        "fingerprint": request.fingerprint,
        "executor_handle": request.executor_handle,
        "pid": identity.pid,
        "start_ticks": identity.start_ticks,
        "host": "127.0.0.1",
        "port": identity.port,
        "url": f"http://127.0.0.1:{identity.port}/",
        "readiness_path": request.readiness_path,
        "status": "running",
    }
    _write_json_atomic(state_path, state)
    return _response(state, action="start", runtime_state="running")


def probe_runtime(
    executor_handle: str,
    root: Path = DEFAULT_ROOT,
    processes: ProcessController | None = None,
) -> dict[str, object]:
    runtime_id, _lease_fence = _parse_handle(executor_handle)
    runtime_root = _runtime_root(root, runtime_id)
    with _runtime_lock(runtime_root / "runtime.lock"):
        return _probe_runtime_locked(executor_handle, root, processes)


def _probe_runtime_locked(
    executor_handle: str,
    root: Path = DEFAULT_ROOT,
    processes: ProcessController | None = None,
) -> dict[str, object]:
    controller = processes or PosixProcessController()
    runtime_id, lease_fence = _parse_handle(executor_handle)
    state_path = (
        Path(root).resolve(strict=False) / "runtimes" / str(runtime_id) / "state.json"
    )
    state = _required_state(state_path, executor_handle, lease_fence)
    running = _state_is_running(state, controller)
    status = "running" if running else "interrupted"
    if not running and state.get("status") == "running":
        state["status"] = "interrupted"
        _write_json_atomic(state_path, state)
    return _response(state, action="probe", runtime_state=status)


def stop_runtime(
    executor_handle: str,
    root: Path = DEFAULT_ROOT,
    processes: ProcessController | None = None,
) -> dict[str, object]:
    runtime_id, _lease_fence = _parse_handle(executor_handle)
    runtime_root = _runtime_root(root, runtime_id)
    with _runtime_lock(runtime_root / "runtime.lock"):
        return _stop_runtime_locked(executor_handle, root, processes)


def _stop_runtime_locked(
    executor_handle: str,
    root: Path = DEFAULT_ROOT,
    processes: ProcessController | None = None,
) -> dict[str, object]:
    controller = processes or PosixProcessController()
    runtime_id, lease_fence = _parse_handle(executor_handle)
    state_path = (
        Path(root).resolve(strict=False) / "runtimes" / str(runtime_id) / "state.json"
    )
    state = _required_state(state_path, executor_handle, lease_fence)
    if _state_is_running(state, controller):
        controller.stop(
            _state_integer(state, "pid"), _state_integer(state, "start_ticks")
        )
    state["status"] = "stopped"
    _write_json_atomic(state_path, state)
    return {
        "schema_version": 1,
        "executor": EXECUTOR,
        "executor_version": RUNNER_VERSION,
        "action": "stop",
        "executor_handle": executor_handle,
        "runtime_id": str(runtime_id),
        "preview_id": state["preview_id"],
        "lease_fence": lease_fence,
        "stopped": True,
    }


def build_isolation_command(
    request: RuntimeRequest,
    workspace: Path,
    layer: Path,
    port: int,
) -> tuple[str, ...]:
    workspace = workspace.resolve(strict=True)
    layer = layer.resolve(strict=True)
    mount_name = (
        "node_modules" if request.adapter in {"vite", "next", "astro"} else ".venv"
    )
    mount_source = layer / mount_name
    if not mount_source.is_dir() or mount_source.is_symlink():
        raise RuntimeProtocolError("Runtime dependency layer content is unavailable")
    mount_target = workspace / mount_name
    if mount_target.exists() and (
        mount_target.is_symlink() or not mount_target.is_dir()
    ):
        raise RuntimeProtocolError("Runtime dependency mount target is invalid")
    mount_target.mkdir(exist_ok=True)
    argv = tuple(str(port) if value == PORT_TOKEN else value for value in request.argv)
    if PORT_TOKEN in argv:
        raise RuntimeProtocolError("Runtime port token substitution failed")
    return (
        BWRAP.as_posix(),
        "--new-session",
        "--unshare-pid",
        "--unshare-uts",
        "--unshare-ipc",
        "--seccomp",
        SECCOMP_FD_TOKEN,
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
        "--bind",
        str(workspace),
        "/workspace",
        "--ro-bind",
        str(mount_source.resolve(strict=True)),
        f"/workspace/{mount_name}",
        "--setenv",
        "HOME",
        "/home/fairy",
        "--setenv",
        "PATH",
        "/workspace/.venv/bin:/usr/local/bin:/usr/bin:/bin",
        "--setenv",
        "LANG",
        "C.UTF-8",
        "--chdir",
        "/workspace",
        "--",
        *argv,
    )


def _network_filter(machine: str | None = None) -> bytes:
    architecture = (machine or platform.machine()).lower()
    blocked = {
        "x86_64": (42, 44, 46, 307, 425),
        "amd64": (42, 44, 46, 307, 425),
        "aarch64": (203, 206, 211, 269, 425),
        "arm64": (203, 206, 211, 269, 425),
    }.get(architecture)
    if blocked is None:
        raise RuntimeProtocolError("Runtime seccomp architecture is unsupported")
    instructions = [(0x20, 0, 0, 0)]
    for syscall in blocked:
        instructions.extend(
            (
                (0x15, 0, 1, syscall),
                (0x06, 0, 0, 0x00050000 | 1),
            )
        )
    instructions.append((0x06, 0, 0, 0x7FFF0000))
    return b"".join(struct.pack("=HBBI", *instruction) for instruction in instructions)


def health_document() -> dict[str, object]:
    values: dict[str, object] = {
        "schema_version": 1,
        "executor": EXECUTOR,
        "runner_version": RUNNER_VERSION,
        "user": os.environ.get("USER", "fairy"),
        "uid": os.getuid() if hasattr(os, "getuid") else 0,
        "sandbox_root": DEFAULT_ROOT.as_posix(),
        "supervisor_sha256": _file_sha256(Path(__file__).resolve(strict=True)),
        "bwrap_sha256": _file_sha256(BWRAP),
        "toolchain": {
            "node": _tool_version(("/usr/local/bin/node", "--version")),
            "npm": _tool_version(("/usr/local/bin/npm", "--version")),
            "pnpm": _tool_version(("/usr/local/bin/pnpm", "--version")),
            "yarn": _tool_version(("/usr/local/bin/yarn", "--version")),
            "uv": _tool_version(("/usr/local/bin/uv", "--version")),
        },
    }
    if RUNTIME_MODE == "local":
        values["config"] = _wsl_isolation_config(WSL_CONFIG)
    return values


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        if len(arguments) != 1 or arguments[0] not in {
            "health",
            "start",
            "probe",
            "stop",
        }:
            raise RuntimeProtocolError("Runtime supervisor action is invalid")
        action = arguments[0]
        if action == "health":
            response = health_document()
        elif action == "start":
            frame = sys.stdin.buffer.read(4 + MAX_HEADER_BYTES + MAX_ARCHIVE_BYTES + 1)
            if len(frame) > 4 + MAX_HEADER_BYTES + MAX_ARCHIVE_BYTES:
                raise RuntimeProtocolError("Runtime request exceeds the protocol limit")
            request, archive = parse_start_frame(frame)
            response = start_runtime(request, archive)
        else:
            values = _read_handle_request(sys.stdin.buffer.read(MAX_HEADER_BYTES + 1))
            response = (
                probe_runtime(values) if action == "probe" else stop_runtime(values)
            )
        sys.stdout.buffer.write(_canonical_json(response) + b"\n")
        return 0
    except (OSError, RuntimeProtocolError, ValueError, zipfile.BadZipFile) as error:
        sys.stderr.write(f"SANDBOX_UNAVAILABLE: {error}\n")
        return 2


def _validate_adapter_argv(adapter: str, argv: tuple[str, ...]) -> None:
    valid = {
        "vite": (
            "node_modules/.bin/vite",
            "--host",
            "127.0.0.1",
            "--port",
            PORT_TOKEN,
            "--strictPort",
        ),
        "next": (
            "node_modules/.bin/next",
            "dev",
            "-H",
            "127.0.0.1",
            "-p",
            PORT_TOKEN,
        ),
        "astro": (
            "node_modules/.bin/astro",
            "dev",
            "--host",
            "127.0.0.1",
            "--port",
            PORT_TOKEN,
        ),
    }
    if adapter in valid:
        if argv != valid[adapter]:
            raise RuntimeProtocolError("Runtime argv does not match its adapter")
        return
    if (
        len(argv) != 10
        or argv[:3] != (".venv/bin/python", "-m", "uvicorn")
        or _ASGI_ENTRY.fullmatch(argv[3]) is None
        or argv[4:]
        != (
            "--host",
            "127.0.0.1",
            "--port",
            PORT_TOKEN,
            "--no-access-log",
        )
    ):
        raise RuntimeProtocolError("Runtime argv does not match python_asgi")


def _argv(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_ARGUMENTS:
        raise RuntimeProtocolError("Runtime argv must be a bounded array")
    result: list[str] = []
    for item in value:
        if (
            not isinstance(item, str)
            or not item
            or "\0" in item
            or len(item.encode("utf-8")) > MAX_ARGUMENT_BYTES
        ):
            raise RuntimeProtocolError("Runtime argv contains an invalid item")
        result.append(item)
    if sum(item.count(PORT_TOKEN) for item in result) != 1:
        raise RuntimeProtocolError("Runtime argv requires one port token")
    return tuple(result)


def _dependency_layer(root: Path, key: str, adapter: str) -> Path:
    layer = root / "dependencies" / key
    complete = layer / "complete.json"
    try:
        if layer.is_symlink() or not layer.is_dir() or complete.is_symlink():
            raise RuntimeProtocolError("Runtime dependency layer is unavailable")
        metadata = json.loads(complete.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeProtocolError("Runtime dependency layer is unavailable") from error
    allowed_managers = (
        {"npm", "pnpm", "yarn"} if adapter in {"vite", "next", "astro"} else {"uv"}
    )
    if (
        not isinstance(metadata, dict)
        or metadata.get("schema_version") != 1
        or metadata.get("dependency_key") != key
        or metadata.get("dependency_manager") not in allowed_managers
        or set(metadata) != {"schema_version", "dependency_key", "dependency_manager"}
    ):
        raise RuntimeProtocolError("Runtime dependency layer binding does not match")
    expected = "node_modules" if adapter in {"vite", "next", "astro"} else ".venv"
    content = layer / expected
    if content.is_symlink() or not content.is_dir():
        raise RuntimeProtocolError("Runtime dependency layer content is unavailable")
    return layer


def _extract_archive(content: bytes, destination: Path) -> None:
    total = 0
    count = 0
    root = destination.resolve(strict=True)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for info in archive.infolist():
            count += 1
            if count > MAX_FILES:
                raise RuntimeProtocolError("Runtime archive contains too many files")
            path = PurePosixPath(info.filename)
            if (
                not info.filename
                or path.is_absolute()
                or ".." in path.parts
                or "\\" in info.filename
                or "\0" in info.filename
                or ":" in info.filename
                or stat.S_ISLNK(info.external_attr >> 16)
            ):
                raise RuntimeProtocolError("Runtime archive path is invalid")
            total += info.file_size
            if total > MAX_EXTRACTED_BYTES:
                raise RuntimeProtocolError("Runtime archive expands beyond its limit")
            target = (root / Path(*path.parts)).resolve(strict=False)
            if not target.is_relative_to(root):
                raise RuntimeProtocolError("Runtime archive escapes the workspace")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True, mode=0o700)
                continue
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with archive.open(info, "r") as source, target.open("xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            target.chmod(0o600)


def _state_is_running(state: dict[str, object], controller: ProcessController) -> bool:
    if state.get("status") != "running":
        return False
    return controller.is_running(
        _state_integer(state, "pid"),
        _state_integer(state, "start_ticks"),
        _state_integer(state, "port"),
        _state_string(state, "readiness_path"),
    )


def _required_state(
    path: Path, executor_handle: str, lease_fence: int
) -> dict[str, object]:
    state = _read_state(path)
    if (
        state is None
        or state.get("executor_handle") != executor_handle
        or state.get("lease_fence") != lease_fence
    ):
        raise RuntimeProtocolError("Runtime state binding does not match")
    return state


def _runtime_root(root: Path, runtime_id: UUID) -> Path:
    resolved_root = Path(root).resolve(strict=False)
    resolved_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    runtimes = resolved_root / "runtimes"
    runtimes.mkdir(parents=True, exist_ok=True, mode=0o700)
    runtime_root = runtimes / str(runtime_id)
    runtime_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if runtime_root.is_symlink() or not runtime_root.is_dir():
        raise RuntimeProtocolError("Runtime state directory is invalid")
    return runtime_root


@contextmanager
def _runtime_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a+b") as lock:
        lock.flush()
        if os.name == "posix":
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "posix":
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _read_state(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeProtocolError("Runtime state is invalid") from error
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise RuntimeProtocolError("Runtime state schema is invalid")
    return value


def _response(
    record: dict[str, object],
    *,
    action: str,
    runtime_state: str,
) -> dict[str, object]:
    values = {
        "schema_version": 1,
        "executor": EXECUTOR,
        "executor_version": RUNNER_VERSION,
        "action": action,
        "executor_handle": record["executor_handle"],
        "runtime_id": record["runtime_id"],
        "preview_id": record["preview_id"],
        "lease_fence": record["lease_fence"],
        "state": runtime_state,
    }
    if runtime_state != "stopped":
        values.update(
            {
                "host": record["host"],
                "port": record["port"],
                "url": record["url"],
            }
        )
    if action == "start":
        for key in (
            "project_id",
            "conversation_id",
            "task_id",
            "version_id",
            "runtime_id",
            "scope_digest",
            "workspace_generation",
            "dependency_key",
        ):
            values[key] = record[key]
    return values


def _read_handle_request(content: bytes) -> str:
    if not content or len(content) > MAX_HEADER_BYTES:
        raise RuntimeProtocolError("Runtime handle request size is invalid")
    try:
        values = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeProtocolError("Runtime handle request is invalid JSON") from error
    if (
        not isinstance(values, dict)
        or set(values) != {"schema_version", "executor_handle"}
        or values.get("schema_version") != 1
        or not isinstance(values.get("executor_handle"), str)
    ):
        raise RuntimeProtocolError("Runtime handle request schema is invalid")
    handle = str(values["executor_handle"])
    _parse_handle(handle)
    return handle


def _parse_handle(value: str) -> tuple[UUID, int]:
    match = _HANDLE.fullmatch(value)
    if match is None:
        raise RuntimeProtocolError("Runtime executor handle is invalid")
    return UUID(match.group(1)), int(match.group(2))


def _uuid(values: dict[str, object], key: str) -> UUID:
    value = values.get(key)
    if not isinstance(value, str):
        raise RuntimeProtocolError(f"{key} must be a UUID")
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise RuntimeProtocolError(f"{key} must be a UUID") from error
    if str(parsed) != value:
        raise RuntimeProtocolError(f"{key} must be a canonical UUID")
    return parsed


def _digest(values: dict[str, object], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise RuntimeProtocolError(f"{key} must be a lowercase SHA-256 digest")
    return value


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
        raise RuntimeProtocolError(f"{key} is outside its allowed range")
    return value


def _state_integer(values: dict[str, object], key: str) -> int:
    value = values.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RuntimeProtocolError(f"Runtime state {key} is invalid")
    return value


def _state_string(values: dict[str, object], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeProtocolError(f"Runtime state {key} is invalid")
    return value


def _write_json_atomic(path: Path, values: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(_canonical_json(values) + b"\n")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _process_start_ticks(pid: int) -> int:
    content = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    suffix = content[content.rfind(")") + 2 :].split()
    return int(suffix[19])


def _optional_start_ticks(pid: int) -> int | None:
    try:
        return _process_start_ticks(pid)
    except (OSError, ValueError, IndexError):
        return None


def _http_ready(port: int, readiness_path: str) -> bool:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.5)
    try:
        connection.request("GET", readiness_path, headers={"Host": "127.0.0.1"})
        response = connection.getresponse()
        response.read(1024)
        return 200 <= response.status < 300
    except OSError:
        return False
    finally:
        connection.close()


def _terminate_process(pid: int, expected_start_ticks: int | None) -> None:
    if (
        expected_start_ticks is None
        or _optional_start_ticks(pid) != expected_start_ticks
    ):
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if _optional_start_ticks(pid) != expected_start_ticks:
            return
        time.sleep(0.05)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _wsl_isolation_config(path: Path) -> dict[str, bool]:
    parser = ConfigParser(interpolation=None)
    try:
        with path.open("r", encoding="utf-8") as source:
            parser.read_file(source)
        return {
            "automount.enabled": parser.getboolean("automount", "enabled"),
            "automount.mountFsTab": parser.getboolean("automount", "mountFsTab"),
            "interop.enabled": parser.getboolean("interop", "enabled"),
            "interop.appendWindowsPath": parser.getboolean(
                "interop",
                "appendWindowsPath",
            ),
        }
    except (ConfigError, OSError, ValueError) as error:
        raise RuntimeProtocolError("WSL isolation configuration is invalid") from error


def _tool_version(argv: tuple[str, ...]) -> str:
    try:
        completed = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=5,
            env={
                "HOME": "/tmp",
                "LANG": "C.UTF-8",
                "PATH": "/usr/local/bin:/usr/bin:/bin",
            },
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeProtocolError("Runtime toolchain could not be attested") from error
    value = completed.stdout.decode("utf-8", errors="strict").strip()
    if completed.returncode != 0 or not value or "\n" in value:
        raise RuntimeProtocolError("Runtime toolchain version is invalid")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
