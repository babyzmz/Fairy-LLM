from __future__ import annotations

import os
import sys
from pathlib import Path


def _resource_root() -> Path:
    configured = os.environ.get("FAIRY_RESOURCE_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser()
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if getattr(sys, "frozen", False) and bundle_dir:
        # PyInstaller onefile bundles ``resources`` alongside the extracted code.
        return Path(bundle_dir) / "resources"
    return Path(__file__).resolve().parents[4] / "resources"


def persona_resource_root() -> Path:
    root = _resource_root().resolve(strict=True)
    persona_root = (root / "persona").resolve(strict=True)
    if not persona_root.is_relative_to(root):
        raise ValueError("Persona resource root escapes the approved resource directory")
    return persona_root


__all__ = ["persona_resource_root"]
