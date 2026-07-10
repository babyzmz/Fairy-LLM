from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

SOURCE_ROOTS = (
    Path("core/src"),
    Path("cloud/src"),
    Path("desktop/src"),
    Path("desktop/src-tauri/crates"),
)
PYTHON_SUFFIXES = {".py"}
SCRIPT_SUFFIXES = {".cjs", ".js", ".jsx", ".mjs", ".ts", ".tsx"}
MANIFEST_NAMES = {"Cargo.toml", "package.json", "pyproject.toml"}
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
        return [Violation(path, error.lineno or 1, f"invalid Python syntax: {error.msg}")]

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
                    Violation(path, node.lineno, f"forbidden legacy Python import: {module}")
                )
            layer_message = _forbidden_layer_message(path, module, root)
            if layer_message is not None:
                violations.append(Violation(path, node.lineno, f"{layer_message}: {module}"))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        value = node.value.strip()
        if value and _outside_root(source=path, specifier=value, root=root):
            normalized = value.replace("\\", "/").lower()
            if any(
                segment in normalized
                for segment in ("/app/", "/fairy-desktop/", "/skills/")
            ):
                violations.append(
                    Violation(path, getattr(node, "lineno", 1), f"legacy path escape: {value}")
                )
    return violations


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


def _check_script(path: Path, root: Path) -> list[Violation]:
    source = path.read_text(encoding="utf-8")
    return [
        Violation(
            path,
            _line_number(source, match.start(1)),
            f"import escapes the V3 product root: {match.group(1)}",
        )
        for match in SCRIPT_SPECIFIER.finditer(source)
        if _outside_root(source=path, specifier=match.group(1), root=root)
    ]


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


def check_boundaries(root: Path) -> list[Violation]:
    resolved_root = root.resolve(strict=True)
    violations: list[Violation] = []
    for relative_source_root in SOURCE_ROOTS:
        source_root = resolved_root / relative_source_root
        if not source_root.is_dir():
            violations.append(Violation(source_root, 1, "required source root is missing"))
            continue
        for path in sorted(item for item in source_root.rglob("*") if item.is_file()):
            if path.suffix in PYTHON_SUFFIXES:
                violations.extend(_check_python(path, resolved_root))
            elif path.suffix in SCRIPT_SUFFIXES:
                violations.extend(_check_script(path, resolved_root))
            elif path.name in MANIFEST_NAMES:
                violations.extend(_check_manifest(path, resolved_root))
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
