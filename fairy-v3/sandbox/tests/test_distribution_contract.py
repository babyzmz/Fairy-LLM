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
    assert "fairy-runtime-supervisor" in source
    assert "fairy_runtime_supervisor.py" in source
    assert "fairy_install_toolchain.sh" in source
    assert '"curl", "gnupg"' in source
    assert '"/var/lib/fairy-sandbox/dependencies"' in source
    assert '"/var/lib/fairy-sandbox/runtimes"' in source
    assert "function Test-WslUser" in source
    assert '$ErrorActionPreference = "Continue"' in source
    assert 'if (-not (Test-WslUser -User "fairy"))' in source
    assert (
        source.count('$HealthDocument.toolchain.uv -notmatch "^uv 0\\.11\\.28(?: |$)"')
        == 1
    )
    assert (
        source.count(
            '$RuntimeHealthDocument.toolchain.uv -notmatch "^uv 0\\.11\\.28(?: |$)"'
        )
        == 1
    )
    assert "RedirectStandardInput" in source
    assert '"/usr/local/lib", "/usr/local/bin"' in source
    assert "--unregister" not in source
    assert "/mnt/" not in source


def test_wsl_toolchain_is_version_pinned_and_signature_verified() -> None:
    source = (ROOT / "sandbox" / "wsl" / "install-toolchain.sh").read_text(
        encoding="utf-8"
    )

    assert 'NODE_VERSION="24.18.0"' in source
    assert 'PNPM_VERSION="10.34.4"' in source
    assert 'YARN_VERSION="1.22.22"' in source
    assert 'UV_VERSION="0.11.28"' in source
    assert "uv --version | cut -d ' ' -f 1-2" in source
    assert "SHASUMS256.txt.sig" in source
    assert "gpg --batch --verify" in source
    assert "sha256sum --check --strict" in source


def test_release_gate_runs_runner_tests_and_requires_real_execution_with_wsl() -> None:
    source = (ROOT / "scripts" / "test-all.ps1").read_text(encoding="utf-8")

    assert "Sandbox runner: pytest" in source
    assert "verify_wsl_sandbox.py" in source
    required_gate = source[source.index("if ($RequireWslSandbox)") :]
    assert "verify_wsl_sandbox.py" in required_gate
    assert "verify_wsl_scratch_runtime.py" in required_gate
    assert "json.dumps(dict(" in required_gate
    assert "available=health.available" in required_gate
