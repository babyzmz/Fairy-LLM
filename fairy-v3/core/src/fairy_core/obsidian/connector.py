from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from pathlib import Path

from fairy_core.contracts.obsidian import ObsidianConnectorHealthModel


class ObsidianConnector:
    def __init__(self, environment: Mapping[str, str] | None = None) -> None:
        self._environment = dict(os.environ if environment is None else environment)

    def health(self) -> ObsidianConnectorHealthModel:
        desktop_installed = any(path.is_file() for path in self._desktop_candidates())
        cli_available = self._cli_path() is not None
        if cli_available:
            status = "ready"
            summary = "Obsidian Desktop and the official CLI are available"
        elif desktop_installed:
            status = "cli_disabled"
            summary = "Enable Command line interface in Obsidian Settings > General"
        else:
            status = "not_installed"
            summary = "Install Obsidian 1.12.7 or newer to connect a Vault"
        return ObsidianConnectorHealthModel(
            desktop_installed=desktop_installed,
            cli_available=cli_available,
            status=status,
            public_summary=summary,
        )

    def _cli_path(self) -> Path | None:
        configured_path = self._environment.get("PATH")
        located = shutil.which("obsidian", path=configured_path)
        if located is None:
            return None
        candidate = Path(located)
        return candidate if candidate.is_file() else None

    def _desktop_candidates(self) -> tuple[Path, ...]:
        local_app_data = Path(self._environment.get("LOCALAPPDATA", ""))
        program_files = Path(self._environment.get("PROGRAMFILES", r"C:\Program Files"))
        return (
            local_app_data / "Programs" / "Obsidian" / "Obsidian.exe",
            program_files / "Obsidian" / "Obsidian.exe",
        )


__all__ = ["ObsidianConnector"]
