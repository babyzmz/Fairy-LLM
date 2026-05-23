"""
Fairy bundle runtime feature flags.

These flags tune the canonical bundle runtime and deterministic direct executors.
They no longer represent a legacy-vs-new pipeline split.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
_CONFIG_FILE = BASE_DIR / "config" / "lazy_skills_config.json"


def _str_to_bool(value: str) -> bool:
    return value.strip().lower() in {"true", "1", "yes", "on"}


def _load_config_file() -> dict:
    if not _CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


@dataclass
class LazySkillsFlags:
    """Runtime feature flags for the canonical bundle runtime."""

    use_lazy_skills: bool = False
    use_bundle_fallback: bool = True
    enable_debug_logging: bool = True
    graceful_fallback: bool = True

    def __post_init__(self) -> None:
        logger.info(
            "lazy_skills_flags use_lazy=%s bundle_fallback=%s debug=%s graceful=%s",
            self.use_lazy_skills,
            self.use_bundle_fallback,
            self.enable_debug_logging,
            self.graceful_fallback,
        )


def _resolve_flags() -> LazySkillsFlags:
    """Resolve feature flags from env vars -> config file -> defaults."""
    config = _load_config_file()

    use_lazy = os.getenv("USE_LAZY_SKILLS")
    if use_lazy is not None:
        use_lazy_val = _str_to_bool(use_lazy)
    else:
        use_lazy_val = config.get("use_lazy_skills", False)

    use_bundle_fallback = os.getenv("USE_BUNDLE_FALLBACK")
    if use_bundle_fallback is not None:
        use_bundle_fallback_val = _str_to_bool(use_bundle_fallback)
    else:
        use_bundle_fallback_val = config.get("use_bundle_fallback", True)

    debug = os.getenv("LAZY_SKILLS_DEBUG")
    if debug is not None:
        debug_val = _str_to_bool(debug)
    else:
        debug_val = config.get("enable_debug_logging", True)

    graceful = os.getenv("LAZY_SKILLS_GRACEFUL_FALLBACK")
    if graceful is not None:
        graceful_val = _str_to_bool(graceful)
    else:
        graceful_val = config.get("graceful_fallback", True)

    return LazySkillsFlags(
        use_lazy_skills=use_lazy_val,
        use_bundle_fallback=use_bundle_fallback_val,
        enable_debug_logging=debug_val,
        graceful_fallback=graceful_val,
    )


lazy_skills_flags = _resolve_flags()
