from __future__ import annotations

import json
import re
import stat
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from fairy_core.domain.execution import RuntimeKind
from fairy_core.execution.templates import (
    DependencyManager,
    UnknownProjectManagerError,
    dependency_layer_key,
    dependency_template,
)

PORT_TOKEN = "{port}"
_MAX_CONTROL_FILE_BYTES = 2 * 1024 * 1024
_ASGI_ENTRY = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*"
    r":[A-Za-z_][A-Za-z0-9_]*$"
)
_READINESS_PATH = re.compile(r"^/(?:[A-Za-z0-9._~-]+/)*[A-Za-z0-9._~-]*$")


class RuntimeTemplateError(ValueError):
    error_code = "CAPABILITY_NOT_AVAILABLE"


class RuntimeAdapter(StrEnum):
    STATIC = "static"
    VITE = "vite"
    NEXT = "next"
    ASTRO = "astro"
    PYTHON_ASGI = "python_asgi"


@dataclass(frozen=True, slots=True)
class RuntimeTemplate:
    kind: RuntimeKind
    adapter: RuntimeAdapter
    argv: tuple[str, ...]
    cwd: str
    entry_path: str | None
    readiness_path: str
    startup_timeout_seconds: int
    dependency_key: str | None

    def __post_init__(self) -> None:
        if self.cwd != ".":
            raise ValueError("Runtime template cwd must be the project root")
        if _READINESS_PATH.fullmatch(self.readiness_path) is None:
            raise ValueError("Runtime readiness path is invalid")
        if not 1 <= self.startup_timeout_seconds <= 120:
            raise ValueError("Runtime startup timeout is invalid")
        if self.adapter is RuntimeAdapter.STATIC:
            if (
                self.kind is not RuntimeKind.STATIC_SITE
                or self.argv
                or self.entry_path != "index.html"
                or self.dependency_key is not None
            ):
                raise ValueError("static Runtime template is inconsistent")
            return
        if self.kind not in {RuntimeKind.WSL_PROJECT, RuntimeKind.CLOUD_OCI}:
            raise ValueError("dynamic Runtime kind is invalid")
        if self.entry_path is not None:
            raise ValueError("dynamic Runtime cannot declare a static entry")
        if len(self.dependency_key or "") != 64:
            raise ValueError("dynamic Runtime requires a dependency key")
        if sum(value.count(PORT_TOKEN) for value in self.argv) != 1:
            raise ValueError("dynamic Runtime argv requires one Core port token")
        if "127.0.0.1" not in self.argv:
            raise ValueError("dynamic Runtime must bind exact loopback")


def select_runtime_template(
    project_root: Path,
    *,
    execution_target: str,
) -> RuntimeTemplate:
    if execution_target not in {"local", "cloud"}:
        raise RuntimeTemplateError("execution target is invalid")
    root = _project_root(project_root)
    manifest = _optional_json_object(root / "fairy.runtime.json")
    try:
        dependency = dependency_template(root)
    except UnknownProjectManagerError:
        if manifest is not None:
            raise RuntimeTemplateError(
                "Runtime manifest requires a locked project manager"
            ) from None
        return _static_template(root, execution_target=execution_target)

    dependency_key = dependency_layer_key(root, dependency.manager)
    kind = RuntimeKind.WSL_PROJECT if execution_target == "local" else RuntimeKind.CLOUD_OCI
    if manifest is not None:
        return _manifest_template(
            manifest,
            manager=dependency.manager,
            kind=kind,
            dependency_key=dependency_key,
        )
    if dependency.manager not in {
        DependencyManager.NPM,
        DependencyManager.PNPM,
        DependencyManager.YARN,
    }:
        raise RuntimeTemplateError("dynamic Runtime requires a strict Runtime manifest")
    package = _json_object(root / "package.json")
    declared = _declared_node_adapters(package)
    if len(declared) != 1:
        qualifier = "multiple" if declared else "no supported"
        raise RuntimeTemplateError(f"{qualifier} dynamic Runtime adapters were detected")
    adapter = declared[0]
    return RuntimeTemplate(
        kind=kind,
        adapter=adapter,
        argv=_node_argv(adapter),
        cwd=".",
        entry_path=None,
        readiness_path="/",
        startup_timeout_seconds=45,
        dependency_key=dependency_key,
    )


def _static_template(root: Path, *, execution_target: str) -> RuntimeTemplate:
    if execution_target != "local":
        raise RuntimeTemplateError("cloud static Preview requires a cloud artifact adapter")
    if not _regular_file(root / "index.html", required=False):
        raise RuntimeTemplateError("no supported static or dynamic Runtime was detected")
    return RuntimeTemplate(
        kind=RuntimeKind.STATIC_SITE,
        adapter=RuntimeAdapter.STATIC,
        argv=(),
        cwd=".",
        entry_path="index.html",
        readiness_path="/",
        startup_timeout_seconds=10,
        dependency_key=None,
    )


def _manifest_template(
    values: dict[str, object],
    *,
    manager: DependencyManager,
    kind: RuntimeKind,
    dependency_key: str,
) -> RuntimeTemplate:
    allowed = {"schema_version", "adapter", "entry", "readiness_path"}
    if set(values) - allowed or values.get("schema_version") != 1:
        raise RuntimeTemplateError("Runtime manifest schema is invalid")
    if values.get("adapter") != RuntimeAdapter.PYTHON_ASGI.value:
        raise RuntimeTemplateError("Runtime manifest adapter is unsupported")
    if manager is not DependencyManager.UV:
        raise RuntimeTemplateError("python_asgi currently requires a uv lockfile")
    entry = values.get("entry")
    if not isinstance(entry, str) or _ASGI_ENTRY.fullmatch(entry) is None:
        raise RuntimeTemplateError("Runtime manifest entry is invalid")
    readiness_path = values.get("readiness_path", "/")
    if (
        not isinstance(readiness_path, str)
        or _READINESS_PATH.fullmatch(readiness_path) is None
        or ".." in readiness_path.split("/")
    ):
        raise RuntimeTemplateError("Runtime manifest readiness path is invalid")
    return RuntimeTemplate(
        kind=kind,
        adapter=RuntimeAdapter.PYTHON_ASGI,
        argv=(
            ".venv/bin/python",
            "-m",
            "uvicorn",
            entry,
            "--host",
            "127.0.0.1",
            "--port",
            PORT_TOKEN,
            "--no-access-log",
        ),
        cwd=".",
        entry_path=None,
        readiness_path=readiness_path,
        startup_timeout_seconds=45,
        dependency_key=dependency_key,
    )


def _declared_node_adapters(values: dict[str, object]) -> tuple[RuntimeAdapter, ...]:
    packages: set[str] = set()
    for key in ("dependencies", "devDependencies"):
        declared = values.get(key, {})
        if not isinstance(declared, dict) or any(
            not isinstance(name, str) or not isinstance(version, str)
            for name, version in declared.items()
        ):
            raise RuntimeTemplateError(f"package.json {key} is invalid")
        packages.update(declared)
    return tuple(
        adapter
        for package, adapter in (
            ("vite", RuntimeAdapter.VITE),
            ("next", RuntimeAdapter.NEXT),
            ("astro", RuntimeAdapter.ASTRO),
        )
        if package in packages
    )


def _node_argv(adapter: RuntimeAdapter) -> tuple[str, ...]:
    return {
        RuntimeAdapter.VITE: (
            "node_modules/.bin/vite",
            "--host",
            "127.0.0.1",
            "--port",
            PORT_TOKEN,
            "--strictPort",
        ),
        RuntimeAdapter.NEXT: (
            "node_modules/.bin/next",
            "dev",
            "-H",
            "127.0.0.1",
            "-p",
            PORT_TOKEN,
        ),
        RuntimeAdapter.ASTRO: (
            "node_modules/.bin/astro",
            "dev",
            "--host",
            "127.0.0.1",
            "--port",
            PORT_TOKEN,
        ),
    }[adapter]


def _project_root(value: Path) -> Path:
    try:
        root = Path(value).resolve(strict=True)
    except OSError as error:
        raise RuntimeTemplateError("project root is unavailable") from error
    if not root.is_dir():
        raise RuntimeTemplateError("project root must be a directory")
    return root


def _optional_json_object(path: Path) -> dict[str, object] | None:
    if not _regular_file(path, required=False):
        return None
    return _json_object(path)


def _json_object(path: Path) -> dict[str, object]:
    try:
        value = json.loads(_read_bytes(path).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeTemplateError("Runtime control file is invalid JSON") from error
    if not isinstance(value, dict):
        raise RuntimeTemplateError("Runtime control file schema must be an object")
    return value


def _read_bytes(path: Path) -> bytes:
    _regular_file(path, required=True)
    try:
        return path.read_bytes()
    except OSError as error:
        raise RuntimeTemplateError(f"Runtime control file is unavailable: {path.name}") from error


def _regular_file(path: Path, *, required: bool) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        if required:
            raise RuntimeTemplateError(f"Runtime control file is missing: {path.name}") from None
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or getattr(metadata, "st_file_attributes", 0) & reparse_flag
    ):
        raise RuntimeTemplateError(f"Runtime control file is not a regular file: {path.name}")
    if metadata.st_size > _MAX_CONTROL_FILE_BYTES:
        raise RuntimeTemplateError(f"Runtime control file is too large: {path.name}")
    return True


__all__ = [
    "PORT_TOKEN",
    "RuntimeAdapter",
    "RuntimeTemplate",
    "RuntimeTemplateError",
    "select_runtime_template",
]
