from .assets import router as assets_router
from .capabilities import router as capabilities_router
from .chat import router as chat_router
from .health import router as health_router
from .system import router as system_router

__all__ = [
    "assets_router",
    "capabilities_router",
    "chat_router",
    "health_router",
    "system_router",
]
