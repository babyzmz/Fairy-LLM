"""
Fairy Skill Lazy Loader
=======================

Implements the 3-tier progressive disclosure model:

  Tier 1 – Metadata   (~100 tokens)  : name + description, loaded at startup for ALL skills.
  Tier 2 – Instructions (<5 000 tok) : Full SKILL.md body, loaded when a skill is ACTIVATED.
  Tier 3 – Resources  (as needed)    : scripts/, references/, assets/, loaded on demand.

This module is intentionally stateless between requests; caching is handled at the
SkillBundle level so the caller can decide eviction policy.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SkillMetadata:
    """Tier-1 payload: cheap to hold in memory for every registered skill."""

    name: str
    description: str
    trigger_keywords: list[str] = field(default_factory=list)
    trigger_intents: list[str] = field(default_factory=list)
    priority: int = 50
    path: str = ""


@dataclass
class SkillBundle:
    """Tier-2 payload: the full instruction set for a single activated skill."""

    metadata: SkillMetadata
    instructions: str = ""          # Markdown body from SKILL.md
    allowed_tools: list[str] = field(default_factory=list)  # From tools.json
    examples: str = ""              # Optional examples.md content
    frontmatter: dict[str, Any] = field(default_factory=dict)  # Raw YAML frontmatter

    @property
    def name(self) -> str:
        return self.metadata.name

    @property
    def description(self) -> str:
        return self.metadata.description


# ---------------------------------------------------------------------------
# Lazy Loader
# ---------------------------------------------------------------------------


class SkillLazyLoader:
    """Discovers skills from the registry and loads bundles on demand."""

    def __init__(self, skills_root: str | Path, registry_path: str | Path | None = None) -> None:
        self._skills_root = Path(skills_root)
        self._registry_path = Path(registry_path) if registry_path else None
        self._metadata_cache: dict[str, SkillMetadata] = {}
        self._bundle_cache: dict[str, SkillBundle] = {}
        self._discover()

    # ------------------------------------------------------------------
    # Tier 1 – Metadata (loaded at startup)
    # ------------------------------------------------------------------

    def _discover(self) -> None:
        """Populate the metadata cache from the registry JSON or by scanning the filesystem."""
        if self._registry_path and self._registry_path.exists():
            self._discover_from_registry()
        else:
            self._discover_from_filesystem()
        logger.info("skill_discovery count=%d names=%s", len(self._metadata_cache), list(self._metadata_cache))

    def _discover_from_registry(self) -> None:
        with open(self._registry_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        for entry in data.get("skills", []):
            name = entry["name"]
            self._metadata_cache[name] = SkillMetadata(
                name=name,
                description=entry.get("description", ""),
                trigger_keywords=entry.get("trigger_keywords", []),
                trigger_intents=entry.get("trigger_intents", []),
                priority=entry.get("priority", 50),
                path=entry.get("path", ""),
            )

    def _discover_from_filesystem(self) -> None:
        """Fallback: scan skills_root for directories containing SKILL.md."""
        for child in sorted(self._skills_root.iterdir()):
            skill_md = child / "SKILL.md"
            if child.is_dir() and skill_md.exists():
                fm = self._parse_frontmatter(skill_md)
                name = fm.get("name", child.name)
                self._metadata_cache[name] = SkillMetadata(
                    name=name,
                    description=fm.get("description", ""),
                    path=str(child.relative_to(self._skills_root.parent)),
                )

    # ------------------------------------------------------------------
    # Tier 2 – Full bundle (loaded on activation)
    # ------------------------------------------------------------------

    def load_bundle(self, skill_name: str, *, force_reload: bool = False) -> SkillBundle | None:
        """Load the full instruction bundle for *skill_name*.

        Returns ``None`` if the skill is unknown or the bundle directory is missing.
        """
        if not force_reload and skill_name in self._bundle_cache:
            return self._bundle_cache[skill_name]

        meta = self._metadata_cache.get(skill_name)
        if meta is None:
            logger.warning("load_bundle unknown_skill=%s", skill_name)
            return None

        skill_dir = self._resolve_skill_dir(meta)
        if skill_dir is None or not skill_dir.exists():
            logger.warning("load_bundle missing_dir skill=%s dir=%s", skill_name, skill_dir)
            return None

        skill_md_path = skill_dir / "SKILL.md"
        frontmatter, instructions = self._read_skill_md(skill_md_path)
        allowed_tools = self._read_tools_json(skill_dir / "tools.json")
        examples = self._read_optional_file(skill_dir / "examples.md")

        bundle = SkillBundle(
            metadata=meta,
            instructions=instructions,
            allowed_tools=allowed_tools,
            examples=examples,
            frontmatter=frontmatter,
        )
        self._bundle_cache[skill_name] = bundle
        logger.info(
            "load_bundle skill=%s tools=%d instructions_len=%d",
            skill_name,
            len(allowed_tools),
            len(instructions),
        )
        return bundle

    # ------------------------------------------------------------------
    # Tier 3 – Resource files (loaded on demand)
    # ------------------------------------------------------------------

    def load_resource(self, skill_name: str, relative_path: str) -> str | None:
        """Load an arbitrary resource file from a skill directory."""
        meta = self._metadata_cache.get(skill_name)
        if meta is None:
            return None
        skill_dir = self._resolve_skill_dir(meta)
        if skill_dir is None:
            return None
        target = (skill_dir / relative_path).resolve()
        # Security: ensure the resolved path stays within the skill directory.
        if not str(target).startswith(str(skill_dir.resolve())):
            logger.warning("load_resource path_escape skill=%s path=%s", skill_name, relative_path)
            return None
        if not target.exists():
            return None
        return target.read_text(encoding="utf-8")

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def all_metadata(self) -> list[SkillMetadata]:
        """Return Tier-1 metadata for every registered skill (cheap)."""
        return list(self._metadata_cache.values())

    def get_metadata(self, skill_name: str) -> SkillMetadata | None:
        return self._metadata_cache.get(skill_name)

    def invalidate_cache(self, skill_name: str | None = None) -> None:
        """Drop cached bundles so the next ``load_bundle`` re-reads from disk."""
        if skill_name is None:
            self._bundle_cache.clear()
            self._metadata_cache.clear()
            self._discover()
        else:
            self._bundle_cache.pop(skill_name, None)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_skill_dir(self, meta: SkillMetadata) -> Path | None:
        if meta.path:
            # path in registry is relative to the app/ directory (e.g. "skills/bundles/web_research")
            candidate = self._skills_root.parent / meta.path
            if candidate.is_dir():
                return candidate
            # Also try relative to skills_root itself
            candidate2 = self._skills_root / meta.path
            if candidate2.is_dir():
                return candidate2
        # Fallback: try matching by name with underscores
        candidate = self._skills_root / meta.name.replace("-", "_")
        if candidate.is_dir():
            return candidate
        return None

    @staticmethod
    def _parse_frontmatter(skill_md_path: Path) -> dict[str, Any]:
        """Extract YAML frontmatter from a SKILL.md file."""
        text = skill_md_path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            return {}
        end = text.find("---", 3)
        if end == -1:
            return {}
        try:
            import yaml
            return yaml.safe_load(text[3:end]) or {}
        except Exception:
            return {}

    def _read_skill_md(self, path: Path) -> tuple[dict[str, Any], str]:
        """Return (frontmatter_dict, markdown_body)."""
        if not path.exists():
            return {}, ""
        text = path.read_text(encoding="utf-8")
        frontmatter: dict[str, Any] = {}
        body = text
        if text.startswith("---"):
            end = text.find("---", 3)
            if end != -1:
                try:
                    import yaml
                    frontmatter = yaml.safe_load(text[3:end]) or {}
                except Exception:
                    pass
                body = text[end + 3:].strip()
        return frontmatter, body

    @staticmethod
    def _read_tools_json(path: Path) -> list[str]:
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [str(t) for t in data]
        except (json.JSONDecodeError, OSError):
            pass
        return []

    @staticmethod
    def _read_optional_file(path: Path) -> str:
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""
