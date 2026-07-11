from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from fairy_core.runtime.wsl_health import (
    ProcessResult,
    WslSandboxHealthProbe,
)


class FakeRunner:
    def __init__(self, responses: list[ProcessResult | BaseException]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def run(
        self,
        argv: tuple[str, ...],
        *,
        timeout_seconds: float,
        environment: dict[str, str],
        shell: bool,
        creation_flags: int,
    ) -> ProcessResult:
        self.calls.append(
            {
                "argv": argv,
                "timeout_seconds": timeout_seconds,
                "environment": environment,
                "shell": shell,
                "creation_flags": creation_flags,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _result(stdout: str = "", *, returncode: int = 0, utf16: bool = False) -> ProcessResult:
    encoded = ("\ufeff" + stdout).encode("utf-16-le") if utf16 else stdout.encode()
    return ProcessResult(returncode=returncode, stdout=encoded, stderr=b"")


def _attestation(**overrides: object) -> str:
    values: dict[str, object] = {
        "schema_version": 1,
        "executor": "wsl_fairy_sandbox",
        "runner_version": "1.0.0",
        "user": "fairy",
        "uid": 1000,
        "default_user": "fairy",
        "config": {
            "automount.enabled": False,
            "automount.mountFsTab": False,
            "interop.enabled": False,
            "interop.appendWindowsPath": False,
        },
        "runner_sha256": "a" * 64,
        "config_sha256": "b" * 64,
        "bwrap_path": "/usr/bin/bwrap",
        "bwrap_sha256": "c" * 64,
        "files": {
            "runner": {"uid": 0, "mode": 0o755},
            "config": {"uid": 0, "mode": 0o644},
            "bwrap": {"uid": 0, "mode": 0o755},
        },
    }
    values.update(overrides)
    values["attestation_digest"] = hashlib.sha256(
        json.dumps(
            values,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return json.dumps(values)


def _probe(tmp_path: Path, runner: FakeRunner) -> WslSandboxHealthProbe:
    executable = tmp_path / "wsl.exe"
    executable.write_bytes(b"")
    return WslSandboxHealthProbe(
        runner=runner,
        wsl_executable=executable,
        expected_runner_version="1.0.0",
        host_environment={
            "SYSTEMROOT": "C:\\Windows",
            "PATH": "C:\\Windows\\System32",
            "FAIRY_SECRET": "must-not-leak",
        },
    )


def test_wsl_probe_fails_closed_when_wsl_executable_is_missing(tmp_path: Path) -> None:
    runner = FakeRunner([])
    probe = WslSandboxHealthProbe(
        runner=runner,
        wsl_executable=tmp_path / "missing-wsl.exe",
        expected_runner_version="1.0.0",
        host_environment={},
    )

    health = probe.health()

    assert health.available is False
    assert health.error_code == "SANDBOX_UNAVAILABLE"
    assert runner.calls == []


@pytest.mark.parametrize(
    ("list_output", "attestation", "diagnostic"),
    (
        ("Ubuntu Running 2\n", _attestation(), "distribution is not installed"),
        ("FairySandbox Running 1\n", _attestation(), "WSL 2"),
        (
            "FairySandbox Running 2\n",
            _attestation(default_user="root", uid=0),
            "non-root",
        ),
        (
            "FairySandbox Running 2\n",
            _attestation(runner_version="0.9.0"),
            "runner version",
        ),
        (
            "FairySandbox Running 2\n",
            _attestation(config={"automount.enabled": False}),
            "wsl.conf",
        ),
        (
            "FairySandbox Running 2\n",
            _attestation(executor="forged"),
            "executor identity",
        ),
        (
            "FairySandbox Running 2\n",
            _attestation(bwrap_path="/tmp/bwrap"),
            "bubblewrap",
        ),
        (
            "FairySandbox Running 2\n",
            _attestation(
                files={
                    "runner": {"uid": 1000, "mode": 0o775},
                    "config": {"uid": 0, "mode": 0o644},
                    "bwrap": {"uid": 0, "mode": 0o755},
                }
            ),
            "root-owned",
        ),
    ),
)
def test_wsl_probe_rejects_incomplete_attestation(
    tmp_path: Path,
    list_output: str,
    attestation: str,
    diagnostic: str,
) -> None:
    runner = FakeRunner(
        [
            _result("Default Version: 2"),
            _result(list_output, utf16=True),
            _result(attestation),
        ]
    )
    probe = _probe(tmp_path, runner)

    health = probe.health()

    assert health.available is False
    assert health.error_code == "SANDBOX_UNAVAILABLE"
    assert any(diagnostic in item for item in health.diagnostics)


def test_wsl_probe_rejects_failed_status_without_mutating_system(tmp_path: Path) -> None:
    runner = FakeRunner([_result(returncode=1)])
    health = _probe(tmp_path, runner).health()

    assert health.available is False
    assert health.error_code == "SANDBOX_UNAVAILABLE"
    assert len(runner.calls) == 1
    assert runner.calls[0]["argv"][-1] == "--status"


def test_wsl_probe_accepts_only_full_wsl2_fairysandbox_attestation(
    tmp_path: Path,
) -> None:
    runner = FakeRunner(
        [
            _result("Default Version: 2"),
            _result(
                "  NAME            STATE           VERSION\n* FairySandbox    Running         2\n",
                utf16=True,
            ),
            _result(_attestation()),
        ]
    )
    health = _probe(tmp_path, runner).health()

    assert health.available is True
    assert health.executor == "wsl_fairy_sandbox"
    assert health.version == "1.0.0"
    assert health.error_code is None
    assert len(runner.calls) == 3
    assert all(call["shell"] is False for call in runner.calls)
    assert all("FAIRY_SECRET" not in call["environment"] for call in runner.calls)
    assert [tuple(call["argv"])[1:] for call in runner.calls] == [
        ("--status",),
        ("--list", "--verbose"),
        (
            "--distribution",
            "FairySandbox",
            "--user",
            "fairy",
            "--exec",
            "/usr/local/bin/fairy-sandbox-health",
            "--json",
        ),
    ]
    forbidden = {"--install", "--import", "--unregister", "--set-default-version"}
    assert all(not (forbidden & set(call["argv"])) for call in runner.calls)


def test_wsl_probe_rejects_an_attestation_changed_after_signing(tmp_path: Path) -> None:
    values = json.loads(_attestation())
    values["runner_sha256"] = "d" * 64
    runner = FakeRunner(
        [
            _result("Default Version: 2"),
            _result("FairySandbox Running 2\n", utf16=True),
            _result(json.dumps(values)),
        ]
    )

    health = _probe(tmp_path, runner).health()

    assert health.available is False
    assert any("digest" in item for item in health.diagnostics)
