from __future__ import annotations

import json
import os
import subprocess
import threading
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Any, ClassVar, Protocol, TextIO

from fairy_core.system_actions.models import SystemActionWorkerResult

_INHERITED_WORKER_ENVIRONMENT = (
    "LANG",
    "LC_ALL",
    "LOCALAPPDATA",
    "PATH",
    "PATHEXT",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "WINDIR",
)


class WorkerRpcError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_code: str = "WORKER_INTERRUPTED",
        rpc_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.rpc_code = rpc_code


class WorkerTransport(Protocol):
    def call(self, method: str, params: dict[str, object]) -> dict[str, object]: ...

    def close(self) -> None: ...


class RustSystemActionWorker:
    _ACTION_FIELDS: ClassVar[dict[str, frozenset[str]]] = {
        "open_url": frozenset({"type", "url"}),
        "reveal_path": frozenset({"type", "project_id", "version_id", "relative_path"}),
        "copy_text": frozenset({"type", "text"}),
        "notify": frozenset({"type", "title", "body", "level"}),
        "open_settings": frozenset({"type", "page"}),
    }

    def __init__(self, transport: WorkerTransport) -> None:
        self._transport = transport

    def execute(
        self,
        *,
        action: dict[str, object],
        idempotency_key: str,
    ) -> SystemActionWorkerResult:
        self._validate_action(action)
        if not idempotency_key or len(idempotency_key) > 255:
            raise ValueError("system action idempotency key is invalid")
        result = self._transport.call(
            "system.execute",
            {
                "idempotency_key": idempotency_key,
                "action": dict(action),
            },
        )
        if set(result) != {"action_type", "completed", "replayed"}:
            raise WorkerRpcError("system action worker result has an invalid shape")
        try:
            return SystemActionWorkerResult.model_validate(result)
        except ValueError as error:
            raise WorkerRpcError("system action worker result is invalid") from error

    @classmethod
    def _validate_action(cls, action: dict[str, object]) -> None:
        action_type = action.get("type")
        if not isinstance(action_type, str) or action_type not in cls._ACTION_FIELDS:
            raise ValueError("system action type is invalid")
        if set(action) != cls._ACTION_FIELDS[action_type]:
            raise ValueError("system action contains unexpected fields")
        if not all(isinstance(value, str) for value in action.values()):
            raise ValueError("system action fields must be text")


class SubprocessWorkerTransport:
    def __init__(
        self,
        *,
        program: str,
        args: Sequence[str],
        environment: Mapping[str, str],
        current_directory: Path | None = None,
        request_timeout_seconds: float | None = None,
    ) -> None:
        if request_timeout_seconds is not None and request_timeout_seconds <= 0:
            raise ValueError("Worker request timeout must be positive")
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self._process = subprocess.Popen(
            [program, *args],
            cwd=current_directory,
            env=_worker_environment(environment),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            bufsize=1,
            creationflags=creation_flags,
        )
        if self._process.stdin is None or self._process.stdout is None:
            self._process.kill()
            raise WorkerRpcError("worker stdio is unavailable")
        self._stdin: TextIO = self._process.stdin
        self._stdout: TextIO = self._process.stdout
        self._lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._closed = threading.Event()
        self._timeout = request_timeout_seconds
        self._responses: Queue[str] = Queue(maxsize=2)
        self._reader_failed = threading.Event()
        self._request_id = 0
        self._stderr: deque[str] = deque(maxlen=50)
        self._reader = threading.Thread(
            target=self._read_stdout,
            daemon=True,
            name="fairy-worker-stdout",
        )
        self._reader.start()
        self._stderr_reader: threading.Thread | None = None
        if self._process.stderr is not None:
            self._stderr_reader = threading.Thread(
                target=self._drain_stderr,
                args=(self._process.stderr,),
                daemon=True,
                name="fairy-worker-stderr",
            )
            self._stderr_reader.start()

    def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
        with self._lock:
            if self._closed.is_set() or self._process.poll() is not None:
                raise WorkerRpcError(self._interrupted_message())
            self._request_id += 1
            request = {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": method,
                "params": params,
            }
            try:
                self._stdin.write(json.dumps(request, ensure_ascii=False, separators=(",", ":")))
                self._stdin.write("\n")
                self._stdin.flush()
                line = self._responses.get(timeout=self._timeout)
            except Empty as error:
                self.close()
                raise WorkerRpcError(
                    "Worker request timed out; operation result is unknown and was not retried.",
                    error_code="WORKER_TIMEOUT",
                ) from error
            except (BrokenPipeError, OSError) as error:
                raise WorkerRpcError(self._interrupted_message()) from error
            if not line or self._closed.is_set() or self._reader_failed.is_set():
                raise WorkerRpcError(self._interrupted_message())
            try:
                response: dict[str, Any] = json.loads(line)
            except json.JSONDecodeError as error:
                raise WorkerRpcError("worker returned invalid JSON") from error
            if response.get("id") != self._request_id:
                raise WorkerRpcError("worker response id does not match request")
            failure = response.get("error")
            if isinstance(failure, dict):
                data = failure.get("data") if isinstance(failure.get("data"), dict) else {}
                raise WorkerRpcError(
                    str(failure.get("message", "worker command failed")),
                    error_code=str(data.get("error_code", "WORKER_INTERRUPTED")),
                    rpc_code=failure.get("code") if isinstance(failure.get("code"), int) else None,
                )
            result = response.get("result")
            if not isinstance(result, dict):
                raise WorkerRpcError("worker result must be an object")
            return result

    def close(self) -> None:
        # Do not wait for the request lock: its owner may be waiting for this process.
        with self._close_lock:
            self._closed.set()
            idle = self._lock.acquire(blocking=False)
            try:
                if self._process.poll() is None:
                    if idle:
                        self._stdin.close()
                    else:
                        self._process.terminate()
                    try:
                        self._process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self._process.kill()
                        self._process.wait(timeout=2)
            finally:
                if idle:
                    self._lock.release()
            self._reader.join(timeout=2)
            if self._stderr_reader is not None:
                self._stderr_reader.join(timeout=2)
            for stream in (self._stdin, self._stdout, self._process.stderr):
                if stream is not None:
                    stream.close()

    def _read_stdout(self) -> None:
        try:
            for line in self._stdout:
                try:
                    self._responses.put_nowait(line)
                except Full:
                    self._reader_failed.set()
                    with suppress(OSError):
                        self._process.kill()
                    return
        except (OSError, UnicodeError):
            self._reader_failed.set()
        finally:
            with suppress(Full):
                self._responses.put_nowait("")

    def _drain_stderr(self, source: TextIO) -> None:
        for line in source:
            self._stderr.append(line.rstrip())

    def _interrupted_message(self) -> str:
        detail = self._stderr[-1] if self._stderr else "no worker diagnostics"
        return f"worker interrupted: {detail}"


class RestartingWorkerTransport:
    """Recreate a failed worker without replaying state-changing calls."""

    def __init__(self, factory: Callable[[], WorkerTransport]) -> None:
        self._factory = factory
        self._transport = factory()
        self._lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._closed = False
        self._generation = 0

    @property
    def generation(self) -> int:
        with self._state_lock:
            return self._generation

    def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
        with self._lock:
            with self._state_lock:
                if self._closed:
                    raise WorkerRpcError("Browser Worker is closed")
                transport = self._transport
            try:
                return transport.call(method, params)
            except WorkerRpcError as error:
                if error.error_code not in {"WORKER_INTERRUPTED", "WORKER_TIMEOUT"}:
                    raise
                self._restart(error)
                if method == "browser.health":
                    return self._transport.call(method, params)
                raise

    def close(self) -> None:
        with self._state_lock:
            self._closed = True
            transport = self._transport
        transport.close()

    def _restart(self, original_error: WorkerRpcError) -> None:
        with self._state_lock:
            if self._closed:
                raise original_error
            previous = self._transport
            with suppress(Exception):
                previous.close()
            self._generation += 1
            try:
                self._transport = self._factory()
            except Exception as restart_error:
                raise WorkerRpcError(
                    f"{original_error}; worker restart failed: {restart_error}"
                ) from original_error


def _worker_environment(overrides: Mapping[str, str]) -> dict[str, str]:
    environment = {
        name: os.environ[name] for name in _INHERITED_WORKER_ENVIRONMENT if name in os.environ
    }
    environment.update(overrides)
    environment.update(
        {
            "GCM_INTERACTIVE": "Never",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        }
    )
    return environment
