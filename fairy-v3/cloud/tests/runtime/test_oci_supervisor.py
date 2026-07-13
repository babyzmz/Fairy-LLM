from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from uuid import uuid4

from fairy_core.runtime.models import ExecutorRuntimeState

from fairy_cloud.runtime.supervisor import OciDynamicRuntimeSupervisor, ProcessResult

from .test_cloud_runtime import _request


@dataclass(slots=True)
class FakeRunner:
    response: dict[str, object]
    calls: list[dict[str, object]] = field(default_factory=list)

    def run(
        self,
        argv: tuple[str, ...],
        *,
        input_bytes: bytes,
        timeout_seconds: float,
        environment: dict[str, str],
        shell: bool,
    ) -> ProcessResult:
        self.calls.append(
            {
                "argv": argv,
                "input": input_bytes,
                "timeout": timeout_seconds,
                "environment": environment,
                "shell": shell,
            }
        )
        return ProcessResult(0, json.dumps(self.response).encode(), b"")


def test_oci_supervisor_invokes_only_attested_runner_with_scrubbed_environment(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    runner = FakeRunner(_response(request))
    supervisor = OciDynamicRuntimeSupervisor(
        runner=runner,
        runner_path="/usr/local/bin/fairy-runtime-supervisor",
    )

    result = supervisor.start_dynamic(request)

    assert result.state is ExecutorRuntimeState.RUNNING
    assert result.execution_target == "local"
    assert result.host == "127.0.0.1"
    call = runner.calls[0]
    assert call["argv"] == ("/usr/local/bin/fairy-runtime-supervisor", "start")
    assert call["shell"] is False
    assert call["environment"] == {
        "FAIRY_RUNTIME_MODE": "cloud",
        "HOME": "/tmp",
        "LANG": "C.UTF-8",
        "PATH": "/usr/local/bin:/usr/bin:/bin",
    }
    assert request.workspace_archive in bytes(call["input"])


def test_oci_supervisor_accepts_projectless_workspace_scope(tmp_path: Path) -> None:
    request = replace(_request(tmp_path), project_id=None, workspace_id=uuid4())
    runner = FakeRunner(_response(request))
    supervisor = OciDynamicRuntimeSupervisor(
        runner=runner,
        runner_path="/usr/local/bin/fairy-runtime-supervisor",
    )

    supervisor.start_dynamic(request)

    payload = bytes(runner.calls[0]["input"])
    header_length = int.from_bytes(payload[:4], byteorder="big")
    header = json.loads(payload[4 : 4 + header_length])
    assert header["project_id"] is None
    assert header["workspace_id"] == str(request.workspace_id)


def _response(request) -> dict[str, object]:
    return {
        "schema_version": 1,
        "executor": "cloud_oci_runtime",
        "executor_version": "1.0.0",
        "action": "start",
        "project_id": str(request.project_id) if request.project_id is not None else None,
        "workspace_id": str(request.workspace_id),
        "conversation_id": str(request.conversation_id),
        "task_id": str(request.task_id),
        "version_id": str(request.version_id),
        "runtime_id": str(request.runtime_id),
        "preview_id": str(request.preview_id),
        "scope_digest": request.scope_digest,
        "workspace_generation": request.workspace_generation,
        "lease_fence": request.lease_fence,
        "dependency_key": request.dependency_key,
        "executor_handle": f"cloud-dynamic:{request.runtime_id}:{request.lease_fence}",
        "state": "running",
        "host": "127.0.0.1",
        "port": 43125,
        "url": "http://127.0.0.1:43125/",
    }
