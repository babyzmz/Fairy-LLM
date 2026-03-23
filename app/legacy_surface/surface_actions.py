from __future__ import annotations

EXPLICIT_DESKTOP_AUTOMATION_MARKERS: tuple[str, ...] = (
    "/desktop",
    "/surface",
    "desktop automation:",
    "surface automation:",
    "legacy desktop action:",
    "legacy surface:",
    "桌面自动化:",
    "旧桌面自动化:",
)


def is_explicit_desktop_automation_command(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    return any(lowered.startswith(marker) for marker in EXPLICIT_DESKTOP_AUTOMATION_MARKERS)


def strip_explicit_desktop_automation_prefix(text: str) -> str:
    raw = str(text or "").strip()
    lowered = raw.lower()
    for marker in EXPLICIT_DESKTOP_AUTOMATION_MARKERS:
        if lowered.startswith(marker):
            return raw[len(marker) :].strip()
    return raw
