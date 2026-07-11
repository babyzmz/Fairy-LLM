from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from fairy_core.sandbox.models import (
    SandboxNetworkPolicy,
    SandboxRequest,
    SandboxResult,
    SandboxResultStatus,
)


def _request(**changes) -> SandboxRequest:
    values = {
        "job_id": uuid4(),
        "project_id": uuid4(),
        "conversation_id": uuid4(),
        "task_id": uuid4(),
        "version_id": uuid4(),
        "scope_digest": "a" * 64,
        "workspace_generation": 3,
        "lease_fence": 2,
        "argv": ("python", "-m", "pytest", "-q"),
        "cwd": "src",
        "environment": {"CI": "1"},
        "timeout_seconds": 60,
        "output_limit_bytes": 65_536,
        "network_policy": SandboxNetworkPolicy.NONE,
        "workspace_archive": b"PK\x03\x04fixture",
    }
    values.update(changes)
    return SandboxRequest.create(**values)


def test_sandbox_request_is_canonical_and_scope_bound() -> None:
    request = _request()

    assert request.argv == ("python", "-m", "pytest", "-q")
    assert request.environment == {"CI": "1"}
    assert request.archive_sha256
    assert request.workspace_generation == 3
    assert request.lease_fence == 2


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"argv": ()}, "argv"),
        ({"argv": ("python\x00.exe",)}, "NUL"),
        ({"cwd": "../escape"}, "cwd"),
        ({"environment": {"PATH": "/forged"}}, "environment"),
        ({"environment": {"LD_PRELOAD": "/tmp/x"}}, "environment"),
        ({"timeout_seconds": 0}, "timeout"),
        ({"output_limit_bytes": 2_000_000}, "output"),
        ({"workspace_generation": 0}, "generation"),
        ({"lease_fence": 0}, "fence"),
        ({"scope_digest": "A" * 64}, "scope_digest"),
        ({"network_policy": "host"}, "network_policy"),
    ],
)
def test_sandbox_request_rejects_unsafe_or_unbounded_fields(changes, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _request(**changes)


def test_sandbox_result_requires_matching_hashes_and_timing() -> None:
    request = _request()
    started = datetime.now(UTC)
    result = SandboxResult.create(
        request=request,
        executor="wsl_fairy_sandbox",
        executor_version="1.0.0",
        status=SandboxResultStatus.COMPLETED,
        exit_code=0,
        stdout=b"ok\n",
        stderr=b"",
        output_truncated=False,
        started_at=started,
        finished_at=started + timedelta(milliseconds=10),
    )

    assert result.job_id == request.job_id
    assert result.scope_digest == request.scope_digest
    assert result.stdout_sha256
    assert result.stderr_sha256

    with pytest.raises(ValueError, match="finished_at"):
        SandboxResult.create(
            request=request,
            executor="wsl_fairy_sandbox",
            executor_version="1.0.0",
            status=SandboxResultStatus.COMPLETED,
            exit_code=0,
            stdout=b"",
            stderr=b"",
            output_truncated=False,
            started_at=started,
            finished_at=started - timedelta(seconds=1),
        )


@pytest.mark.parametrize(
    ("status", "exit_code"),
    (
        (SandboxResultStatus.COMPLETED, 1),
        (SandboxResultStatus.TIMED_OUT, 1),
        (SandboxResultStatus.CANCELLED, 1),
    ),
)
def test_sandbox_result_rejects_inconsistent_status_and_exit_code(
    status: SandboxResultStatus,
    exit_code: int,
) -> None:
    request = _request()
    now = datetime.now(UTC)

    with pytest.raises(ValueError, match="exit code"):
        SandboxResult.create(
            request=request,
            executor="wsl_fairy_sandbox",
            executor_version="1.0.0",
            status=status,
            exit_code=exit_code,
            stdout=b"",
            stderr=b"",
            output_truncated=False,
            started_at=now,
            finished_at=now,
        )


def test_sandbox_result_requires_a_real_boolean_truncation_flag() -> None:
    request = _request()
    now = datetime.now(UTC)

    with pytest.raises(ValueError, match="output_truncated"):
        SandboxResult.create(
            request=request,
            executor="wsl_fairy_sandbox",
            executor_version="1.0.0",
            status=SandboxResultStatus.COMPLETED,
            exit_code=0,
            stdout=b"",
            stderr=b"",
            output_truncated=1,  # type: ignore[arg-type]
            started_at=now,
            finished_at=now,
        )
