from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

SOURCE_ROOTS = (
    Path("core/src"),
    Path("capabilities/src"),
    Path("cloud/src"),
    Path("desktop/src"),
    Path("desktop/src-tauri/crates"),
)
PYTHON_SUFFIXES = {".py"}
SCRIPT_SUFFIXES = {".cjs", ".js", ".jsx", ".mjs", ".rs", ".ts", ".tsx"}
MANIFEST_NAMES = {"Cargo.toml", "package.json", "pyproject.toml"}
OWNED_SOURCE_SUFFIXES = PYTHON_SUFFIXES | SCRIPT_SUFFIXES | {".css", ".json", ".toml"}
FORBIDDEN_PYTHON_ROOTS = {
    "app",
    "fairy_desktop",
    "lazy_runtime",
    "legacy_surface",
    "skills",
}
FORBIDDEN_LAYER_IMPORTS = {
    Path("core/src"): (
        ("fairy_cloud", "Core cannot import Cloud adapters"),
        ("fairy_capabilities", "Core cannot import Capability adapters"),
    ),
    Path("capabilities/src"): (
        ("fairy_cloud", "Capabilities cannot import Cloud composition"),
    ),
    Path("cloud/src"): (
        ("fairy_core.transports", "Cloud cannot compose through Core transports"),
        ("fairy_core.commanding.sqlite", "Cloud cannot use local SQLite adapters"),
        ("fairy_core.persistence.sqlite", "Cloud cannot use local SQLite adapters"),
        ("fairy_core.storage.sqlite", "Cloud cannot use local SQLite adapters"),
    ),
}
SCRIPT_SPECIFIER = re.compile(
    r"(?:\bfrom\s+|\bimport\s*\(|\brequire\s*\()\s*['\"]([^'\"]+)['\"]"
)
MANIFEST_PATH = re.compile(r"\bpath\s*=\s*['\"]([^'\"]+)['\"]")
SECRET_LITERAL = re.compile(
    r"(?:sk-or-v1-|sk-proj-|AKIA|AIza)[A-Za-z0-9_-]{16,}",
)
BROWSER_SPEECH = re.compile(r"\b(?:speechSynthesis|SpeechSynthesisUtterance)\b")
KEYWORD_ROUTER = re.compile(r"\bkeyword(?:_?intent)?_?router\b", re.IGNORECASE)
HOST_SHELL = re.compile(
    r"\b(?:create_subprocess_shell|os\.system|shell\s*=\s*True)\b"
    r"|\bsubprocess\.(?:run|Popen|call|check_call|check_output)\s*\("
    r"[^)]*['\"](?:powershell|cmd(?:\.exe)?|bash|sh)['\"]"
    r"|\bCommand::new\(\s*['\"](?:powershell|cmd(?:\.exe)?|bash|sh)['\"]",
    re.IGNORECASE | re.DOTALL,
)
DUPLICATE_MEMORY_ROOTS = {"chromadb", "faiss", "pinecone", "qdrant_client", "weaviate"}
FORBIDDEN_RENDERER_IMPORTS = {
    "@tauri-apps/api/fs",
    "@tauri-apps/api/process",
    "@tauri-apps/api/shell",
    "@tauri-apps/plugin-fs",
    "@tauri-apps/plugin-opener",
    "@tauri-apps/plugin-process",
    "@tauri-apps/plugin-shell",
    "child_process",
    "fs",
    "fs/promises",
    "node:child_process",
    "node:fs",
    "node:fs/promises",
    "node:process",
    "process",
}
PROJECT_EXECUTION_SERVICES = {"execution", "runtime", "worker"}
SOURCE_LINE_LIMIT = 1_200
CSS_LINE_LIMIT = 1_500
IGNORED_DIRECTORY_NAMES = {
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "dist",
    "node_modules",
    "target",
}


@dataclass(frozen=True, slots=True)
class Violation:
    path: Path
    line: int
    message: str


def _outside_root(*, source: Path, specifier: str, root: Path) -> bool:
    if not specifier.startswith((".", "/", "\\")) and not Path(specifier).is_absolute():
        return False
    candidate = (source.parent / specifier).resolve(strict=False)
    return not candidate.is_relative_to(root)


def _check_python(path: Path, root: Path) -> list[Violation]:
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as error:
        return [
            Violation(path, error.lineno or 1, f"invalid Python syntax: {error.msg}")
        ]

    violations: list[Violation] = []
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                continue
            if node.module:
                modules.append(node.module)
        for module in modules:
            if module.split(".", 1)[0] in FORBIDDEN_PYTHON_ROOTS:
                violations.append(
                    Violation(
                        path, node.lineno, f"forbidden legacy Python import: {module}"
                    )
                )
            layer_message = _forbidden_layer_message(path, module, root)
            if layer_message is not None:
                violations.append(
                    Violation(path, node.lineno, f"{layer_message}: {module}")
                )
            if module.split(".", 1)[0] in DUPLICATE_MEMORY_ROOTS:
                violations.append(
                    Violation(
                        path,
                        node.lineno,
                        f"duplicate memory authority dependency: {module}",
                    )
                )

    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        value = node.value.strip()
        if value and _outside_root(source=path, specifier=value, root=root):
            normalized = value.replace("\\", "/").lower()
            if any(
                segment in normalized
                for segment in ("/app/", "/fairy-desktop/")
            ):
                violations.append(
                    Violation(
                        path, getattr(node, "lineno", 1), f"legacy path escape: {value}"
                    )
                )
    return violations


def _is_test_source(path: Path) -> bool:
    return (
        "tests" in path.parts
        or path.name.startswith("test_")
        or ".test." in path.name
        or ".spec." in path.name
    )


def _under_ignored_directory(path: Path, source_root: Path) -> bool:
    relative = path.relative_to(source_root)
    return any(part in IGNORED_DIRECTORY_NAMES for part in relative.parts)


def _check_release_safety(path: Path) -> list[Violation]:
    if _is_test_source(path):
        return []
    source = path.read_text(encoding="utf-8")
    rules = (
        (HOST_SHELL, "host shell execution is forbidden"),
        (BROWSER_SPEECH, "browser speech synthesis is forbidden"),
        (KEYWORD_ROUTER, "keyword routing is forbidden"),
        (SECRET_LITERAL, "credential-shaped literal is forbidden"),
    )
    violations: list[Violation] = []
    for pattern, message in rules:
        for match in pattern.finditer(source):
            violations.append(
                Violation(path, _line_number(source, match.start()), message)
            )
    return violations


def _check_module_size(path: Path) -> list[Violation]:
    if "generated" in path.parts or _is_test_source(path):
        return []
    limit = CSS_LINE_LIMIT if path.suffix == ".css" else SOURCE_LINE_LIMIT
    line_count = len(path.read_text(encoding="utf-8").splitlines())
    if line_count <= limit:
        return []
    return [
        Violation(
            path,
            limit + 1,
            f"source module exceeds {limit} lines ({line_count})",
        )
    ]


def _forbidden_layer_message(path: Path, module: str, root: Path) -> str | None:
    relative_path = path.relative_to(root)
    for source_root, rules in FORBIDDEN_LAYER_IMPORTS.items():
        if not relative_path.is_relative_to(source_root):
            continue
        for prefix, message in rules:
            if module == prefix or module.startswith(f"{prefix}."):
                return message
    return None


def _line_number(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def _check_script(path: Path, root: Path, *, renderer: bool) -> list[Violation]:
    source = path.read_text(encoding="utf-8")
    violations: list[Violation] = []
    for match in SCRIPT_SPECIFIER.finditer(source):
        specifier = match.group(1)
        line = _line_number(source, match.start(1))
        if _outside_root(source=path, specifier=specifier, root=root):
            violations.append(
                Violation(
                    path, line, f"import escapes the V3 product root: {specifier}"
                )
            )
        if renderer and specifier in FORBIDDEN_RENDERER_IMPORTS:
            violations.append(
                Violation(
                    path, line, f"renderer privileged API is forbidden: {specifier}"
                )
            )
    return violations


def _check_manifest(path: Path, root: Path) -> list[Violation]:
    source = path.read_text(encoding="utf-8")
    return [
        Violation(
            path,
            _line_number(source, match.start(1)),
            f"manifest path escapes the V3 product root: {match.group(1)}",
        )
        for match in MANIFEST_PATH.finditer(source)
        if _outside_root(source=path, specifier=match.group(1), root=root)
    ]


def _check_compose(path: Path) -> list[Violation]:
    source = path.read_text(encoding="utf-8")
    violations: list[Violation] = []
    if "docker.sock" in source.casefold():
        offset = source.casefold().index("docker.sock")
        violations.append(
            Violation(
                path, _line_number(source, offset), "Docker socket access is forbidden"
            )
        )
    try:
        document = yaml.safe_load(source)
    except yaml.YAMLError as error:
        return [Violation(path, 1, f"invalid Compose YAML: {error}")]
    if not isinstance(document, dict):
        return [Violation(path, 1, "Compose document must be an object")]
    services = document.get("services")
    if not isinstance(services, dict):
        return [Violation(path, 1, "Compose services must be an object")]
    for service_name, raw_service in services.items():
        if not isinstance(service_name, str) or not isinstance(raw_service, dict):
            violations.append(
                Violation(path, 1, "Compose service entries must be objects")
            )
            continue
        if raw_service.get("privileged") is True:
            violations.append(
                Violation(
                    path, 1, f"privileged Compose service is forbidden: {service_name}"
                )
            )
        for host_namespace in ("ipc", "network_mode", "pid"):
            if str(raw_service.get(host_namespace, "")).casefold() == "host":
                violations.append(
                    Violation(
                        path,
                        1,
                        f"host {host_namespace} is forbidden for Compose service: {service_name}",
                    )
                )
        if raw_service.get("cap_add"):
            violations.append(
                Violation(
                    path, 1, f"added Linux capabilities are forbidden: {service_name}"
                )
            )
        if service_name not in PROJECT_EXECUTION_SERVICES:
            continue
        volumes = raw_service.get("volumes") or []
        if not isinstance(volumes, list):
            violations.append(
                Violation(
                    path, 1, f"Compose service volumes must be an array: {service_name}"
                )
            )
            continue
        for volume in volumes:
            if _is_host_bind(volume):
                violations.append(
                    Violation(
                        path,
                        1,
                        f"project execution service has a host bind mount: {service_name}",
                    )
                )
    return violations


def _is_host_bind(volume: object) -> bool:
    if isinstance(volume, dict):
        if str(volume.get("type", "")).casefold() == "bind":
            return True
        source = volume.get("source")
        return isinstance(source, str) and _looks_like_host_path(source)
    if not isinstance(volume, str):
        return True
    return _looks_like_host_path(volume)


def _looks_like_host_path(value: str) -> bool:
    normalized = value.strip().replace("\\", "/")
    return normalized.startswith(("/", "./", "../", "~/")) or bool(
        re.match(r"^[A-Za-z]:/", normalized)
    )


def check_boundaries(root: Path) -> list[Violation]:
    resolved_root = root.resolve(strict=True)
    violations: list[Violation] = []
    for relative_source_root in SOURCE_ROOTS:
        source_root = resolved_root / relative_source_root
        if not source_root.is_dir():
            violations.append(
                Violation(source_root, 1, "required source root is missing")
            )
            continue
        for directory in sorted(
            item for item in source_root.rglob("*") if item.is_dir()
        ):
            if _under_ignored_directory(directory, source_root) or any(
                directory.iterdir()
            ):
                continue
            violations.append(
                Violation(directory, 1, "empty future-facing source directory")
            )
        for path in sorted(item for item in source_root.rglob("*") if item.is_file()):
            if _under_ignored_directory(path, source_root):
                continue
            if (
                path.suffix not in OWNED_SOURCE_SUFFIXES
                and path.name not in MANIFEST_NAMES
            ):
                violations.append(Violation(path, 1, "unowned source file type"))
                continue
            if path.suffix in PYTHON_SUFFIXES:
                violations.extend(_check_python(path, resolved_root))
            elif path.suffix in SCRIPT_SUFFIXES:
                violations.extend(
                    _check_script(
                        path,
                        resolved_root,
                        renderer=relative_source_root == Path("desktop/src"),
                    )
                )
            elif path.name in MANIFEST_NAMES:
                violations.extend(_check_manifest(path, resolved_root))
            if path.suffix in PYTHON_SUFFIXES | SCRIPT_SUFFIXES | {".css"}:
                violations.extend(_check_release_safety(path))
                violations.extend(_check_module_size(path))
    compose_path = resolved_root / "cloud" / "compose.yaml"
    if compose_path.is_file():
        violations.extend(_check_compose(compose_path))
    return violations


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parents[1]
    violations = check_boundaries(root)
    if not violations:
        print("V3 repository boundaries: OK")
        return 0
    for violation in violations:
        print(f"{violation.path}:{violation.line}: {violation.message}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
