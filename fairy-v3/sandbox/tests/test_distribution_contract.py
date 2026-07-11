from __future__ import annotations

import configparser
from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_wsl_configuration_disables_host_mounts_and_interoperability() -> None:
    config = configparser.ConfigParser()
    config.optionxform = str
    loaded = config.read(
        ROOT / "sandbox" / "wsl" / "etc" / "wsl.conf", encoding="utf-8"
    )

    assert loaded
    assert config["automount"] == {"enabled": "false", "mountFsTab": "false"}
    assert config["interop"] == {"enabled": "false", "appendWindowsPath": "false"}
    assert config["user"] == {"default": "fairy"}


def test_installer_imports_a_verified_wsl2_rootfs_without_host_path_mounts() -> None:
    source = (ROOT / "sandbox" / "wsl" / "install.ps1").read_text(encoding="utf-8")

    assert "RootfsSha256" in source
    assert "Get-FileHash" in source
    assert '"--import"' in source
    assert '"FairySandbox"' in source
    assert '"--version"' in source
    assert '"2"' in source
    assert "bubblewrap" in source
    assert "fairy-sandbox-runner" in source
    assert "fairy-sandbox-health" in source
    assert "RedirectStandardInput" in source
    assert '"/usr/local/lib", "/usr/local/bin"' in source
    assert "--unregister" not in source
    assert "/mnt/" not in source


def test_release_gate_runs_runner_tests_and_requires_real_execution_with_wsl() -> None:
    source = (ROOT / "scripts" / "test-all.ps1").read_text(encoding="utf-8")

    assert "Sandbox runner: pytest" in source
    assert "verify_wsl_sandbox.py" in source
    required_gate = source[source.index("if ($RequireWslSandbox)") :]
    assert "verify_wsl_sandbox.py" in required_gate
