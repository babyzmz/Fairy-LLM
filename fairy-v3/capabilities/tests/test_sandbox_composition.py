from __future__ import annotations

from pathlib import Path

from fairy_core.sandbox.wsl import WslSandboxExecutor

from fairy_capabilities.composition import build_local_sandbox


def test_local_composition_binds_wsl_executor_health_to_local_target(
    tmp_path: Path,
) -> None:
    sandbox = build_local_sandbox(
        {
            "SYSTEMROOT": str(tmp_path),
            "PATH": str(tmp_path),
            "OPENROUTER_API_KEY": "must-not-forward",
        }
    )

    assert isinstance(sandbox.executor, WslSandboxExecutor)
    assert sandbox.health.is_healthy("local") is False
    assert sandbox.health.is_healthy("cloud") is False
