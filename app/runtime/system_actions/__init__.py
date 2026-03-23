from .action_models import SystemActionCategory, SystemActionResolution
from .action_registry import SystemActionRegistry

__all__ = [
    "SystemActionCategory",
    "SystemActionExecutor",
    "SystemActionRegistry",
    "SystemActionResolution",
    "TauriBridgeClient",
]


def __getattr__(name: str):
    if name == "SystemActionExecutor":
        from .system_action_executor import SystemActionExecutor

        return SystemActionExecutor
    if name == "TauriBridgeClient":
        from .tauri_bridge_client import TauriBridgeClient

        return TauriBridgeClient
    raise AttributeError(name)
