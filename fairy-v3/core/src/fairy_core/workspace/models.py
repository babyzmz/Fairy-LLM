from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any
from uuid import UUID

_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def _now() -> datetime:
    return datetime.now(UTC)


def _json_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        normalized = json.loads(
            json.dumps(
                dict(value),
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
    except (TypeError, ValueError) as error:
        raise ValueError("workspace metadata must be JSON-compatible") from error
    return MappingProxyType(normalized)


def _relative_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip()
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts or ":" in normalized:
        raise ValueError(f"invalid workspace-relative path: {value}")
    return path.as_posix()


@dataclass(frozen=True, slots=True)
class TaskWorkspace:
    task_id: UUID
    project_id: UUID | None
    conversation_id: UUID
    version_id: UUID | None
    root: Path
    editable_files: tuple[str, ...]
    reference_files: tuple[str, ...]
    constraints: Mapping[str, Any]
    generation: int = 1
    created_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        root = self.root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Task Workspace root must be a directory")
        if (self.project_id is None) != (self.version_id is None):
            raise ValueError("project and Version bindings must both be present or absent")
        if self.generation < 1:
            raise ValueError("Task Workspace generation must be positive")
        editable = tuple(dict.fromkeys(_relative_path(value) for value in self.editable_files))
        references = tuple(dict.fromkeys(_relative_path(value) for value in self.reference_files))
        if not editable and not references:
            raise ValueError("Task Workspace must expose at least one file pattern")
        object.__setattr__(self, "root", root)
        object.__setattr__(self, "editable_files", editable)
        object.__setattr__(self, "reference_files", references)
        object.__setattr__(self, "constraints", _json_mapping(self.constraints))


@dataclass(frozen=True, slots=True)
class ProjectFile:
    path: str
    byte_length: int
    content_hash: str
    kind: str
    language: str | None = None
    imports: tuple[str, ...] = ()
    exports: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    summary: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if self.byte_length < 0:
            raise ValueError("indexed file byte_length cannot be negative")
        if _DIGEST.fullmatch(self.content_hash) is None:
            raise ValueError("indexed file content_hash must be lowercase SHA-256")
        if self.kind not in {"source", "manifest", "config", "text", "binary", "oversized"}:
            raise ValueError(f"unsupported indexed file kind: {self.kind}")
        object.__setattr__(self, "path", _relative_path(self.path))
        object.__setattr__(self, "imports", tuple(sorted(set(self.imports))))
        object.__setattr__(self, "exports", tuple(sorted(set(self.exports))))
        object.__setattr__(self, "symbols", tuple(sorted(set(self.symbols))))
        object.__setattr__(self, "summary", _json_mapping(self.summary))


@dataclass(frozen=True, slots=True)
class ProjectIndex:
    project_id: UUID
    version_id: UUID
    generation: int
    source_hash: str
    files: tuple[ProjectFile, ...]
    created_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        if self.generation < 1:
            raise ValueError("Project Index generation must be positive")
        if _DIGEST.fullmatch(self.source_hash) is None:
            raise ValueError("Project Index source_hash must be lowercase SHA-256")
        ordered = tuple(sorted(self.files, key=lambda item: item.path))
        paths = tuple(item.path for item in ordered)
        if len(paths) != len(set(paths)):
            raise ValueError("Project Index contains duplicate file paths")
        object.__setattr__(self, "files", ordered)

    def file(self, path: str) -> ProjectFile:
        normalized = _relative_path(path)
        for item in self.files:
            if item.path == normalized:
                return item
        raise KeyError(f"indexed file not found: {normalized}")


__all__ = ["ProjectFile", "ProjectIndex", "TaskWorkspace"]
