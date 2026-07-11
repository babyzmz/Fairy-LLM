from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from fairy_core.runtime.models import (
    ExecutorRuntimeState,
    RuntimeExecutorError,
    RuntimeExecutorHealth,
    RuntimeProbeResult,
    RuntimeStartResult,
    RuntimeStopResult,
    StaticRuntimeStart,
)
from fairy_core.workspace.worker_transport import WorkerRpcError, WorkerTransport


class RustRuntimeExecutor:
    def __init__(
        self,
        transport: WorkerTransport,
        *,
        managed_root: Path,
        worker_version: str | None = None,
    ) -> None:
        self._transport = transport
        self._managed_root = Path(managed_root).resolve(strict=False)
        self._worker_version = worker_version

    def health(self) -> RuntimeExecutorHealth:
        return RuntimeExecutorHealth(
            available=True,
            executor="rust_local_worker",
            version=self._worker_version,
            error_code=None,
            diagnostics=("persistent stdio transport configured",),
        )

    def start_static(self, request: StaticRuntimeStart) -> RuntimeStartResult:
        expected_root = (
            self._managed_root
            / "projects"
            / str(request.project_id)
            / "versions"
            / str(request.version_id)
        ).resolve(strict=False)
        if request.project_root != expected_root:
            raise RuntimeExecutorError(
                "Static Runtime root does not match the managed Version",
                error_code="SCOPE_MISMATCH",
            )
        result = self._call(
            "preview.start_static",
            {
                "preview_id": str(request.preview_id),
                "project_id": str(request.project_id),
                "version_id": str(request.version_id),
                "entry_path": request.entry_path,
            },
        )
        try:
            parsed = RuntimeStartResult(
                executor_handle=_required_string(result, "executor_handle"),
                host=_required_string(result, "host"),
                port=_required_port(result, "port"),
                url=_required_string(result, "url"),
                state=ExecutorRuntimeState(_required_string(result, "state")),
            )
            _validate_worker_identity(parsed.executor_handle, request.preview_id)
            _validate_preview_url_path(parsed.url, request.preview_id)
            return parsed
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeExecutorError("worker returned invalid Runtime metadata") from error

    def probe(self, executor_handle: str) -> RuntimeProbeResult:
        preview_id = _preview_id_from_handle(executor_handle)
        result = self._call("preview.status", {"preview_id": str(preview_id)})
        try:
            state = ExecutorRuntimeState(_required_string(result, "state"))
            host, port, url = _optional_endpoint(result)
            parsed = RuntimeProbeResult(
                executor_handle=_required_string(result, "executor_handle"),
                state=state,
                host=host,
                port=port,
                url=url,
            )
            _validate_worker_identity(parsed.executor_handle, preview_id)
            if parsed.url is not None:
                _validate_preview_url_path(parsed.url, preview_id)
            return parsed
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeExecutorError("worker returned invalid Runtime probe metadata") from error

    def stop(self, executor_handle: str) -> RuntimeStopResult:
        preview_id = _preview_id_from_handle(executor_handle)
        result = self._call("preview.stop", {"preview_id": str(preview_id)})
        try:
            return RuntimeStopResult(stopped=result["stopped"])
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeExecutorError("worker returned invalid Runtime stop metadata") from error

    def _call(self, method: str, params: dict[str, object]) -> dict[str, object]:
        try:
            return self._transport.call(method, params)
        except WorkerRpcError as error:
            raise RuntimeExecutorError(str(error), error_code=error.error_code) from error


def _required_string(result: dict[str, object], key: str) -> str:
    value = result[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _required_port(result: dict[str, object], key: str) -> int:
    value = result[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{key} must be an integer")
    return value


def _optional_endpoint(
    result: dict[str, object],
) -> tuple[str | None, int | None, str | None]:
    values = (result.get("host"), result.get("port"), result.get("url"))
    if values == (None, None, None):
        return None, None, None
    return (
        _required_string(result, "host"),
        _required_port(result, "port"),
        _required_string(result, "url"),
    )


def _preview_id_from_handle(executor_handle: str) -> UUID:
    if not isinstance(executor_handle, str) or not executor_handle.startswith("static:"):
        raise RuntimeExecutorError("Runtime executor handle is invalid")
    try:
        return UUID(executor_handle.removeprefix("static:"))
    except ValueError as error:
        raise RuntimeExecutorError("Runtime executor handle is invalid") from error


def _validate_worker_identity(executor_handle: str, preview_id: UUID) -> None:
    if _preview_id_from_handle(executor_handle) != preview_id:
        raise ValueError("worker rebound the Preview identity")


def _validate_preview_url_path(url: str, preview_id: UUID) -> None:
    if urlsplit(url).path != f"/{preview_id}/":
        raise ValueError("worker returned a URL for another Preview")
