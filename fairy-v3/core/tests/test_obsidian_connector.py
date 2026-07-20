from pathlib import Path

from fairy_core.obsidian import ObsidianConnector


def test_obsidian_health_distinguishes_desktop_and_cli(tmp_path: Path) -> None:
    local_app_data = tmp_path / "local"
    desktop = local_app_data / "Programs" / "Obsidian" / "Obsidian.exe"
    desktop.parent.mkdir(parents=True)
    desktop.write_bytes(b"placeholder")
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()

    health = ObsidianConnector(
        {
            "LOCALAPPDATA": str(local_app_data),
            "PROGRAMFILES": str(tmp_path),
            "PATH": str(empty_path),
        }
    ).health()

    assert health.desktop_installed
    assert not health.cli_available
    assert health.status == "cli_disabled"


def test_obsidian_health_reports_missing_installation(tmp_path: Path) -> None:
    health = ObsidianConnector(
        {"LOCALAPPDATA": str(tmp_path), "PROGRAMFILES": str(tmp_path), "PATH": str(tmp_path)}
    ).health()

    assert not health.desktop_installed
    assert not health.cli_available
    assert health.status == "not_installed"
