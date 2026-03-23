from __future__ import annotations

from app.legacy_surface.surface_actions import (
    EXPLICIT_DESKTOP_AUTOMATION_MARKERS,
    is_explicit_desktop_automation_command,
    strip_explicit_desktop_automation_prefix,
)
from app.legacy_surface.surface_executor import LegacySurfaceExecutor
from app.legacy_surface.surface_models import LegacySurfaceDecision

__all__ = [
    "EXPLICIT_DESKTOP_AUTOMATION_MARKERS",
    "LegacySurfaceDecision",
    "LegacySurfaceExecutor",
    "is_explicit_desktop_automation_command",
    "strip_explicit_desktop_automation_prefix",
]
