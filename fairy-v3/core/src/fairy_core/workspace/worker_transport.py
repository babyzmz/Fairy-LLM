from __future__ import annotations

import json
import os
import subprocess
import threading
from collections import deque
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol, TextIO


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


class SubprocessWorkerTransport:
    def __init__(
        self,
        *,
        program: str,
        args: Sequence[str],
        environment: Mapping[str, str],
        current_directory: Path | None = None,
    ) -> None:
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self._process = subprocess.Popen(
            [program, *args],
            cwd=current_directory,
            env={**os.environ, **environment},
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
        self._request_id = 0
        self._stderr: deque[str] = deque(maxlen=50)
        if self._process.stderr is not None:
            threading.Thread(
                target=self._drain_stderr,
                args=(self._process.stderr,),
                daemon=True,
                name="fairy-worker-stderr",
            ).start()

    def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
        with self._lock:
            if self._process.poll() is not None:
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
                line = self._stdout.readline()
            except (BrokenPipeError, OSError) as error:
                raise WorkerRpcError(self._interrupted_message()) from error
            if not line:
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
        with self._lock:
            if self._process.poll() is not None:
                return
            self._stdin.close()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.terminate()
                try:
                    self._process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=2)

    def _drain_stderr(self, source: TextIO) -> None:
        for line in source:
            self._stderr.append(line.rstrip())

    def _interrupted_message(self) -> str:
        detail = self._stderr[-1] if self._stderr else "no worker diagnostics"
        return f"worker interrupted: {detail}"
