from __future__ import annotations

from fairy_core.contracts.common import ContractModel


class ObsidianConnectorHealthModel(ContractModel):
    desktop_installed: bool
    cli_available: bool
    minimum_installer_version: str = "1.12.7"
    status: str
    public_summary: str


__all__ = ["ObsidianConnectorHealthModel"]
