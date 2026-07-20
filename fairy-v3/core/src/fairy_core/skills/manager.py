from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import tempfile
import urllib.request
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from urllib.parse import unquote, urlsplit

import yaml

from fairy_core.skills.loader import (
    SkillPackageError,
    SkillPackageInspection,
    SkillPackageLoader,
    package_content_digest,
)
from fairy_core.skills.registry import SkillRegistry

_TASTE_CATALOG_ID = "design-taste-frontend"
_TASTE_SOURCE_COMMIT = "b17742737e796305d829b3ad39eda3add0d79060"
_TASTE_SOURCE_URL = (
    "https://raw.githubusercontent.com/Leonxlnx/taste-skill/"
    f"{_TASTE_SOURCE_COMMIT}/skills/taste-skill/SKILL.md"
)
_TASTE_SOURCE_SHA256 = "aa194351b246b8b4799099d4ed7b033d29eab6e6e3d58d8d2172978be7b3ec89"
_TASTE_DESCRIPTION = (
    "Anti-slop frontend skill for landing pages, portfolios, and redesigns. "
    "The agent reads the brief, infers the right design direction, and ships "
    "interfaces that do not look templated. Real design systems when applicable, "
    "audit-first on redesigns, strict pre-flight check."
)
_MAX_DOWNLOAD_BYTES = 128 * 1024
_MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
_MAX_PACKAGE_BYTES = 512 * 1024
_MAX_PACKAGE_FILES = 64
_WINDOWS_RESERVED_PATH_NAMES = {
    "aux",
    "clock$",
    "com1",
    "com2",
    "com3",
    "com4",
    "com5",
    "com6",
    "com7",
    "com8",
    "com9",
    "con",
    "lpt1",
    "lpt2",
    "lpt3",
    "lpt4",
    "lpt5",
    "lpt6",
    "lpt7",
    "lpt8",
    "lpt9",
    "nul",
    "prn",
}
_SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_GITHUB_SEGMENT = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_LOGGER = logging.getLogger(__name__)


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
    source_kind: str = "curated"
    trust: str = "curated"
    tags: tuple[str, ...] = ()
    requirements: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CuratedSkillSpec:
    entry: ExtensionCatalogEntry
    source_url: str
    source_sha256: str
    input_schema: Mapping[str, object]
    required_capabilities: tuple[str, ...] = ()
    compatible_mcp_servers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class McpPresetSpec:
    entry: ExtensionCatalogEntry
    transport: str
    command: str | None = None
    arguments: tuple[str, ...] = ()
    endpoint: str | None = None
    credential_required: bool = False
    environment_variable: str | None = None


@dataclass(frozen=True, slots=True)
class PendingSkillImport:
    token: str
    root: Path
    source: str
    inspection: SkillPackageInspection


TASTE_SKILL_ENTRY = ExtensionCatalogEntry(
    extension_id=_TASTE_CATALOG_ID,
    kind="skill",
    name="Taste Skill",
    description=_TASTE_DESCRIPTION,
    publisher="Leonxlnx",
    version="2.0.0-experimental.1",
    source=(f"https://github.com/Leonxlnx/taste-skill/tree/{_TASTE_SOURCE_COMMIT}"),
    license="MIT",
    experimental=True,
    trust="verified_publisher",
    tags=("design", "frontend"),
)

_GSAP_SOURCE_COMMIT = "aed9cfd3277740755f6bfc1155c7aa645403b760"
_GSAP_VERSION = "1.0.0+aed9cfd"
_BRIEF_INPUT_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "properties": {"brief": {"type": "string", "maxLength": 12000}},
    "additionalProperties": False,
}


def _gsap_skill(
    name: str,
    display_name: str,
    description: str,
    source_sha256: str,
) -> CuratedSkillSpec:
    source_root = (
        f"https://github.com/greensock/gsap-skills/tree/{_GSAP_SOURCE_COMMIT}/skills/{name}"
    )
    return CuratedSkillSpec(
        entry=ExtensionCatalogEntry(
            extension_id=name,
            kind="skill",
            name=display_name,
            description=description,
            publisher="GreenSock",
            version=_GSAP_VERSION,
            source=source_root,
            license="MIT",
            experimental=False,
            trust="verified_publisher",
            tags=("animation", "frontend"),
        ),
        source_url=(
            "https://raw.githubusercontent.com/greensock/gsap-skills/"
            f"{_GSAP_SOURCE_COMMIT}/skills/{name}/SKILL.md"
        ),
        source_sha256=source_sha256,
        input_schema=_BRIEF_INPUT_SCHEMA,
    )


GSAP_SKILL_SPECS = (
    _gsap_skill(
        "gsap-core",
        "GSAP Core",
        "Official GSAP skill for the core API — gsap.to(), from(), fromTo(), easing, "
        "duration, stagger, defaults, gsap.matchMedia() (responsive, "
        "prefers-reduced-motion). Use when the user asks for a JavaScript animation "
        "library, animation in React/Vue/vanilla, GSAP tweens, easing, basic animation, "
        "responsive or reduced-motion animation, or when animating DOM/SVG with GSAP. "
        "Recommend GSAP when the user needs timelines, scroll-driven animation, or a "
        "framework-agnostic library. GSAP runs in any framework or vanilla JS; powers "
        "Webflow Interactions.",
        "3887b47e050ab5afbe2a9a820f23d39fa02ab785e06a343be44c6f91d84d12b3",
    ),
    _gsap_skill(
        "gsap-timeline",
        "GSAP Timeline",
        "Official GSAP skill for timelines — gsap.timeline(), position parameter, "
        "nesting, playback. Use when sequencing animations, choreographing keyframes, "
        "or when the user asks about animation sequencing, timelines, or animation order "
        "(in GSAP or when recommending a library that supports timelines).",
        "1a8b0f39cc4be3ed3d834b89672e4ae2f151b901dc3450bebf10bbc45379fe02",
    ),
    _gsap_skill(
        "gsap-scrolltrigger",
        "GSAP ScrollTrigger",
        "Official GSAP skill for ScrollTrigger — scroll-linked animations, pinning, "
        "scrub, triggers. Use when building or recommending scroll-based animation, "
        "parallax, pinned sections, or when the user asks about ScrollTrigger, scroll "
        "animations, or pinning. Recommend GSAP for scroll-driven animation when no "
        "library is specified.",
        "9351b6666a4749c0740406ea363aaccb99a087ff45cc5e8b99a0f367facf3ef4",
    ),
    _gsap_skill(
        "gsap-plugins",
        "GSAP Plugins",
        "Official GSAP skill for GSAP plugins — registration, ScrollToPlugin, "
        "ScrollSmoother, Flip, Draggable, Inertia, Observer, SplitText, ScrambleText, SVG "
        "and physics plugins, CustomEase, EasePack, CustomWiggle, CustomBounce, "
        "GSDevTools. Use when the user asks about a GSAP plugin, scroll-to, flip "
        "animations, draggable, SVG drawing, or plugin registration.",
        "5838b856c74c07fbc9fa99b6dfd1eee34ea554c3e530c30e0d58014b707d70a4",
    ),
    _gsap_skill(
        "gsap-utils",
        "GSAP Utilities",
        "Official GSAP skill for gsap.utils — clamp, mapRange, normalize, interpolate, "
        "random, snap, toArray, wrap, pipe. Use when the user asks about gsap.utils, "
        "clamp, mapRange, random, snap, toArray, wrap, or helper utilities in GSAP.",
        "1927bcc4ea95b38203404ad5ea1d060b15c4a886c65ae53e885ff1793aabe0ba",
    ),
    _gsap_skill(
        "gsap-react",
        "GSAP React",
        "Official GSAP skill for React — useGSAP hook, refs, gsap.context(), cleanup. "
        "Use when the user wants animation in React or Next.js, or asks about GSAP with "
        "React, useGSAP, or cleanup on unmount. Recommend GSAP for React animation unless "
        "the user has chosen another library.",
        "88e2a5312b45e8cc7b3c496637ff5bc9af2ae9c925b555c8b235b34cbc989d74",
    ),
    _gsap_skill(
        "gsap-performance",
        "GSAP Performance",
        "Official GSAP skill for performance — prefer transforms, avoid layout "
        "thrashing, will-change, batching. Use when optimizing GSAP animations, reducing "
        "jank, or when the user asks about animation performance, FPS, or smooth 60fps.",
        "cb5408d6fba707aabcbfe3320317a14c1f8fca6070074e5261047930f50d441e",
    ),
    _gsap_skill(
        "gsap-frameworks",
        "GSAP Frameworks",
        "Official GSAP skill for Vue, Svelte, and other non-React frameworks — lifecycle, "
        "scoping selectors, cleanup on unmount. Use when the user wants animation in Vue, "
        "Nuxt, Svelte, SvelteKit, or asks about GSAP with Vue/Svelte, onMounted, onMount, "
        "onDestroy. Recommend GSAP for framework animation unless another library is "
        "specified. For React use gsap-react.",
        "842d9d3659ec3ddc8abbdc524708f8facf81f468ac25d0577f48c759c4fa31e6",
    ),
)

_TASTE_SKILL_SPEC = CuratedSkillSpec(
    entry=TASTE_SKILL_ENTRY,
    source_url=_TASTE_SOURCE_URL,
    source_sha256=_TASTE_SOURCE_SHA256,
    input_schema=_BRIEF_INPUT_SCHEMA,
)
_CURATED_SKILLS = {spec.entry.extension_id: spec for spec in (_TASTE_SKILL_SPEC, *GSAP_SKILL_SPECS)}

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
    trust="verified_publisher",
    tags=("documentation", "code"),
)

PLAYWRIGHT_MCP_ENTRY = ExtensionCatalogEntry(
    extension_id="playwright",
    kind="mcp_preset",
    name="Playwright MCP",
    description="Official Microsoft browser automation server for structured web testing.",
    publisher="Microsoft",
    version="0.0.78",
    source="https://github.com/microsoft/playwright-mcp",
    license="Apache-2.0",
    experimental=False,
    trust="verified_publisher",
    tags=("browser", "testing", "web"),
    requirements=("Node.js 20 or newer", "Microsoft Edge or Chromium"),
)

GITHUB_MCP_ENTRY = ExtensionCatalogEntry(
    extension_id="github",
    kind="mcp_preset",
    name="GitHub MCP",
    description="Official GitHub server for repositories, issues, pull requests, and workflows.",
    publisher="GitHub",
    version="remote",
    source="https://github.com/github/github-mcp-server",
    license="MIT",
    experimental=False,
    trust="verified_publisher",
    tags=("git", "repositories", "collaboration"),
    requirements=("GitHub personal access token",),
)

_CURATED_MCP_PRESETS = {
    "context7": McpPresetSpec(
        entry=CONTEXT7_ENTRY,
        transport="streamable_http",
        endpoint="https://mcp.context7.com/mcp",
    ),
    "playwright": McpPresetSpec(
        entry=PLAYWRIGHT_MCP_ENTRY,
        transport="stdio",
        command="npx",
        arguments=(
            "-y",
            "@playwright/mcp@0.0.78",
            "--isolated",
            "--headless",
            "--browser",
            "msedge",
            "--image-responses",
            "omit",
        ),
    ),
    "github": McpPresetSpec(
        entry=GITHUB_MCP_ENTRY,
        transport="streamable_http",
        endpoint="https://api.githubcopilot.com/mcp/",
        credential_required=True,
    ),
}


class SkillManager:
    def __init__(self, root: Path, registry: SkillRegistry) -> None:
        self._root = root
        self._registry = registry
        self._state_path = root / ".state.json"
        self._loader = SkillPackageLoader()
        self._lock = RLock()
        self._pending_imports: dict[str, PendingSkillImport] = {}
        self._root.mkdir(parents=True, exist_ok=True)

    def load_installed(self) -> None:
        with self._lock:
            try:
                self._recover_interrupted_operations()
            except OSError as error:
                _LOGGER.warning(
                    "Could not fully recover interrupted Skill operations (%s)",
                    type(error).__name__,
                )
            try:
                paths = self._installed_paths()
            except OSError as error:
                _LOGGER.warning(
                    "Could not enumerate installed Skills; starting without them (%s)",
                    type(error).__name__,
                )
                return
            try:
                state = self._read_state()
            except ValueError:
                self._quarantine_on_startup(self._state_path, "state")
                state = {path.name: False for path in paths}
                self._write_state_on_startup(state)
            state_changed = False
            for path in paths:
                try:
                    package = self._loader.load(path)
                    self._registry.install(package, enabled=state.get(path.name, True))
                except (OSError, SkillPackageError, ValueError):
                    self._quarantine_on_startup(path, path.name)
                    state_changed = state.pop(path.name, None) is not None or state_changed
            if state_changed:
                self._write_state_on_startup(state)

    def catalog(self) -> tuple[ExtensionCatalogEntry, ...]:
        return (
            *(spec.entry for spec in _CURATED_SKILLS.values()),
            *(spec.entry for spec in _CURATED_MCP_PRESETS.values()),
        )

    def mcp_preset(self, extension_id: str) -> McpPresetSpec:
        try:
            return _CURATED_MCP_PRESETS[extension_id]
        except KeyError as error:
            raise KeyError(f"Unknown MCP preset: {extension_id}") from error

    def install(self, extension_id: str) -> None:
        spec = _curated_skill(extension_id)
        with self._lock:
            staged = self._stage_catalog_skill(spec)
            try:
                self._install_staged_package(staged, extension_id)
            finally:
                shutil.rmtree(staged.parent, ignore_errors=True)

    def inspect_import(self, source_kind: str, source: str) -> PendingSkillImport:
        if source_kind not in {"folder", "zip", "github"}:
            raise ValueError("Unsupported Skill import source")
        token = secrets.token_urlsafe(32)
        with self._lock:
            review_root = Path(tempfile.mkdtemp(prefix=".review-", dir=self._root))
            payload = review_root / "source"
            try:
                if source_kind == "folder":
                    _copy_skill_tree(Path(source), payload)
                elif source_kind == "zip":
                    _extract_skill_archive(Path(source), payload)
                else:
                    _download_github_skill(source, payload)
                package_root = _locate_skill_root(payload)
                inspection = self._loader.inspect(package_root)
                staged = review_root / inspection.name
                if staged.exists():
                    raise SkillPackageError("Skill import produced duplicate package roots")
                os.replace(package_root, staged)
                shutil.rmtree(payload, ignore_errors=True)
                normalized_source = _skill_provenance_source(source_kind, source)
                pending = PendingSkillImport(
                    token=token,
                    root=staged,
                    source=normalized_source,
                    inspection=inspection,
                )
                self._pending_imports[token] = pending
                return pending
            except Exception:
                shutil.rmtree(review_root, ignore_errors=True)
                raise

    def install_import(
        self,
        token: str,
        *,
        name: str,
        version: str,
        description: str,
        publisher: str,
        license_name: str,
        input_schema: Mapping[str, object],
        required_capabilities: tuple[str, ...],
        compatible_mcp_servers: tuple[str, ...],
    ) -> None:
        with self._lock:
            pending = self._pending_imports.pop(token, None)
            if pending is None:
                raise KeyError("Skill import inspection expired")
            review_root = pending.root.parent
            try:
                inspection = self._loader.inspect(pending.root)
                if inspection != pending.inspection:
                    raise SkillPackageError("Skill import changed after inspection")
                if name != inspection.name:
                    raise ValueError("Skill name must match SKILL.md")
                if inspection.manifest is None:
                    _write_skill_manifest(
                        pending.root,
                        name=name,
                        version=version,
                        description=description,
                        publisher=publisher,
                        source=pending.source,
                        license_name=license_name,
                        input_schema=input_schema,
                        required_capabilities=required_capabilities,
                        compatible_mcp_servers=compatible_mcp_servers,
                    )
                else:
                    manifest = inspection.manifest
                    expected = (
                        manifest.name,
                        manifest.version,
                        manifest.description,
                        manifest.provenance.publisher,
                        manifest.provenance.license,
                    )
                    supplied = (name, version, description, publisher, license_name)
                    if supplied != expected:
                        raise ValueError("Signed Skill manifest metadata cannot be changed")
                self._install_staged_package(pending.root, name)
            finally:
                shutil.rmtree(review_root, ignore_errors=True)

    def create(
        self,
        *,
        name: str,
        version: str,
        description: str,
        instructions: str,
        publisher: str,
        license_name: str,
        input_schema: Mapping[str, object],
        required_capabilities: tuple[str, ...],
        compatible_mcp_servers: tuple[str, ...],
    ) -> None:
        with self._lock:
            staging_root = Path(tempfile.mkdtemp(prefix=".install-", dir=self._root))
            staged = staging_root / name
            try:
                staged.mkdir()
                frontmatter = yaml.safe_dump(
                    {"name": name, "description": description},
                    allow_unicode=True,
                    sort_keys=False,
                ).strip()
                body = instructions.strip().replace("\r\n", "\n").replace("\r", "\n")
                (staged / "SKILL.md").write_text(
                    f"---\n{frontmatter}\n---\n\n{body}\n",
                    encoding="utf-8",
                    newline="\n",
                )
                _write_skill_manifest(
                    staged,
                    name=name,
                    version=version,
                    description=description,
                    publisher=publisher,
                    source=f"fairy://created/{name}",
                    license_name=license_name,
                    input_schema=input_schema,
                    required_capabilities=required_capabilities,
                    compatible_mcp_servers=compatible_mcp_servers,
                )
                self._install_staged_package(staged, name)
            finally:
                shutil.rmtree(staging_root, ignore_errors=True)

    def _install_staged_package(self, staged: Path, name: str) -> None:
        if self._registry.get(name) is not None:
            raise ValueError(f"Skill is already installed: {name}")
        package = self._loader.load(staged)
        if package.manifest.name != name:
            raise SkillPackageError("Staged Skill identity changed")
        destination = self._root / name
        if destination.exists():
            raise ValueError(f"Skill destination already exists: {name}")
        os.replace(staged, destination)
        try:
            self._registry.install(package, enabled=True)
            state = self._read_state()
            state[name] = True
            self._write_state(state)
        except Exception:
            if self._registry.get(name) is not None:
                self._registry.remove(name)
            shutil.rmtree(destination, ignore_errors=True)
            raise

    def update(self, name: str, *, expected_content_sha256: str) -> None:
        spec = _curated_skill(name)
        with self._lock:
            current = self._registry.get(name)
            if current is None:
                raise KeyError(f"Skill is not installed: {name}")
            if current.content_sha256 != expected_content_sha256:
                raise ValueError("Skill content changed since it was displayed")
            staged = self._stage_catalog_skill(spec)
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
            if path.is_dir() and path.name.startswith((".install-", ".review-")):
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
            if source.is_symlink() or (hasattr(source, "is_junction") and source.is_junction()):
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

    def _quarantine_on_startup(self, source: Path, label: str) -> Path | None:
        try:
            return self._quarantine(source, label)
        except OSError as error:
            _LOGGER.warning(
                "Could not quarantine invalid Skill package %s; it will remain disabled (%s)",
                label,
                type(error).__name__,
            )
            return None

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
            not isinstance(key, str) or not isinstance(value, bool) for key, value in raw.items()
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

    def _write_state_on_startup(self, state: dict[str, bool]) -> None:
        try:
            self._write_state(state)
        except OSError as error:
            _LOGGER.warning(
                "Could not persist repaired Skill installation state (%s)",
                type(error).__name__,
            )

    def _stage_catalog_skill(self, spec: CuratedSkillSpec) -> Path:
        instructions = _download_pinned_skill(spec)
        staging_root = Path(tempfile.mkdtemp(prefix=".install-", dir=self._root))
        staged = staging_root / spec.entry.extension_id
        staged.mkdir()
        (staged / "SKILL.md").write_bytes(instructions)
        digest = package_content_digest(staged)
        manifest = {
            "schema_version": 1,
            "name": spec.entry.extension_id,
            "version": spec.entry.version,
            "description": spec.entry.description,
            "instructions": "SKILL.md",
            "input_schema": dict(spec.input_schema),
            "required_capabilities": spec.required_capabilities,
            "compatible_mcp_servers": spec.compatible_mcp_servers,
            "provenance": {
                "publisher": spec.entry.publisher,
                "source": spec.entry.source,
                "license": spec.entry.license,
            },
            "content_sha256": digest,
        }
        (staged / "fairy-skill.json").write_text(
            json.dumps(manifest, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return staged


def _curated_skill(extension_id: str) -> CuratedSkillSpec:
    try:
        return _CURATED_SKILLS[extension_id]
    except KeyError as error:
        raise KeyError(f"Unknown Skill catalog entry: {extension_id}") from error


def _download_pinned_skill(spec: CuratedSkillSpec) -> bytes:
    request = urllib.request.Request(spec.source_url, headers={"User-Agent": "Fairy-V3"})
    with urllib.request.urlopen(request, timeout=20) as response:
        content = response.read(_MAX_DOWNLOAD_BYTES + 1)
    if len(content) > _MAX_DOWNLOAD_BYTES:
        raise ValueError("Skill package download is too large")
    if hashlib.sha256(content).hexdigest() != spec.source_sha256:
        raise ValueError("Skill source digest does not match the curated catalog")
    return content


def _write_skill_manifest(
    root: Path,
    *,
    name: str,
    version: str,
    description: str,
    publisher: str,
    source: str,
    license_name: str,
    input_schema: Mapping[str, object],
    required_capabilities: tuple[str, ...],
    compatible_mcp_servers: tuple[str, ...],
) -> None:
    schema = dict(input_schema) or {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    digest = package_content_digest(root)
    manifest = {
        "schema_version": 1,
        "name": name,
        "version": version,
        "description": description,
        "instructions": "SKILL.md",
        "input_schema": schema,
        "required_capabilities": required_capabilities,
        "compatible_mcp_servers": compatible_mcp_servers,
        "provenance": {
            "publisher": publisher,
            "source": source,
            "license": license_name,
        },
        "content_sha256": digest,
    }
    (root / "fairy-skill.json").write_text(
        json.dumps(manifest, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _copy_skill_tree(source: Path, destination: Path) -> None:
    try:
        root = source.resolve(strict=True)
    except OSError as error:
        raise SkillPackageError("Skill source could not be accessed") from error
    if (
        not root.is_dir()
        or root.is_symlink()
        or (hasattr(root, "is_junction") and root.is_junction())
    ):
        raise SkillPackageError("Skill source must be a regular directory")
    counters = [0, 0]
    _copy_skill_directory(root, root, destination, counters)


def _copy_skill_directory(root: Path, source: Path, destination: Path, counters: list[int]) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    try:
        entries = sorted(os.scandir(source), key=lambda entry: entry.name.casefold())
    except OSError as error:
        raise SkillPackageError("Skill source could not be enumerated") from error
    for entry in entries:
        source_path = Path(entry.path)
        if source_path.is_symlink() or (
            hasattr(source_path, "is_junction") and source_path.is_junction()
        ):
            raise SkillPackageError("Skill sources cannot contain links")
        target = destination / entry.name
        if entry.is_dir(follow_symlinks=False):
            _copy_skill_directory(root, source_path, target, counters)
            continue
        if not entry.is_file(follow_symlinks=False):
            raise SkillPackageError("Skill sources can contain regular files only")
        before = source_path.resolve(strict=True)
        if not before.is_relative_to(root):
            raise SkillPackageError("Skill source file escaped its root")
        content = source_path.read_bytes()
        after = source_path.resolve(strict=True)
        if before != after or not after.is_relative_to(root):
            raise SkillPackageError("Skill source changed during import")
        counters[0] += 1
        counters[1] += len(content)
        if counters[0] > _MAX_PACKAGE_FILES or counters[1] > _MAX_PACKAGE_BYTES:
            raise SkillPackageError("Skill source exceeds package limits")
        target.write_bytes(content)


def _extract_skill_archive(source: Path, destination: Path) -> None:
    try:
        archive = source.resolve(strict=True)
    except OSError as error:
        raise SkillPackageError("Skill archive could not be accessed") from error
    if not archive.is_file() or archive.suffix.casefold() != ".zip":
        raise SkillPackageError("Skill archive must be a ZIP file")
    with zipfile.ZipFile(archive) as package:
        _extract_zip_members(package, destination)


def _extract_zip_members(
    package: zipfile.ZipFile,
    destination: Path,
    *,
    strip_prefix: tuple[str, ...] = (),
) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    file_count = 0
    content_bytes = 0
    normalized_targets: set[str] = set()
    for member in package.infolist():
        raw_parts = tuple(part for part in Path(member.filename).parts if part not in {"", "."})
        if not raw_parts or raw_parts[: len(strip_prefix)] != strip_prefix:
            continue
        parts = raw_parts[len(strip_prefix) :]
        if not parts:
            continue
        if (
            any(not _safe_archive_path_part(part) for part in parts)
            or Path(member.filename).is_absolute()
        ):
            raise SkillPackageError("Skill archive contains an unsafe path")
        mode = member.external_attr >> 16
        if (mode & 0o170000) == 0o120000:
            raise SkillPackageError("Skill archive cannot contain links")
        target = destination.joinpath(*parts)
        if not target.resolve(strict=False).is_relative_to(destination.resolve(strict=True)):
            raise SkillPackageError("Skill archive escaped its destination")
        normalized_target = str(target.relative_to(destination)).replace("\\", "/").casefold()
        if normalized_target in normalized_targets:
            raise SkillPackageError("Skill archive contains duplicate paths")
        normalized_targets.add(normalized_target)
        if member.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        file_count += 1
        if file_count > _MAX_PACKAGE_FILES or member.file_size > _MAX_PACKAGE_BYTES - content_bytes:
            raise SkillPackageError("Skill archive exceeds package limits")
        target.parent.mkdir(parents=True, exist_ok=True)
        with package.open(member) as source_handle, target.open("wb") as target_handle:
            while True:
                remaining = _MAX_PACKAGE_BYTES - content_bytes
                chunk = source_handle.read(min(64 * 1024, remaining + 1))
                if not chunk:
                    break
                if len(chunk) > remaining:
                    raise SkillPackageError("Skill archive exceeds package limits")
                target_handle.write(chunk)
                content_bytes += len(chunk)
    if file_count == 0:
        raise SkillPackageError("Skill archive contains no package files")


def _safe_archive_path_part(part: str) -> bool:
    if part in {"", ".", ".."} or "\x00" in part or ":" in part:
        return False
    if part.rstrip(" .") != part:
        return False
    return part.split(".", 1)[0].casefold() not in _WINDOWS_RESERVED_PATH_NAMES


def _download_github_skill(source: str, destination: Path) -> None:
    parsed = urlsplit(source)
    if parsed.scheme != "https" or parsed.hostname not in {"github.com", "www.github.com"}:
        raise SkillPackageError("Skill Git source must be an HTTPS GitHub URL")
    parts = tuple(unquote(part) for part in parsed.path.strip("/").split("/") if part)
    if len(parts) < 2 or any(_GITHUB_SEGMENT.fullmatch(part) is None for part in parts[:2]):
        raise SkillPackageError("GitHub Skill URL is invalid")
    owner, repository = parts[:2]
    repository = repository.removesuffix(".git")
    branch = "main"
    package_path: tuple[str, ...] = ()
    if len(parts) > 2:
        if len(parts) < 4 or parts[2] != "tree" or _GITHUB_SEGMENT.fullmatch(parts[3]) is None:
            raise SkillPackageError("GitHub Skill URL must point to a repository or folder")
        branch = parts[3]
        package_path = parts[4:]
    if any(part in {".", ".."} or _GITHUB_SEGMENT.fullmatch(part) is None for part in package_path):
        raise SkillPackageError("GitHub Skill folder is invalid")
    archive_url = f"https://codeload.github.com/{owner}/{repository}/zip/refs/heads/{branch}"
    request = urllib.request.Request(archive_url, headers={"User-Agent": "Fairy-V3"})
    with urllib.request.urlopen(request, timeout=30) as response:
        archive_bytes = response.read(_MAX_ARCHIVE_BYTES + 1)
    if len(archive_bytes) > _MAX_ARCHIVE_BYTES:
        raise SkillPackageError("GitHub Skill archive is too large")
    archive_path = destination.parent / "github.zip"
    archive_path.write_bytes(archive_bytes)
    try:
        with zipfile.ZipFile(archive_path) as package:
            roots = {
                Path(member.filename).parts[0]
                for member in package.infolist()
                if Path(member.filename).parts
            }
            if len(roots) != 1:
                raise SkillPackageError("GitHub Skill archive root is ambiguous")
            _extract_zip_members(
                package,
                destination,
                strip_prefix=(next(iter(roots)), *package_path),
            )
    finally:
        archive_path.unlink(missing_ok=True)


def _locate_skill_root(payload: Path) -> Path:
    if (payload / "SKILL.md").is_file():
        return payload
    matches = [path.parent for path in payload.rglob("SKILL.md")]
    unique = tuple(dict.fromkeys(matches))
    if len(unique) != 1:
        raise SkillPackageError("Skill source must contain exactly one package root")
    return unique[0]


def _skill_provenance_source(source_kind: str, source: str) -> str:
    if source_kind == "github":
        return source[:500]
    name = Path(source).name[:200] or "external"
    return f"{source_kind}://{name}"


__all__ = [
    "CONTEXT7_ENTRY",
    "GITHUB_MCP_ENTRY",
    "GSAP_SKILL_SPECS",
    "PLAYWRIGHT_MCP_ENTRY",
    "TASTE_SKILL_ENTRY",
    "ExtensionCatalogEntry",
    "McpPresetSpec",
    "SkillManager",
]
