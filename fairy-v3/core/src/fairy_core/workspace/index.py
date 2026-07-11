from __future__ import annotations

import ast
import hashlib
import json
import os
import stat
import tomllib
from pathlib import Path
from typing import Any
from uuid import UUID

from fairy_core.workspace.models import ProjectFile, ProjectIndex

_MAX_FILES = 20_000
_MAX_SEMANTIC_BYTES = 1_000_000
_MAX_FILE_BYTES = 64_000_000
_IGNORED_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".idea",
        ".svn",
        ".venv",
        ".vscode",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "target",
        "venv",
    }
)
_SECRET_NAMES = frozenset(
    {
        "credentials",
        "credentials.json",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa",
        "secrets.json",
    }
)
_SECRET_SUFFIXES = (".key", ".p12", ".pem", ".pfx")
_LANGUAGES = {
    ".c": "c",
    ".cpp": "cpp",
    ".css": "css",
    ".go": "go",
    ".html": "html",
    ".js": "javascript",
    ".jsx": "javascript",
    ".md": "markdown",
    ".py": "python",
    ".rs": "rust",
    ".ts": "typescript",
    ".tsx": "typescript",
}


class ProjectIndexer:
    def build(
        self,
        *,
        project_id: UUID,
        version_id: UUID,
        root: Path,
        generation: int,
    ) -> ProjectIndex:
        canonical_root = root.resolve(strict=True)
        if not canonical_root.is_dir():
            raise ValueError("Project Index root must be a directory")
        files: list[ProjectFile] = []
        for path in sorted(canonical_root.rglob("*"), key=lambda item: item.as_posix()):
            relative = path.relative_to(canonical_root).as_posix()
            if self._ignored(relative) or self._reparse_point(path):
                continue
            if not path.is_file() or not self._within(path, canonical_root):
                continue
            files.append(self._index_file(path, relative))
            if len(files) > _MAX_FILES:
                raise ValueError("Project Index exceeds the file limit")
        digest = hashlib.sha256()
        for item in files:
            digest.update(item.path.encode("utf-8"))
            digest.update(b"\0")
            digest.update(item.content_hash.encode("ascii"))
            digest.update(b"\0")
            digest.update(item.kind.encode("ascii"))
            digest.update(b"\n")
        return ProjectIndex(
            project_id=project_id,
            version_id=version_id,
            generation=generation,
            source_hash=digest.hexdigest(),
            files=tuple(files),
        )

    def _index_file(self, path: Path, relative: str) -> ProjectFile:
        size = path.stat(follow_symlinks=False).st_size
        if size > _MAX_FILE_BYTES:
            return ProjectFile(
                path=relative,
                byte_length=size,
                content_hash=self._hash_file(path),
                kind="oversized",
            )
        data = path.read_bytes()
        content_hash = hashlib.sha256(data).hexdigest()
        if size > _MAX_SEMANTIC_BYTES:
            return ProjectFile(
                path=relative,
                byte_length=size,
                content_hash=content_hash,
                kind="oversized",
            )
        if b"\0" in data:
            return ProjectFile(
                path=relative,
                byte_length=size,
                content_hash=content_hash,
                kind="binary",
            )
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return ProjectFile(
                path=relative,
                byte_length=size,
                content_hash=content_hash,
                kind="binary",
            )
        kind, language, imports, exports, symbols, summary = self._semantics(relative, text)
        return ProjectFile(
            path=relative,
            byte_length=size,
            content_hash=content_hash,
            kind=kind,
            language=language,
            imports=imports,
            exports=exports,
            symbols=symbols,
            summary=summary,
        )

    @staticmethod
    def _semantics(
        relative: str,
        text: str,
    ) -> tuple[str, str | None, tuple[str, ...], tuple[str, ...], tuple[str, ...], dict[str, Any]]:
        path = Path(relative)
        suffix = path.suffix.casefold()
        language = _LANGUAGES.get(suffix)
        name = path.name.casefold()
        if name == "package.json":
            return "manifest", "json", (), (), (), _package_json_summary(text)
        if name == "pyproject.toml":
            return "manifest", "toml", (), (), (), _pyproject_summary(text)
        if name == "cargo.toml":
            return "manifest", "toml", (), (), (), _cargo_summary(text)
        if suffix == ".json":
            return "config", "json", (), (), (), _json_summary(text)
        if suffix == ".toml":
            return "config", "toml", (), (), (), _toml_summary(text)
        if suffix == ".py":
            imports, exports, symbols = _python_semantics(text)
            return "source", language, imports, exports, symbols, {}
        if suffix in {".ts", ".tsx", ".js", ".jsx"}:
            imports, exports, symbols = _typescript_semantics(text)
            return "source", language, imports, exports, symbols, {}
        if suffix == ".rs":
            imports, exports, symbols = _rust_semantics(text)
            return "source", language, imports, exports, symbols, {}
        return ("source" if language else "text"), language, (), (), (), {}

    @staticmethod
    def _ignored(relative: str) -> bool:
        parts = tuple(part.casefold() for part in relative.split("/"))
        name = parts[-1]
        return (
            any(part in _IGNORED_DIRECTORIES for part in parts[:-1])
            or name == ".env"
            or name.startswith(".env.")
            or name in _SECRET_NAMES
            or name.endswith(_SECRET_SUFFIXES)
        )

    @staticmethod
    def _within(path: Path, root: Path) -> bool:
        try:
            return os.path.commonpath(
                (os.path.normcase(str(path.resolve(strict=True))), os.path.normcase(str(root)))
            ) == os.path.normcase(str(root))
        except (OSError, ValueError):
            return False

    @staticmethod
    def _reparse_point(path: Path) -> bool:
        try:
            metadata = path.stat(follow_symlinks=False)
        except OSError:
            return True
        attributes = getattr(metadata, "st_file_attributes", 0)
        return path.is_symlink() or bool(
            attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        )

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()


def _package_json_summary(text: str) -> dict[str, Any]:
    value = _json_object(text)
    return {
        "name": _optional_string(value.get("name")),
        "package_manager": _optional_string(value.get("packageManager")),
        "scripts": _mapping_keys(value.get("scripts")),
        "dependencies": _mapping_keys(value.get("dependencies")),
        "dev_dependencies": _mapping_keys(value.get("devDependencies")),
    }


def _pyproject_summary(text: str) -> dict[str, Any]:
    value = _toml_object(text)
    project = value.get("project") if isinstance(value.get("project"), dict) else {}
    tool = value.get("tool") if isinstance(value.get("tool"), dict) else {}
    dependencies = project.get("dependencies") if isinstance(project, dict) else []
    return {
        "name": _optional_string(project.get("name")) if isinstance(project, dict) else None,
        "dependencies": tuple(sorted(item for item in dependencies if isinstance(item, str))),
        "tools": tuple(sorted(str(key) for key in tool)),
    }


def _cargo_summary(text: str) -> dict[str, Any]:
    value = _toml_object(text)
    package = value.get("package") if isinstance(value.get("package"), dict) else {}
    return {
        "name": _optional_string(package.get("name")) if isinstance(package, dict) else None,
        "dependencies": _mapping_keys(value.get("dependencies")),
        "features": _mapping_keys(value.get("features")),
    }


def _json_summary(text: str) -> dict[str, Any]:
    return {"top_level_keys": tuple(sorted(_json_object(text)))}


def _toml_summary(text: str) -> dict[str, Any]:
    return {"top_level_keys": tuple(sorted(_toml_object(text)))}


def _json_object(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _toml_object(text: str) -> dict[str, Any]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return {}


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _mapping_keys(value: Any) -> tuple[str, ...]:
    return tuple(sorted(str(key) for key in value)) if isinstance(value, dict) else ()


def _python_semantics(text: str) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return (), (), ()
    imports: set[str] = set()
    symbols: set[str] = set()
    exports: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".", 1)[0])
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.add(node.name)
            if not node.name.startswith("_"):
                exports.add(node.name)
    return tuple(sorted(imports)), tuple(sorted(exports)), tuple(sorted(symbols))


def _typescript_semantics(
    text: str,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    tokens = _lex(text)
    imports: set[str] = set()
    exports: set[str] = set()
    symbols: set[str] = set()
    declarations = {"class", "const", "enum", "function", "interface", "let", "type", "var"}
    for index, token in enumerate(tokens):
        if token == "import":
            for candidate in tokens[index + 1 : index + 20]:
                if candidate.startswith("string:"):
                    imports.add(candidate[7:])
                    break
                if candidate == ";":
                    break
        if token in declarations and index + 1 < len(tokens):
            name = tokens[index + 1]
            if name.isidentifier():
                symbols.add(name)
                if index > 0 and tokens[index - 1] in {"export", "default"}:
                    exports.add(name)
    return tuple(sorted(imports)), tuple(sorted(exports)), tuple(sorted(symbols))


def _rust_semantics(text: str) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    tokens = _lex(text)
    imports: set[str] = set()
    exports: set[str] = set()
    symbols: set[str] = set()
    declarations = {"const", "enum", "fn", "mod", "static", "struct", "trait", "type"}
    for index, token in enumerate(tokens):
        if token == "use" and index + 1 < len(tokens):
            candidate = tokens[index + 1]
            if candidate.isidentifier():
                imports.add(candidate)
        if token in declarations and index + 1 < len(tokens):
            name = tokens[index + 1]
            if name.isidentifier():
                symbols.add(name)
                if index > 0 and tokens[index - 1] == "pub":
                    exports.add(name)
    return tuple(sorted(imports)), tuple(sorted(exports)), tuple(sorted(symbols))


def _lex(text: str) -> tuple[str, ...]:
    bounded = text[:_MAX_SEMANTIC_BYTES]
    tokens: list[str] = []
    index = 0
    while index < len(bounded):
        character = bounded[index]
        if character.isspace():
            index += 1
            continue
        if bounded.startswith("//", index):
            newline = bounded.find("\n", index + 2)
            index = len(bounded) if newline < 0 else newline + 1
            continue
        if bounded.startswith("/*", index):
            end = bounded.find("*/", index + 2)
            index = len(bounded) if end < 0 else end + 2
            continue
        if character in {'"', "'", "`"}:
            value, index = _string_token(bounded, index, character)
            tokens.append(f"string:{value}")
            continue
        if character.isalpha() or character == "_":
            end = index + 1
            while end < len(bounded) and (bounded[end].isalnum() or bounded[end] == "_"):
                end += 1
            tokens.append(bounded[index:end])
            index = end
            continue
        tokens.append(character)
        index += 1
    return tuple(tokens)


def _string_token(text: str, start: int, quote: str) -> tuple[str, int]:
    value: list[str] = []
    index = start + 1
    while index < len(text):
        if text[index] == "\\" and index + 1 < len(text):
            value.append(text[index + 1])
            index += 2
            continue
        if text[index] == quote:
            return "".join(value), index + 1
        value.append(text[index])
        index += 1
    return "".join(value), index


__all__ = ["ProjectIndexer"]
