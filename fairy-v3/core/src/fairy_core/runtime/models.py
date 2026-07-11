from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID


class RuntimeExecutorError(RuntimeError):
    def __init__(self, message: str, *, error_code: str = "WORKER_INTERRUPTED") -> None:
        super().__init__(message)
        self.error_code = error_code


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
class RuntimeStartResult:
    executor_handle: str
    host: str
    port: int
    url: str
    state: ExecutorRuntimeState

    def __post_init__(self) -> None:
        if self.state is not ExecutorRuntimeState.RUNNING:
            raise ValueError("started Runtime must be running")
        _validate_endpoint(self.host, self.port, self.url)


@dataclass(frozen=True, slots=True)
class RuntimeProbeResult:
    executor_handle: str
    state: ExecutorRuntimeState
    host: str | None
    port: int | None
    url: str | None

    def __post_init__(self) -> None:
        if self.state is ExecutorRuntimeState.RUNNING:
            if self.host is None or self.port is None or self.url is None:
                raise ValueError("running Runtime probe requires endpoint metadata")
            _validate_endpoint(self.host, self.port, self.url)
        elif any(value is not None for value in (self.host, self.port, self.url)):
            if self.host is None or self.port is None or self.url is None:
                raise ValueError("Runtime probe endpoint fields must be present together")
            _validate_endpoint(self.host, self.port, self.url)


@dataclass(frozen=True, slots=True)
class RuntimeStopResult:
    stopped: bool

    def __post_init__(self) -> None:
        if self.stopped is not True:
            raise ValueError("Runtime stop result must confirm stopped=true")


def _validate_endpoint(host: str, port: int, url: str) -> None:
    if host != "127.0.0.1":
        raise ValueError("local Runtime host must be exact IPv4 loopback")
    if isinstance(port, bool) or not 1 <= port <= 65_535:
        raise ValueError("Runtime port must be between 1 and 65535")
    try:
        parsed = urlsplit(url)
        parsed_port = parsed.port
    except ValueError as error:
        raise ValueError("Runtime URL is invalid") from error
    if (
        parsed.scheme != "http"
        or parsed.hostname != host
        or parsed_port != port
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/")
    ):
        raise ValueError("Runtime URL must match the loopback endpoint")
