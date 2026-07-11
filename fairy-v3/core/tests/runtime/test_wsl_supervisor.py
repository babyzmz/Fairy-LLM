from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

import pytest

from fairy_core.domain.execution import RuntimeKind
from fairy_core.runtime.models import (
    DynamicRuntimeStart,
    ExecutorRuntimeState,
    RuntimeExecutorError,
    RuntimeExecutorHealth,
)
from fairy_core.runtime.supervisor import (
    ProcessResult,
    RoutedRuntimeExecutor,
    WslDynamicRuntimeExecutor,
    WslRuntimeHealthProbe,
)
from tests.runtime_support import FakeRuntimeExecutor


@dataclass(slots=True)
class FakeHealth:
    available: bool = True

    def health(self) -> RuntimeExecutorHealth:
        return RuntimeExecutorHealth(
            available=self.available,
            executor="wsl_fairy_runtime",
            version="1.0.0" if self.available else None,
            error_code=None if self.available else "SANDBOX_UNAVAILABLE",
            diagnostics=("fixture",),
        )


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.responses: list[ProcessResult] = []

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
        self.calls.append(
            {
                "argv": argv,
                "input": input_bytes,
                "timeout": timeout_seconds,
                "environment": environment,
                "shell": shell,
                "creation_flags": creation_flags,
            }
        )
        return self.responses.pop(0)


def test_wsl_dynamic_start_uses_fixed_exec_and_binds_attested_response(tmp_path: Path) -> None:
    runner = FakeRunner()
    request = _request(tmp_path)
    runner.responses.append(_response(_start_payload(request)))
    executor = _executor(tmp_path, runner)

    result = executor.start_dynamic(request)

    assert result.state is ExecutorRuntimeState.RUNNING
    assert result.execution_target == "local"
    call = runner.calls[0]
    assert call["argv"] == (
        str(tmp_path / "wsl.exe"),
        "--distribution",
        "FairySandbox",
        "--user",
        "fairy",
        "--exec",
        "/usr/local/bin/fairy-runtime-supervisor",
        "start",
    )
    assert call["shell"] is False
    assert call["environment"] == {"SYSTEMROOT": str(tmp_path)}
    header, archive = _decode_frame(call["input"])
    assert archive == request.workspace_archive
    assert header["scope_digest"] == request.scope_digest
    assert header["workspace_generation"] == request.workspace_generation
    assert header["lease_fence"] == request.lease_fence
    assert header["argv"] == list(request.argv)
    assert "environment" not in header
    assert "host" not in header
    assert "port" not in header
    assert "url" not in header


def test_wsl_dynamic_probe_and_stop_use_only_bound_handle(tmp_path: Path) -> None:
    runner = FakeRunner()
    request = _request(tmp_path)
    payload = _start_payload(request)
    handle = str(payload["executor_handle"])
    runner.responses.extend(
        (
            _response({**payload, "action": "probe"}),
            _response(
                {
                    "schema_version": 1,
                    "executor": "wsl_fairy_runtime",
                    "executor_version": "1.0.0",
                    "action": "stop",
                    "executor_handle": handle,
                    "runtime_id": str(request.runtime_id),
                    "preview_id": str(request.preview_id),
                    "lease_fence": request.lease_fence,
                    "stopped": True,
                }
            ),
        )
    )
    executor = _executor(tmp_path, runner)

    probe = executor.probe(handle)
    stopped = executor.stop(handle)

    assert probe.state is ExecutorRuntimeState.RUNNING
    assert stopped.stopped is True
    for call, action in zip(runner.calls, ("probe", "stop"), strict=True):
        assert call["argv"][-1] == action
        assert json.loads(bytes(call["input"]).decode("utf-8")) == {
            "schema_version": 1,
            "executor_handle": handle,
        }


def test_wsl_dynamic_rejects_response_scope_rebinding(tmp_path: Path) -> None:
    runner = FakeRunner()
    request = _request(tmp_path)
    runner.responses.append(_response({**_start_payload(request), "scope_digest": "0" * 64}))
    executor = _executor(tmp_path, runner)

    with pytest.raises(RuntimeExecutorError) as captured:
        executor.start_dynamic(request)

    assert captured.value.error_code == "SCOPE_MISMATCH"


def test_wsl_dynamic_fails_closed_without_runtime_attestation(tmp_path: Path) -> None:
    runner = FakeRunner()
    executor = _executor(tmp_path, runner, available=False)

    with pytest.raises(RuntimeExecutorError) as captured:
        executor.start_dynamic(_request(tmp_path))

    assert captured.value.error_code == "SANDBOX_UNAVAILABLE"
    assert runner.calls == []


def test_wsl_runtime_health_requires_disabled_mounts_and_interop(tmp_path: Path) -> None:
    runner = FakeRunner()
    executable = tmp_path / "wsl.exe"
    executable.touch()
    base = {
        "schema_version": 1,
        "executor": "wsl_fairy_runtime",
        "runner_version": "1.0.0",
        "user": "fairy",
        "uid": 1000,
        "sandbox_root": "/var/lib/fairy-sandbox",
        "toolchain": {
            "node": "v24.18.0",
            "npm": "11.16.0",
            "pnpm": "10.34.4",
            "yarn": "1.22.22",
            "uv": "uv 0.11.28",
        },
        "config": {
            "automount.enabled": False,
            "automount.mountFsTab": False,
            "interop.enabled": False,
            "interop.appendWindowsPath": False,
        },
    }
    runner.responses.extend(
        (
            _response(base),
            _response(
                {
                    **base,
                    "config": {**base["config"], "interop.enabled": True},
                }
            ),
        )
    )
    probe = WslRuntimeHealthProbe(
        runner=runner,
        wsl_executable=executable,
        host_environment={"SYSTEMROOT": str(tmp_path)},
    )

    healthy = probe.health()
    unsafe = probe.health()

    assert healthy.available is True
    assert unsafe.available is False
    assert unsafe.error_code == "SANDBOX_UNAVAILABLE"


def test_routed_executor_selects_kind_and_handle_without_cross_binding(tmp_path: Path) -> None:
    static = FakeRuntimeExecutor()
    local = WslDynamicRuntimeExecutor(
        runner=FakeRunner(),
        health_probe=FakeHealth(False),
        wsl_executable=tmp_path / "missing-wsl.exe",
        host_environment={},
    )
    routed = RoutedRuntimeExecutor(static=static, local_dynamic=local)

    assert routed.health_for(RuntimeKind.STATIC_SITE).executor == "fake_runtime"
    assert routed.health_for(RuntimeKind.WSL_PROJECT).available is False
    with pytest.raises(RuntimeExecutorError, match="Cloud") as captured:
        routed.start_dynamic(
            replace(
                _request(tmp_path),
                execution_target="cloud",
                kind=RuntimeKind.CLOUD_OCI,
            )
        )
    assert captured.value.error_code == "SANDBOX_UNAVAILABLE"


def _executor(
    tmp_path: Path,
    runner: FakeRunner,
    *,
    available: bool = True,
) -> WslDynamicRuntimeExecutor:
    executable = tmp_path / "wsl.exe"
    executable.touch()
    return WslDynamicRuntimeExecutor(
        runner=runner,
        health_probe=FakeHealth(available),
        wsl_executable=executable,
        host_environment={"SYSTEMROOT": str(tmp_path), "OPENROUTER_API_KEY": "secret"},
    )


def _request(tmp_path: Path) -> DynamicRuntimeStart:
    archive = b"PK\x03\x04runtime-fixture"
    return DynamicRuntimeStart(
        project_id=uuid4(),
        conversation_id=uuid4(),
        task_id=uuid4(),
        version_id=uuid4(),
        runtime_id=uuid4(),
        preview_id=uuid4(),
        project_root=tmp_path,
        execution_target="local",
        kind=RuntimeKind.WSL_PROJECT,
        adapter="vite",
        scope_digest="a" * 64,
        workspace_generation=4,
        lease_fence=7,
        argv=(
            "node_modules/.bin/vite",
            "--host",
            "127.0.0.1",
            "--port",
            "{port}",
            "--strictPort",
        ),
        cwd=".",
        readiness_path="/health",
        startup_timeout_seconds=45,
        dependency_key="b" * 64,
        workspace_archive=archive,
        archive_sha256=hashlib.sha256(archive).hexdigest(),
    )


def _start_payload(request: DynamicRuntimeStart) -> dict[str, object]:
    return {
        "schema_version": 1,
        "executor": "wsl_fairy_runtime",
        "executor_version": "1.0.0",
        "action": "start",
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
        "executor_handle": f"wsl-dynamic:{request.runtime_id}:{request.lease_fence}",
        "state": "running",
        "host": "127.0.0.1",
        "port": 43126,
        "url": "http://127.0.0.1:43126/",
    }


def _response(values: dict[str, object]) -> ProcessResult:
    return ProcessResult(
        returncode=0,
        stdout=json.dumps(values).encode("utf-8"),
        stderr=b"",
    )


def _decode_frame(value: object) -> tuple[dict[str, object], bytes]:
    assert isinstance(value, bytes)
    header_length = struct.unpack(">I", value[:4])[0]
    header = json.loads(value[4 : 4 + header_length].decode("utf-8"))
    assert isinstance(header, dict)
    return header, value[4 + header_length :]
