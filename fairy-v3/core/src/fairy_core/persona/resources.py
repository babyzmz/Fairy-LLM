from __future__ import annotations

import os
from pathlib import Path


def persona_resource_root() -> Path:
    configured = os.environ.get("FAIRY_RESOURCE_ROOT", "").strip()
    if configured:
        root = Path(configured).expanduser().resolve(strict=True)
    else:
        root = Path(__file__).resolve().parents[4] / "resources"
    persona_root = (root / "persona").resolve(strict=True)
    if not persona_root.is_relative_to(root.resolve(strict=True)):
        raise ValueError("Persona resource root escapes the approved resource directory")
    return persona_root


__all__ = ["persona_resource_root"]
