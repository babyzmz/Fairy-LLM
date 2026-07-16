from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from fairy_core.skills.loader import (
    SkillPackageError,
    SkillPackageLoader,
    package_content_digest,
)
from fairy_core.skills.registry import SkillRegistry

_CATALOG_ID = "design-taste-frontend"
_SOURCE_COMMIT = "b17742737e796305d829b3ad39eda3add0d79060"
_SOURCE_URL = (
    "https://raw.githubusercontent.com/Leonxlnx/taste-skill/"
    f"{_SOURCE_COMMIT}/skills/taste-skill/SKILL.md"
)
_SOURCE_SHA256 = "aa194351b246b8b4799099d4ed7b033d29eab6e6e3d58d8d2172978be7b3ec89"
_DESCRIPTION = (
    "Anti-slop frontend skill for landing pages, portfolios, and redesigns. "
    "The agent reads the brief, infers the right design direction, and ships "
    "interfaces that do not look templated. Real design systems when applicable, "
    "audit-first on redesigns, strict pre-flight check."
)
_MAX_DOWNLOAD_BYTES = 128 * 1024
_SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


@dataclass(frozen=True, slots=True)
class ExtensionCatalogEntry:
    extension_id: str
    kind: str
    name: str
    description: str
    publisher: str
    version: str
    source: str
    license: str
    experimental: bool


TASTE_SKILL_ENTRY = ExtensionCatalogEntry(
    extension_id=_CATALOG_ID,
    kind="skill",
    name="Taste Skill",
    description=_DESCRIPTION,
    publisher="Leonxlnx",
    version="2.0.0-experimental.1",
    source=f"https://github.com/Leonxlnx/taste-skill/tree/{_SOURCE_COMMIT}",
    license="MIT",
    experimental=True,
)
CONTEXT7_ENTRY = ExtensionCatalogEntry(
    extension_id="context7",
    kind="mcp_preset",
    name="Context7",
    description="Current, version-specific library documentation for coding tasks.",
    publisher="Upstash",
    version="remote",
    source="https://github.com/upstash/context7",
    license="MIT",
    experimental=False,
)


class SkillManager:
    def __init__(self, root: Path, registry: SkillRegistry) -> None:
        self._root = root
        self._registry = registry
        self._state_path = root / ".state.json"
        self._loader = SkillPackageLoader()
        self._lock = RLock()
        self._root.mkdir(parents=True, exist_ok=True)

    def load_installed(self) -> None:
        with self._lock:
            self._recover_interrupted_operations()
            paths = self._installed_paths()
            try:
                state = self._read_state()
            except ValueError:
                self._quarantine(self._state_path, "state")
                state = {path.name: False for path in paths}
                self._write_state(state)
            state_changed = False
            for path in paths:
                try:
                    package = self._loader.load(path)
                    self._registry.install(package, enabled=state.get(path.name, True))
                except (SkillPackageError, ValueError):
                    self._quarantine(path, path.name)
                    state_changed = state.pop(path.name, None) is not None or state_changed
            if state_changed:
                self._write_state(state)

    def catalog(self) -> tuple[ExtensionCatalogEntry, ...]:
        return (TASTE_SKILL_ENTRY, CONTEXT7_ENTRY)

    def install(self, extension_id: str) -> None:
        if extension_id != _CATALOG_ID:
            raise KeyError(f"Unknown Skill catalog entry: {extension_id}")
        with self._lock:
            if self._registry.get(extension_id) is not None:
                raise ValueError(f"Skill is already installed: {extension_id}")
            staged = self._stage_catalog_skill()
            destination = self._root / extension_id
            try:
                package = self._loader.load(staged)
                os.replace(staged, destination)
                try:
                    self._registry.install(package, enabled=True)
                    state = self._read_state()
                    state[extension_id] = True
                    self._write_state(state)
                except Exception:
                    if self._registry.get(extension_id) is not None:
                        self._registry.remove(extension_id)
                    shutil.rmtree(destination, ignore_errors=True)
                    raise
            finally:
                shutil.rmtree(staged.parent, ignore_errors=True)

    def update(self, name: str, *, expected_content_sha256: str) -> None:
        if name != _CATALOG_ID:
            raise KeyError(f"Skill is not in the curated catalog: {name}")
        with self._lock:
            current = self._registry.get(name)
            if current is None:
                raise KeyError(f"Skill is not installed: {name}")
            if current.content_sha256 != expected_content_sha256:
                raise ValueError("Skill content changed since it was displayed")
            staged = self._stage_catalog_skill()
            destination = self._root / name
            backup = self._root / f".backup-{name}"
            enabled = self._registry.enabled(name)
            moved_previous = False
            moved_candidate = False
            try:
                package = self._loader.load(staged)
                if backup.exists():
                    shutil.rmtree(backup)
                os.replace(destination, backup)
                moved_previous = True
                os.replace(staged, destination)
                moved_candidate = True
                self._registry.replace(package, enabled=enabled)
            except Exception:
                if moved_candidate and destination.exists():
                    os.replace(destination, staged)
                if moved_previous and backup.exists():
                    os.replace(backup, destination)
                raise
            else:
                shutil.rmtree(backup, ignore_errors=True)
            finally:
                shutil.rmtree(staged.parent, ignore_errors=True)

    def set_enabled(self, name: str, *, enabled: bool) -> None:
        with self._lock:
            previous = self._registry.enabled(name)
            state = self._read_state()
            self._registry.set_enabled(name, enabled=enabled)
            try:
                state[name] = enabled
                self._write_state(state)
            except Exception:
                self._registry.set_enabled(name, enabled=previous)
                raise

    def remove(self, name: str) -> None:
        with self._lock:
            destination = self._root / name
            if not destination.is_dir() or destination.parent != self._root:
                raise KeyError(f"Skill is not installed: {name}")
            removed = self._root / f".remove-{name}"
            if removed.exists():
                shutil.rmtree(removed)
            package = self._registry.get(name)
            if package is None:
                raise KeyError(f"Skill is not installed: {name}")
            enabled = self._registry.enabled(name)
            os.replace(destination, removed)
            registry_removed = False
            try:
                self._registry.remove(name)
                registry_removed = True
                state = self._read_state()
                state.pop(name, None)
                self._write_state(state)
            except Exception:
                if removed.exists() and not destination.exists():
                    os.replace(removed, destination)
                if registry_removed:
                    self._registry.install(package, enabled=enabled)
                raise
            shutil.rmtree(removed, ignore_errors=True)

    def _installed_paths(self) -> tuple[Path, ...]:
        return tuple(
            path
            for path in sorted(self._root.iterdir())
            if path.is_dir()
            and not path.name.startswith(".")
            and _SKILL_NAME.fullmatch(path.name) is not None
        )

    def _recover_interrupted_operations(self) -> None:
        temporary_state = self._state_path.with_suffix(".tmp")
        if temporary_state.is_file():
            if self._state_path.exists():
                temporary_state.unlink()
            else:
                try:
                    self._read_state_file(temporary_state)
                except ValueError:
                    self._quarantine(temporary_state, "state")
                else:
                    os.replace(temporary_state, self._state_path)

        for path in sorted(self._root.iterdir()):
            if path.is_dir() and path.name.startswith(".install-"):
                shutil.rmtree(path, ignore_errors=True)

        self._recover_moved_directories(".backup-", prefer_destination=True)
        self._recover_moved_directories(".remove-", prefer_destination=False)

    def _recover_moved_directories(
        self,
        prefix: str,
        *,
        prefer_destination: bool,
    ) -> None:
        for source in sorted(self._root.glob(f"{prefix}*")):
            name = source.name.removeprefix(prefix)
            if not source.is_dir() or _SKILL_NAME.fullmatch(name) is None:
                continue
            if source.is_symlink() or (
                hasattr(source, "is_junction") and source.is_junction()
            ):
                self._quarantine(source, f"recovery-{name}")
                continue
            destination = self._root / name
            if not destination.exists():
                os.replace(source, destination)
                continue
            if prefer_destination:
                try:
                    self._loader.load(destination)
                except (SkillPackageError, ValueError):
                    self._quarantine(destination, name)
                    os.replace(source, destination)
                    continue
            shutil.rmtree(source, ignore_errors=True)

    def _quarantine(self, source: Path, label: str) -> Path:
        for sequence in range(1, 10_000):
            destination = self._root / f".quarantine-{label}-{sequence}"
            if not destination.exists():
                os.replace(source, destination)
                return destination
        raise OSError("Skill quarantine capacity is exhausted")

    def _read_state(self) -> dict[str, bool]:
        if not self._state_path.is_file():
            return {}
        return self._read_state_file(self._state_path)

    @staticmethod
    def _read_state_file(path: Path) -> dict[str, bool]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("Skill installation state is invalid") from error
        if not isinstance(raw, dict) or any(
            not isinstance(key, str) or not isinstance(value, bool)
            for key, value in raw.items()
        ):
            raise ValueError("Skill installation state is invalid")
        return raw

    def _write_state(self, state: dict[str, bool]) -> None:
        temporary = self._state_path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(state, ensure_ascii=True, sort_keys=True, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self._state_path)

    def _stage_catalog_skill(self) -> Path:
        instructions = _download_pinned_skill()
        staging_root = Path(tempfile.mkdtemp(prefix=".install-", dir=self._root))
        staged = staging_root / _CATALOG_ID
        staged.mkdir()
        (staged / "SKILL.md").write_bytes(instructions)
        digest = package_content_digest(staged)
        manifest = {
            "schema_version": 1,
            "name": _CATALOG_ID,
            "version": TASTE_SKILL_ENTRY.version,
            "description": _DESCRIPTION,
            "instructions": "SKILL.md",
            "input_schema": {
                "type": "object",
                "properties": {"brief": {"type": "string", "maxLength": 12000}},
                "additionalProperties": False,
            },
            "required_capabilities": [],
            "compatible_mcp_servers": [],
            "provenance": {
                "publisher": TASTE_SKILL_ENTRY.publisher,
                "source": TASTE_SKILL_ENTRY.source,
                "license": TASTE_SKILL_ENTRY.license,
            },
            "content_sha256": digest,
        }
        (staged / "fairy-skill.json").write_text(
            json.dumps(manifest, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return staged


def _download_pinned_skill() -> bytes:
    request = urllib.request.Request(_SOURCE_URL, headers={"User-Agent": "Fairy-V3"})
    with urllib.request.urlopen(request, timeout=20) as response:
        content = response.read(_MAX_DOWNLOAD_BYTES + 1)
    if len(content) > _MAX_DOWNLOAD_BYTES:
        raise ValueError("Skill package download is too large")
    if hashlib.sha256(content).hexdigest() != _SOURCE_SHA256:
        raise ValueError("Skill source digest does not match the curated catalog")
    return content


__all__ = ["CONTEXT7_ENTRY", "TASTE_SKILL_ENTRY", "ExtensionCatalogEntry", "SkillManager"]
