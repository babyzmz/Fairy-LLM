"""
Fairy Lazy Skills Feature Flags
================================

Controls whether the new Anthropic-style lazy skill runtime is active.

Feature flags can be set via:
  1. Environment variables (highest priority)
  2. config/lazy_skills_config.json file
  3. Hardcoded defaults (lowest priority)

Usage in code:
    from app.lazy_runtime.feature_flags import lazy_skills_flags
    if lazy_skills_flags.use_lazy_skills:
        # new path
    else:
        # legacy path
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
    """Runtime feature flags for the lazy skill system."""

    # Master switch: enable the new lazy skill routing pipeline
    use_lazy_skills: bool = False

    # Keep legacy skills available as fallback
    use_legacy_fallback: bool = True

    # Log detailed debug info for the new pipeline
    enable_debug_logging: bool = True

    # If True, the new pipeline will be tried first; if it fails, fall back to legacy
    # If False and use_lazy_skills is True, ONLY the new pipeline runs (no fallback)
    graceful_fallback: bool = True

    def __post_init__(self) -> None:
        logger.info(
            "lazy_skills_flags use_lazy=%s legacy_fallback=%s debug=%s graceful=%s",
            self.use_lazy_skills,
            self.use_legacy_fallback,
            self.enable_debug_logging,
            self.graceful_fallback,
        )


def _resolve_flags() -> LazySkillsFlags:
    """Resolve feature flags from env vars → config file → defaults."""
    config = _load_config_file()

    use_lazy = os.getenv("USE_LAZY_SKILLS")
    if use_lazy is not None:
        use_lazy_val = _str_to_bool(use_lazy)
    else:
        use_lazy_val = config.get("use_lazy_skills", False)

    use_legacy = os.getenv("USE_LEGACY_FALLBACK")
    if use_legacy is not None:
        use_legacy_val = _str_to_bool(use_legacy)
    else:
        use_legacy_val = config.get("use_legacy_fallback", True)

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
        use_legacy_fallback=use_legacy_val,
        enable_debug_logging=debug_val,
        graceful_fallback=graceful_val,
    )


# Module-level singleton – imported by fairy_core.py and test scripts
lazy_skills_flags = _resolve_flags()
