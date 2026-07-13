from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from fairy_core.domain.errors import DomainError
from fairy_core.domain.execution import RuntimeKind

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_READINESS_PATH = re.compile(r"^/(?:[A-Za-z0-9._~-]+/)*[A-Za-z0-9._~-]*$")
_MAX_WORKSPACE_ARCHIVE_BYTES = 128 * 1024 * 1024
_PORT_TOKEN = "{port}"
_SERVICE_ID = re.compile(r"^[a-z][a-z0-9-]{0,31}$")


class RuntimeExecutorError(DomainError):
    def __init__(self, message: str, *, error_code: str = "WORKER_INTERRUPTED") -> None:
        super().__init__(message)
        self.error_code = error_code
        self.code = error_code


class ExecutorRuntimeState(StrEnum):
    RUNNING = "running"
    STOPPED = "stopped"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True, slots=True)
class RuntimeExecutorHealth:
    available: bool
    executor: str
    version: str | None
    error_code: str | None
    diagnostics: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.executor.strip():
            raise ValueError("executor is required")
        if self.available and self.error_code is not None:
            raise ValueError("available executor cannot carry an error_code")
        if not self.available and self.error_code is None:
            raise ValueError("unavailable executor requires an error_code")


@dataclass(frozen=True, slots=True)
class StaticRuntimeStart:
    project_id: UUID
    version_id: UUID
    preview_id: UUID
    project_root: Path
    entry_path: str = "index.html"

    def __post_init__(self) -> None:
        if self.entry_path != "index.html":
            raise ValueError("static Preview entry_path must be index.html")
        object.__setattr__(self, "project_root", Path(self.project_root).resolve(strict=False))


@dataclass(frozen=True, slots=True)
class RuntimeServiceStart:
    service_id: str
    adapter: str
    argv: tuple[str, ...]
    cwd: str
    readiness_path: str
    startup_timeout_seconds: int
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "argv", tuple(self.argv))
        object.__setattr__(self, "depends_on", tuple(self.depends_on))
        if _SERVICE_ID.fullmatch(self.service_id) is None:
            raise ValueError("Runtime service_id is invalid")
        if self.adapter not in {"vite", "next", "astro", "python_asgi", "node_http"}:
            raise ValueError("Runtime service adapter is invalid")
        if self.cwd == ".":
            pass
        elif (
            not self.cwd
            or self.cwd.startswith(("/", "\\"))
            or "\\" in self.cwd
            or any(part in {"", ".", ".."} for part in self.cwd.split("/"))
        ):
            raise ValueError("Runtime service cwd is invalid")
        if _READINESS_PATH.fullmatch(self.readiness_path) is None or ".." in (
            self.readiness_path.split("/")
        ):
            raise ValueError("Runtime service readiness_path is invalid")
        if not 1 <= self.startup_timeout_seconds <= 120:
            raise ValueError("Runtime service startup timeout is invalid")
        if not self.argv or any(not isinstance(value, str) or not value for value in self.argv):
            raise ValueError("Runtime service argv is invalid")
        expected_tokens = 0 if self.adapter == "node_http" else 1
        if sum(value.count(_PORT_TOKEN) for value in self.argv) != expected_tokens:
            raise ValueError("Runtime service port binding is invalid")
        if self.adapter != "node_http" and "127.0.0.1" not in self.argv:
            raise ValueError("Runtime service must bind exact IPv4 loopback")
        if len(set(self.depends_on)) != len(self.depends_on) or self.service_id in self.depends_on:
            raise ValueError("Runtime service dependencies are invalid")


@dataclass(frozen=True, slots=True)
class DynamicRuntimeStart:
    project_id: UUID
    conversation_id: UUID
    task_id: UUID
    version_id: UUID
    runtime_id: UUID
    preview_id: UUID
    project_root: Path
    execution_target: str
    kind: RuntimeKind
    adapter: str
    scope_digest: str
    workspace_generation: int
    lease_fence: int
    argv: tuple[str, ...]
    cwd: str
    readiness_path: str
    startup_timeout_seconds: int
    dependency_key: str
    workspace_archive: bytes
    archive_sha256: str
    services: tuple[RuntimeServiceStart, ...] = ()
    public_service_id: str = "app"

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_root", Path(self.project_root).resolve(strict=False))
        object.__setattr__(self, "argv", tuple(self.argv))
        services = tuple(self.services) or (
            RuntimeServiceStart(
                service_id=self.public_service_id,
                adapter=self.adapter,
                argv=self.argv,
                cwd=self.cwd,
                readiness_path=self.readiness_path,
                startup_timeout_seconds=self.startup_timeout_seconds,
            ),
        )
        object.__setattr__(self, "services", services)
        archive = bytes(self.workspace_archive)
        object.__setattr__(self, "workspace_archive", archive)
        if self.execution_target not in {"local", "cloud"}:
            raise ValueError("dynamic Runtime execution_target is invalid")
        expected_kind = (
            RuntimeKind.WSL_PROJECT if self.execution_target == "local" else RuntimeKind.CLOUD_OCI
        )
        if self.kind is not expected_kind:
            raise ValueError("dynamic Runtime kind does not match execution_target")
        if not self.adapter.strip():
            raise ValueError("dynamic Runtime adapter is required")
        if not 1 <= len(services) <= 8:
            raise ValueError("dynamic Runtime requires between one and eight services")
        service_ids = {service.service_id for service in services}
        if len(service_ids) != len(services) or self.public_service_id not in service_ids:
            raise ValueError("dynamic Runtime public service is invalid")
        if any(set(service.depends_on) - service_ids for service in services):
            raise ValueError("dynamic Runtime service dependency is unknown")
        _validate_service_graph(services)
        public = next(
            service for service in services if service.service_id == self.public_service_id
        )
        if (
            self.adapter != public.adapter
            or self.argv != public.argv
            or self.cwd != public.cwd
            or self.readiness_path != public.readiness_path
            or self.startup_timeout_seconds != public.startup_timeout_seconds
        ):
            raise ValueError("dynamic Runtime public service metadata changed")
        if _SHA256.fullmatch(self.scope_digest) is None:
            raise ValueError("dynamic Runtime scope_digest is invalid")
        if _SHA256.fullmatch(self.dependency_key) is None:
            raise ValueError("dynamic Runtime dependency_key is invalid")
        if isinstance(self.workspace_generation, bool) or self.workspace_generation < 1:
            raise ValueError("dynamic Runtime workspace_generation is invalid")
        if isinstance(self.lease_fence, bool) or self.lease_fence < 1:
            raise ValueError("dynamic Runtime lease_fence is invalid")
        if self.cwd != "." and (
            self.cwd.startswith(("/", "\\"))
            or "\\" in self.cwd
            or any(part in {"", ".", ".."} for part in self.cwd.split("/"))
        ):
            raise ValueError("dynamic Runtime cwd is invalid")
        if _READINESS_PATH.fullmatch(self.readiness_path) is None or ".." in (
            self.readiness_path.split("/")
        ):
            raise ValueError("dynamic Runtime readiness_path is invalid")
        if not 1 <= self.startup_timeout_seconds <= 120:
            raise ValueError("dynamic Runtime startup timeout is invalid")
        if not self.argv or any(not isinstance(value, str) or not value for value in self.argv):
            raise ValueError("dynamic Runtime argv is invalid")
        expected_tokens = 0 if self.adapter == "node_http" else 1
        if sum(value.count(_PORT_TOKEN) for value in self.argv) != expected_tokens:
            raise ValueError("dynamic Runtime argv has invalid Core port binding")
        if self.adapter != "node_http" and "127.0.0.1" not in self.argv:
            raise ValueError("dynamic Runtime must bind exact IPv4 loopback")
        if not archive or len(archive) > _MAX_WORKSPACE_ARCHIVE_BYTES:
            raise ValueError("dynamic Runtime workspace archive is invalid")
        if _SHA256.fullmatch(self.archive_sha256) is None or self.archive_sha256 != (
            hashlib.sha256(archive).hexdigest()
        ):
            raise ValueError("dynamic Runtime workspace archive digest is invalid")


def _validate_service_graph(services: tuple[RuntimeServiceStart, ...]) -> None:
    dependencies = {service.service_id: service.depends_on for service in services}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(service_id: str) -> None:
        if service_id in visiting:
            raise ValueError("dynamic Runtime service graph contains a cycle")
        if service_id in visited:
            return
        visiting.add(service_id)
        for dependency in dependencies[service_id]:
            visit(dependency)
        visiting.remove(service_id)
        visited.add(service_id)

    for service_id in dependencies:
        visit(service_id)


@dataclass(frozen=True, slots=True)
class RuntimeRecoveryTarget:
    runtime_id: UUID
    preview_id: UUID
    execution_target: str
    kind: RuntimeKind
    lease_fence: int

    def __post_init__(self) -> None:
        if self.execution_target not in {"local", "cloud"}:
            raise ValueError("Runtime recovery execution_target is invalid")
        expected_target = "cloud" if self.kind is RuntimeKind.CLOUD_OCI else "local"
        if self.execution_target != expected_target:
            raise ValueError("Runtime recovery kind does not match execution_target")
        if isinstance(self.lease_fence, bool) or self.lease_fence < 1:
            raise ValueError("Runtime recovery fence is invalid")


@dataclass(frozen=True, slots=True)
class RuntimeStartResult:
    executor_handle: str
    host: str
    port: int
    url: str
    state: ExecutorRuntimeState
    execution_target: str = "local"

    def __post_init__(self) -> None:
        if self.state is not ExecutorRuntimeState.RUNNING:
            raise ValueError("started Runtime must be running")
        _validate_endpoint(self.host, self.port, self.url, self.execution_target)


@dataclass(frozen=True, slots=True)
class RuntimeProbeResult:
    executor_handle: str
    state: ExecutorRuntimeState
    host: str | None
    port: int | None
    url: str | None
    execution_target: str = "local"

    def __post_init__(self) -> None:
        if self.state is ExecutorRuntimeState.RUNNING:
            if self.host is None or self.port is None or self.url is None:
                raise ValueError("running Runtime probe requires endpoint metadata")
            _validate_endpoint(self.host, self.port, self.url, self.execution_target)
        elif any(value is not None for value in (self.host, self.port, self.url)):
            if self.host is None or self.port is None or self.url is None:
                raise ValueError("Runtime probe endpoint fields must be present together")
            _validate_endpoint(self.host, self.port, self.url, self.execution_target)
        elif self.execution_target not in {"local", "cloud"}:
            raise ValueError("Runtime probe execution_target is invalid")


@dataclass(frozen=True, slots=True)
class RuntimeStopResult:
    stopped: bool

    def __post_init__(self) -> None:
        if self.stopped is not True:
            raise ValueError("Runtime stop result must confirm stopped=true")


def _validate_endpoint(host: str, port: int, url: str, execution_target: str) -> None:
    if execution_target not in {"local", "cloud"}:
        raise ValueError("Runtime execution_target is invalid")
    if isinstance(port, bool) or not 1 <= port <= 65_535:
        raise ValueError("Runtime port must be between 1 and 65535")
    try:
        parsed = urlsplit(url)
        parsed_port = parsed.port
    except ValueError as error:
        raise ValueError("Runtime URL is invalid") from error
    common_invalid = (
        parsed.hostname != host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/")
    )
    if execution_target == "local" and (
        common_invalid or host != "127.0.0.1" or parsed.scheme != "http" or parsed_port != port
    ):
        raise ValueError("Runtime URL must match the loopback endpoint")
    if execution_target == "cloud" and (
        common_invalid
        or not host
        or host in {"127.0.0.1", "localhost", "::1"}
        or parsed.scheme != "https"
        or port != 443
        or parsed_port not in {None, 443}
    ):
        raise ValueError("Runtime URL must match the cloud HTTPS endpoint")
