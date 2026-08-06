from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from fairy_core.runtime.models import RuntimeExecutorHealth
from fairy_core.sandbox.models import SandboxNetworkPolicy, SandboxRequest, decode_request_frame
from fairy_core.sandbox.wsl import ProcessResult, WslSandboxExecutor


class _Health:
    def __init__(self, available: bool = True) -> None:
        self.available = available

    def health(self) -> RuntimeExecutorHealth:
        return RuntimeExecutorHealth(
            available=self.available,
            executor="wsl_fairy_sandbox",
            version="1.1.0" if self.available else None,
            error_code=None if self.available else "SANDBOX_UNAVAILABLE",
            diagnostics=("fixture",),
        )


class _Runner:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], bytes, dict[str, str], float, bool, int]] = []
        self.response: bytes = b""

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
        self.calls.append((argv, input_bytes, environment, timeout_seconds, shell, creation_flags))
        return ProcessResult(returncode=0, stdout=self.response, stderr=b"")


class _FailingRunner(_Runner):
    def run(self, *args, **kwargs) -> ProcessResult:
        del args, kwargs
        raise OSError("host detail must not escape")


def _request() -> SandboxRequest:
    return SandboxRequest.create(
        job_id=uuid4(),
        project_id=uuid4(),
        conversation_id=uuid4(),
        task_id=uuid4(),
        version_id=uuid4(),
        scope_digest="b" * 64,
        workspace_generation=1,
        lease_fence=1,
        argv=("python", "-V"),
        cwd=".",
        environment={},
        timeout_seconds=30,
        output_limit_bytes=32_768,
        network_policy=SandboxNetworkPolicy.NONE,
        workspace_archive=b"PK\x03\x04fixture",
    )


def _response(request: SandboxRequest, **changes) -> bytes:
    stdout = b"ok\\n"
    stderr = b""
    values = {
        "schema_version": 1,
        "job_id": str(request.job_id),
        "executor": "wsl_fairy_sandbox",
        "executor_version": "1.1.0",
        "scope_digest": request.scope_digest,
        "workspace_generation": request.workspace_generation,
        "lease_fence": request.lease_fence,
        "status": "completed",
        "exit_code": 0,
        "stdout": stdout.decode("utf-8"),
        "stderr": "",
        "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
        "output_truncated": False,
        "started_at": datetime.now(UTC).isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
    }
    values.update(changes)
    return json.dumps(values).encode("utf-8")


def test_wsl_executor_uses_only_the_fixed_runner_and_streams_the_archive(
    tmp_path: Path,
) -> None:
    request = _request()
    runner = _Runner()
    runner.response = _response(request)
    wsl = tmp_path / "wsl.exe"
    wsl.write_bytes(b"fixture")
    executor = WslSandboxExecutor(
        runner=runner,
        health_probe=_Health(),
        wsl_executable=wsl,
        host_environment={"SYSTEMROOT": "C:/Windows", "SECRET": "not-forwarded"},
    )

    result = executor.execute(request)

    argv, payload, environment, timeout, shell, _flags = runner.calls[0]
    assert argv == (
        str(wsl),
        "--distribution",
        "FairySandbox",
        "--user",
        "fairy",
        "--exec",
        "/usr/local/bin/fairy-sandbox-runner",
    )
    decoded, archive = decode_request_frame(payload)
    assert decoded["scope_digest"] == request.scope_digest
    assert decoded["argv"] == ["python", "-V"]
    assert archive == request.workspace_archive
    assert "SECRET" not in environment
    assert timeout > request.timeout_seconds
    assert shell is False
    assert result.stdout == b"ok\\n"


def test_wsl_executor_preserves_binary_output_and_verifies_runner_hashes(
    tmp_path: Path,
) -> None:
    request = _request()
    stdout = b"binary:\xff\x00\n"
    stderr = b"warning:\xfe"
    runner = _Runner()
    runner.response = _response(
        request,
        stdout_base64=base64.b64encode(stdout).decode("ascii"),
        stderr_base64=base64.b64encode(stderr).decode("ascii"),
        stdout_sha256=hashlib.sha256(stdout).hexdigest(),
        stderr_sha256=hashlib.sha256(stderr).hexdigest(),
    )
    wsl = tmp_path / "wsl.exe"
    wsl.write_bytes(b"fixture")
    executor = WslSandboxExecutor(
        runner=runner,
        health_probe=_Health(),
        wsl_executable=wsl,
        host_environment={},
    )

    result = executor.execute(request)

    assert result.stdout == stdout
    assert result.stderr == stderr


def test_wsl_executor_rejects_a_result_output_hash_mismatch(tmp_path: Path) -> None:
    request = _request()
    runner = _Runner()
    runner.response = _response(request, stdout_sha256="f" * 64)
    wsl = tmp_path / "wsl.exe"
    wsl.write_bytes(b"fixture")
    executor = WslSandboxExecutor(
        runner=runner,
        health_probe=_Health(),
        wsl_executable=wsl,
        host_environment={},
    )

    with pytest.raises(ValueError, match="stdout hash"):
        executor.execute(request)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"executor": "forged"}, "executor"),
        ({"scope_digest": "c" * 64}, "Scope"),
        ({"workspace_generation": 2}, "generation"),
        ({"lease_fence": 2}, "fence"),
        ({"exit_code": "0"}, "schema"),
        ({"output_truncated": "false"}, "schema"),
    ],
)
def test_wsl_executor_rejects_mismatched_runner_results(
    tmp_path: Path,
    changes,
    message: str,
) -> None:
    request = _request()
    runner = _Runner()
    runner.response = _response(request, **changes)
    wsl = tmp_path / "wsl.exe"
    wsl.write_bytes(b"fixture")
    executor = WslSandboxExecutor(
        runner=runner,
        health_probe=_Health(),
        wsl_executable=wsl,
        host_environment={},
    )

    with pytest.raises(ValueError, match=message):
        executor.execute(request)


def test_wsl_executor_fails_closed_when_attestation_is_unavailable(tmp_path: Path) -> None:
    runner = _Runner()
    wsl = tmp_path / "wsl.exe"
    wsl.write_bytes(b"fixture")
    executor = WslSandboxExecutor(
        runner=runner,
        health_probe=_Health(available=False),
        wsl_executable=wsl,
        host_environment={},
    )

    with pytest.raises(RuntimeError, match="SANDBOX_UNAVAILABLE"):
        executor.execute(_request())
    assert runner.calls == []


def test_wsl_executor_cancels_only_through_the_fixed_runner(tmp_path: Path) -> None:
    runner = _Runner()
    wsl = tmp_path / "wsl.exe"
    wsl.write_bytes(b"fixture")
    executor = WslSandboxExecutor(
        runner=runner,
        health_probe=_Health(),
        wsl_executable=wsl,
        host_environment={},
    )
    job_id = uuid4()

    executor.cancel(job_id)

    argv, payload, _environment, timeout, shell, _flags = runner.calls[0]
    assert argv == (
        str(wsl),
        "--distribution",
        "FairySandbox",
        "--user",
        "fairy",
        "--exec",
        "/usr/local/bin/fairy-sandbox-runner",
        "--cancel",
        str(job_id),
    )
    assert payload == b""
    assert timeout == 10
    assert shell is False


def test_wsl_executor_can_cancel_an_attested_job_after_health_degrades(
    tmp_path: Path,
) -> None:
    runner = _Runner()
    wsl = tmp_path / "wsl.exe"
    wsl.write_bytes(b"fixture")
    executor = WslSandboxExecutor(
        runner=runner,
        health_probe=_Health(available=False),
        wsl_executable=wsl,
        host_environment={},
    )

    executor.cancel(uuid4())

    assert len(runner.calls) == 1


def test_wsl_executor_maps_host_process_failures_to_stable_unavailable_error(
    tmp_path: Path,
) -> None:
    wsl = tmp_path / "wsl.exe"
    wsl.write_bytes(b"fixture")
    executor = WslSandboxExecutor(
        runner=_FailingRunner(),
        health_probe=_Health(),
        wsl_executable=wsl,
        host_environment={},
    )

    with pytest.raises(RuntimeError) as captured:
        executor.execute(_request())

    assert captured.value.error_code == "SANDBOX_UNAVAILABLE"
    assert "host detail" not in str(captured.value)
