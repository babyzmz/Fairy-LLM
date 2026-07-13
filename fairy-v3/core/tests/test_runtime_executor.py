from __future__ import annotations

import os
from pathlib import Path

import pytest

from fairy_core.domain.ids import new_id
from fairy_core.runtime.models import (
    ExecutorRuntimeState,
    RuntimeExecutorError,
    StaticRuntimeStart,
)
from fairy_core.runtime.rust_worker import RustRuntimeExecutor
from fairy_core.workspace.worker_transport import WorkerRpcError


class FakeWorkerTransport:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, object]]] = []

    def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
        self.calls.append((method, params))
        response = self.responses[method]
        if isinstance(response, Exception):
            raise response
        assert isinstance(response, dict)
        return response


def _request(managed_root: Path) -> StaticRuntimeStart:
    project_id = new_id()
    version_id = new_id()
    return StaticRuntimeStart(
        project_id=project_id,
        version_id=version_id,
        preview_id=new_id(),
        project_root=(managed_root / "projects" / str(project_id) / "versions" / str(version_id)),
        entry_path="index.html",
    )


def test_rust_runtime_executor_maps_only_fixed_worker_methods(tmp_path: Path) -> None:
    managed_root = tmp_path / "managed"
    request = _request(managed_root)
    handle = f"static:{request.preview_id}"
    url = f"http://127.0.0.1:43125/{request.preview_id}/"
    transport = FakeWorkerTransport(
        {
            "preview.start_static": {
                "executor_handle": handle,
                "host": "127.0.0.1",
                "port": 43125,
                "url": url,
                "state": "running",
            },
            "preview.status": {
                "executor_handle": handle,
                "host": "127.0.0.1",
                "port": 43125,
                "url": url,
                "state": "running",
            },
            "preview.stop": {"stopped": True},
        }
    )
    executor = RustRuntimeExecutor(
        transport,
        managed_root=managed_root,
        worker_version="0.1.0",
    )

    health = executor.health()
    started = executor.start_static(request)
    probed = executor.probe(handle)
    stopped = executor.stop(handle)

    assert health.available is True
    assert health.executor == "rust_local_worker"
    assert health.version == "0.1.0"
    assert started.executor_handle == handle
    assert started.state is ExecutorRuntimeState.RUNNING
    assert probed.state is ExecutorRuntimeState.RUNNING
    assert stopped.stopped is True
    assert transport.calls == [
        (
            "preview.start_static",
            {
                "preview_id": str(request.preview_id),
                "workspace_id": str(request.workspace_id),
                "version_id": str(request.version_id),
                "entry_path": "index.html",
            },
        ),
        ("preview.status", {"preview_id": str(request.preview_id)}),
        ("preview.stop", {"preview_id": str(request.preview_id)}),
    ]


@pytest.mark.parametrize(
    "response",
    (
        {
            "executor_handle": "static:not-a-uuid",
            "host": "127.0.0.1",
            "port": 43125,
            "url": "http://127.0.0.1:43125/preview/",
            "state": "running",
        },
        {
            "executor_handle": "placeholder",
            "host": "0.0.0.0",
            "port": 43125,
            "url": "http://0.0.0.0:43125/preview/",
            "state": "running",
        },
        {
            "executor_handle": "placeholder",
            "host": "127.0.0.1",
            "port": 0,
            "url": "http://127.0.0.1:0/preview/",
            "state": "running",
        },
        {
            "executor_handle": "placeholder",
            "host": "127.0.0.1",
            "port": 43125,
            "url": "http://localhost:43125/preview/",
            "state": "running",
        },
    ),
)
def test_rust_runtime_executor_rejects_malformed_worker_results(
    tmp_path: Path,
    response: dict[str, object],
) -> None:
    managed_root = tmp_path / "managed"
    request = _request(managed_root)
    response = dict(response)
    if response["executor_handle"] == "placeholder":
        response["executor_handle"] = f"static:{request.preview_id}"
    transport = FakeWorkerTransport({"preview.start_static": response})
    executor = RustRuntimeExecutor(transport, managed_root=managed_root)

    with pytest.raises(RuntimeExecutorError) as captured:
        executor.start_static(request)

    assert captured.value.error_code == "WORKER_INTERRUPTED"


def test_rust_runtime_executor_rejects_rebound_root_without_dispatch(tmp_path: Path) -> None:
    managed_root = tmp_path / "managed"
    request = _request(managed_root)
    rebound = StaticRuntimeStart(
        project_id=request.project_id,
        version_id=request.version_id,
        preview_id=request.preview_id,
        project_root=tmp_path / "outside",
        entry_path="index.html",
    )
    transport = FakeWorkerTransport({})
    executor = RustRuntimeExecutor(transport, managed_root=managed_root)

    with pytest.raises(RuntimeExecutorError) as captured:
        executor.start_static(rebound)

    assert captured.value.error_code == "SCOPE_MISMATCH"
    assert transport.calls == []


@pytest.mark.skipif(os.name != "nt", reason="Windows extended path syntax")
def test_rust_runtime_executor_accepts_the_same_managed_root_with_extended_prefix(
    tmp_path: Path,
) -> None:
    managed_root = tmp_path / "managed"
    request = _request(managed_root)
    extended = StaticRuntimeStart(
        project_id=request.project_id,
        version_id=request.version_id,
        preview_id=request.preview_id,
        project_root=Path("\\\\?\\" + str(request.project_root.resolve(strict=False))),
        entry_path="index.html",
    )
    transport = FakeWorkerTransport(
        {
            "preview.start_static": {
                "executor_handle": f"static:{request.preview_id}",
                "host": "127.0.0.1",
                "port": 43125,
                "url": f"http://127.0.0.1:43125/{request.preview_id}/",
                "state": "running",
            }
        }
    )

    RustRuntimeExecutor(transport, managed_root=managed_root).start_static(extended)

    assert [call[0] for call in transport.calls] == ["preview.start_static"]


def test_rust_runtime_executor_preserves_typed_worker_failure(tmp_path: Path) -> None:
    managed_root = tmp_path / "managed"
    request = _request(managed_root)
    transport = FakeWorkerTransport(
        {
            "preview.start_static": WorkerRpcError(
                "blocked",
                error_code="PATH_OUT_OF_SCOPE",
            )
        }
    )
    executor = RustRuntimeExecutor(transport, managed_root=managed_root)

    with pytest.raises(RuntimeExecutorError) as captured:
        executor.start_static(request)

    assert captured.value.error_code == "PATH_OUT_OF_SCOPE"


@pytest.mark.parametrize(
    "values",
    (
        {"entry_path": "../index.html"},
        {"entry_path": "app.html"},
    ),
)
def test_static_runtime_request_requires_fixed_entry_path(
    tmp_path: Path,
    values: dict[str, object],
) -> None:
    request = _request(tmp_path / "managed")

    with pytest.raises(ValueError):
        StaticRuntimeStart(
            project_id=request.project_id,
            version_id=request.version_id,
            preview_id=request.preview_id,
            project_root=request.project_root,
            **values,
        )
