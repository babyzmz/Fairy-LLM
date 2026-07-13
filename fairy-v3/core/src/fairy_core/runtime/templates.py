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
_SERVICE_ID = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_ENTRY_PATH = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")


class RuntimeTemplateError(ValueError):
    error_code = "CAPABILITY_NOT_AVAILABLE"


class RuntimeAdapter(StrEnum):
    STATIC = "static"
    VITE = "vite"
    NEXT = "next"
    ASTRO = "astro"
    PYTHON_ASGI = "python_asgi"
    NODE_HTTP = "node_http"


@dataclass(frozen=True, slots=True)
class RuntimeServiceTemplate:
    service_id: str
    adapter: RuntimeAdapter
    argv: tuple[str, ...]
    cwd: str
    readiness_path: str
    startup_timeout_seconds: int
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "argv", tuple(self.argv))
        object.__setattr__(self, "depends_on", tuple(self.depends_on))
        if _SERVICE_ID.fullmatch(self.service_id) is None:
            raise ValueError("Runtime service id is invalid")
        if self.cwd != "." and (
            self.cwd.startswith(("/", "\\"))
            or "\\" in self.cwd
            or any(part in {"", ".", ".."} for part in self.cwd.split("/"))
        ):
            raise ValueError("Runtime service cwd is invalid")
        if _READINESS_PATH.fullmatch(self.readiness_path) is None:
            raise ValueError("Runtime service readiness path is invalid")
        if not 1 <= self.startup_timeout_seconds <= 120:
            raise ValueError("Runtime service startup timeout is invalid")
        expected_tokens = 0 if self.adapter is RuntimeAdapter.NODE_HTTP else 1
        if sum(value.count(PORT_TOKEN) for value in self.argv) != expected_tokens:
            raise ValueError("Runtime service port binding is invalid")
        if self.adapter is not RuntimeAdapter.NODE_HTTP and "127.0.0.1" not in self.argv:
            raise ValueError("Runtime service must bind exact loopback")
        if len(set(self.depends_on)) != len(self.depends_on) or self.service_id in self.depends_on:
            raise ValueError("Runtime service dependencies are invalid")


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
    services: tuple[RuntimeServiceTemplate, ...] = ()
    public_service_id: str = "app"

    def __post_init__(self) -> None:
        if self.cwd != "." and (
            self.cwd.startswith(("/", "\\"))
            or "\\" in self.cwd
            or any(part in {"", ".", ".."} for part in self.cwd.split("/"))
        ):
            raise ValueError("Runtime template cwd is invalid")
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
        services = tuple(self.services) or (
            RuntimeServiceTemplate(
                service_id=self.public_service_id,
                adapter=self.adapter,
                argv=self.argv,
                cwd=self.cwd,
                readiness_path=self.readiness_path,
                startup_timeout_seconds=self.startup_timeout_seconds,
            ),
        )
        object.__setattr__(self, "services", services)
        if self.kind not in {RuntimeKind.WSL_PROJECT, RuntimeKind.CLOUD_OCI}:
            raise ValueError("dynamic Runtime kind is invalid")
        if self.entry_path is not None:
            raise ValueError("dynamic Runtime cannot declare a static entry")
        if len(self.dependency_key or "") != 64:
            raise ValueError("dynamic Runtime requires a dependency key")
        expected_tokens = 0 if self.adapter is RuntimeAdapter.NODE_HTTP else 1
        if sum(value.count(PORT_TOKEN) for value in self.argv) != expected_tokens:
            raise ValueError("dynamic Runtime argv has invalid Core port binding")
        if self.adapter is not RuntimeAdapter.NODE_HTTP and "127.0.0.1" not in self.argv:
            raise ValueError("dynamic Runtime must bind exact loopback")
        service_ids = {service.service_id for service in services}
        if not 1 <= len(services) <= 8 or len(service_ids) != len(services):
            raise ValueError("Runtime service graph size is invalid")
        if self.public_service_id not in service_ids:
            raise ValueError("Runtime public service is invalid")
        if any(set(service.depends_on) - service_ids for service in services):
            raise ValueError("Runtime service dependency is unknown")
        _topological_services(services)
        public = next(
            service for service in services if service.service_id == self.public_service_id
        )
        if (
            public.adapter is not self.adapter
            or public.argv != self.argv
            or public.cwd != self.cwd
            or public.readiness_path != self.readiness_path
            or public.startup_timeout_seconds != self.startup_timeout_seconds
        ):
            raise ValueError("Runtime public service metadata changed")


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
            root=root,
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
    root: Path,
    manager: DependencyManager,
    kind: RuntimeKind,
    dependency_key: str,
) -> RuntimeTemplate:
    if values.get("schema_version") == 2:
        return _graph_manifest_template(
            values,
            root=root,
            manager=manager,
            kind=kind,
            dependency_key=dependency_key,
        )
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


def _graph_manifest_template(
    values: dict[str, object],
    *,
    root: Path,
    manager: DependencyManager,
    kind: RuntimeKind,
    dependency_key: str,
) -> RuntimeTemplate:
    if set(values) - {"schema_version", "services", "public_service"}:
        raise RuntimeTemplateError("Runtime graph manifest schema is invalid")
    if manager not in {DependencyManager.NPM, DependencyManager.PNPM, DependencyManager.YARN}:
        raise RuntimeTemplateError("Runtime graph currently requires a Node lockfile")
    raw_services = values.get("services")
    if not isinstance(raw_services, list) or not 1 <= len(raw_services) <= 8:
        raise RuntimeTemplateError("Runtime graph services are invalid")
    services = tuple(_graph_service(value, root=root) for value in raw_services)
    public_service_id = values.get("public_service")
    if not isinstance(public_service_id, str):
        raise RuntimeTemplateError("Runtime graph public service is invalid")
    try:
        _topological_services(services)
        public = next(service for service in services if service.service_id == public_service_id)
    except (StopIteration, ValueError) as error:
        raise RuntimeTemplateError(str(error)) from error
    return RuntimeTemplate(
        kind=kind,
        adapter=public.adapter,
        argv=public.argv,
        cwd=public.cwd,
        entry_path=None,
        readiness_path=public.readiness_path,
        startup_timeout_seconds=public.startup_timeout_seconds,
        dependency_key=dependency_key,
        services=services,
        public_service_id=public_service_id,
    )


def _graph_service(value: object, *, root: Path) -> RuntimeServiceTemplate:
    if not isinstance(value, dict) or set(value) - {
        "id",
        "adapter",
        "cwd",
        "entry",
        "readiness_path",
        "startup_timeout_seconds",
        "depends_on",
    }:
        raise RuntimeTemplateError("Runtime graph service schema is invalid")
    service_id = value.get("id")
    adapter_value = value.get("adapter")
    cwd = value.get("cwd", ".")
    readiness_path = value.get("readiness_path", "/")
    timeout = value.get("startup_timeout_seconds", 45)
    depends_on = value.get("depends_on", [])
    if (
        not isinstance(service_id, str)
        or not isinstance(adapter_value, str)
        or not isinstance(cwd, str)
        or not isinstance(readiness_path, str)
        or isinstance(timeout, bool)
        or not isinstance(timeout, int)
        or not isinstance(depends_on, list)
        or any(not isinstance(item, str) for item in depends_on)
    ):
        raise RuntimeTemplateError("Runtime graph service values are invalid")
    try:
        adapter = RuntimeAdapter(adapter_value)
    except ValueError as error:
        raise RuntimeTemplateError("Runtime graph service adapter is unsupported") from error
    if adapter is RuntimeAdapter.STATIC or adapter is RuntimeAdapter.PYTHON_ASGI:
        raise RuntimeTemplateError("Runtime graph service adapter is unsupported")
    service_root = (root / cwd).resolve(strict=False)
    if not service_root.is_relative_to(root) or not _regular_directory(service_root):
        raise RuntimeTemplateError("Runtime graph service cwd is unavailable")
    if adapter is RuntimeAdapter.NODE_HTTP:
        entry = value.get("entry")
        if not isinstance(entry, str) or _ENTRY_PATH.fullmatch(entry) is None:
            raise RuntimeTemplateError("node_http service entry is invalid")
        entry_path = (service_root / entry).resolve(strict=False)
        if not entry_path.is_relative_to(service_root) or not _regular_file(
            entry_path, required=False
        ):
            raise RuntimeTemplateError("node_http service entry is unavailable")
        argv = ("node", entry)
    else:
        if "entry" in value:
            raise RuntimeTemplateError("frontend Runtime service cannot declare entry")
        argv = _node_argv(adapter)
    try:
        return RuntimeServiceTemplate(
            service_id=service_id,
            adapter=adapter,
            argv=argv,
            cwd=cwd,
            readiness_path=readiness_path,
            startup_timeout_seconds=timeout,
            depends_on=tuple(depends_on),
        )
    except ValueError as error:
        raise RuntimeTemplateError(str(error)) from error


def _topological_services(
    services: tuple[RuntimeServiceTemplate, ...],
) -> tuple[RuntimeServiceTemplate, ...]:
    by_id = {service.service_id: service for service in services}
    if len(by_id) != len(services) or any(
        set(service.depends_on) - set(by_id) for service in services
    ):
        raise ValueError("Runtime service dependency is unknown")
    ordered: list[RuntimeServiceTemplate] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(service: RuntimeServiceTemplate) -> None:
        if service.service_id in visiting:
            raise ValueError("Runtime service graph contains a cycle")
        if service.service_id in visited:
            return
        visiting.add(service.service_id)
        for dependency in service.depends_on:
            visit(by_id[dependency])
        visiting.remove(service.service_id)
        visited.add(service.service_id)
        ordered.append(service)

    for service in services:
        visit(service)
    return tuple(ordered)


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


def _regular_directory(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(
        stat.S_ISDIR(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and not getattr(metadata, "st_file_attributes", 0) & reparse_flag
    )


__all__ = [
    "PORT_TOKEN",
    "RuntimeAdapter",
    "RuntimeServiceTemplate",
    "RuntimeTemplate",
    "RuntimeTemplateError",
    "select_runtime_template",
]
