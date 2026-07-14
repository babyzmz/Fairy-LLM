from __future__ import annotations

import hashlib
import json
import posixpath
import re
from dataclasses import dataclass
from enum import StrEnum
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid5

from fairy_core.security.path_guard import PathGuard
from fairy_core.workspace.models import ProjectFile, ProjectIndex

FILE_SET_PARSER_VERSION = "1.0.0"
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_DEPENDENCIES = 256
_SEQUENCE = re.compile(r"^(.*?)(\d{3,})(\.[^.]+)$")


class FileSetKind(StrEnum):
    SINGLE = "single"
    GLTF = "gltf"
    OBJ = "obj"
    HTML_SITE = "html_site"
    MEDIA_CAPTIONS = "media_captions"
    IMAGE_SEQUENCE = "image_sequence"


@dataclass(frozen=True, slots=True)
class FileSetMember:
    path: str
    content_hash: str
    byte_length: int
    role: str


@dataclass(frozen=True, slots=True)
class FileSet:
    id: UUID
    workspace_id: UUID
    version_id: UUID
    kind: FileSetKind
    primary_path: str
    parser_version: str
    manifest_hash: str
    members: tuple[FileSetMember, ...]
    missing_dependencies: tuple[str, ...]
    blocked_dependencies: tuple[str, ...]


class FileSetResolver:
    def resolve(self, *, index: ProjectIndex, root: Path, primary_path: str) -> FileSet:
        primary = index.file(primary_path)
        files = {file.path: file for file in index.files}
        kind = self._kind(primary.path, files)
        dependencies, missing, blocked = self._dependencies(
            kind=kind,
            primary=primary,
            files=files,
            root=root,
        )
        members = [self._member(primary, "primary")]
        members.extend(self._member(files[path], "dependency") for path in dependencies)
        ordered = tuple(sorted(members, key=lambda item: (item.role != "primary", item.path)))
        payload = {
            "kind": kind.value,
            "primary_path": primary.path,
            "parser_version": FILE_SET_PARSER_VERSION,
            "members": [(item.path, item.content_hash, item.role) for item in ordered],
            "missing": sorted(missing),
            "blocked": sorted(blocked),
        }
        manifest_hash = hashlib.sha256(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest()
        namespace = index.workspace_id or index.version_id
        file_set_id = uuid5(namespace, f"{index.version_id}:{primary.path}:{manifest_hash}")
        return FileSet(
            id=file_set_id,
            workspace_id=namespace,
            version_id=index.version_id,
            kind=kind,
            primary_path=primary.path,
            parser_version=FILE_SET_PARSER_VERSION,
            manifest_hash=manifest_hash,
            members=ordered,
            missing_dependencies=tuple(sorted(missing)),
            blocked_dependencies=tuple(sorted(blocked)),
        )

    def find(self, *, index: ProjectIndex, root: Path, file_set_id: UUID) -> FileSet:
        for file in index.files:
            try:
                candidate = self.resolve(index=index, root=root, primary_path=file.path)
            except (UnicodeDecodeError, ValueError):
                continue
            if candidate.id == file_set_id:
                return candidate
        raise KeyError(f"FileSet not found: {file_set_id}")

    def _dependencies(
        self,
        *,
        kind: FileSetKind,
        primary: ProjectFile,
        files: dict[str, ProjectFile],
        root: Path,
    ) -> tuple[set[str], set[str], set[str]]:
        if kind is FileSetKind.GLTF:
            requested = self._gltf_dependencies(primary.path, self._read(root, primary))
        elif kind is FileSetKind.OBJ:
            requested = self._obj_dependencies(primary.path, self._read(root, primary), files, root)
        elif kind is FileSetKind.HTML_SITE:
            requested = self._html_dependencies(primary.path, self._read(root, primary))
        elif kind is FileSetKind.MEDIA_CAPTIONS:
            stem = PurePosixPath(primary.path).with_suffix("").as_posix()
            requested = {
                posixpath.relpath(path, PurePosixPath(primary.path).parent.as_posix())
                for path in files
                if path in {f"{stem}.vtt", f"{stem}.srt", f"{stem}.ass"}
            }
        elif kind is FileSetKind.IMAGE_SEQUENCE:
            requested = self._sequence_members(primary.path, files)
        else:
            requested = set()
        missing: set[str] = set()
        blocked: set[str] = set()
        resolved: set[str] = set()
        for dependency in sorted(requested):
            normalized = normalize_dependency_path(primary.path, dependency)
            if normalized is None:
                blocked.add(dependency)
            elif normalized not in files:
                missing.add(normalized)
            elif normalized != primary.path:
                resolved.add(normalized)
            if len(resolved) + len(missing) + len(blocked) > MAX_DEPENDENCIES:
                raise ValueError("FileSet dependency limit exceeded")
        return resolved, missing, blocked

    @staticmethod
    def _kind(path: str, files: dict[str, ProjectFile]) -> FileSetKind:
        suffix = PurePosixPath(path).suffix.lower()
        if suffix == ".gltf":
            return FileSetKind.GLTF
        if suffix == ".obj":
            return FileSetKind.OBJ
        if suffix in {".html", ".htm"}:
            return FileSetKind.HTML_SITE
        if suffix in {".mp4", ".webm", ".mov", ".mkv", ".mp3", ".flac", ".wav"}:
            return FileSetKind.MEDIA_CAPTIONS
        if FileSetResolver._sequence_members(path, files):
            return FileSetKind.IMAGE_SEQUENCE
        return FileSetKind.SINGLE

    @staticmethod
    def _read(root: Path, file: ProjectFile) -> bytes:
        if file.byte_length > MAX_MANIFEST_BYTES:
            raise ValueError("FileSet manifest exceeds parser limit")
        guard = PathGuard(project_root=root, allowed_roots=(root,), forbidden_roots=())
        lease = guard.issue_read_lease(file.path)
        source = guard.revalidate_read_lease(lease)
        content = source.read_bytes()
        if hashlib.sha256(content).hexdigest() != file.content_hash:
            raise ValueError("FileSet source digest changed")
        return content

    @staticmethod
    def _gltf_dependencies(primary: str, content: bytes) -> set[str]:
        try:
            payload = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("glTF manifest is invalid") from error
        if not isinstance(payload, dict):
            raise ValueError("glTF manifest must be an object")
        values: set[str] = set()
        for collection in ("buffers", "images"):
            entries = payload.get(collection, [])
            if not isinstance(entries, list):
                raise ValueError(f"glTF {collection} must be an array")
            for entry in entries:
                if isinstance(entry, dict) and isinstance(entry.get("uri"), str):
                    values.add(entry["uri"])
        return values

    @staticmethod
    def _obj_dependencies(
        primary: str,
        content: bytes,
        files: dict[str, ProjectFile],
        root: Path,
    ) -> set[str]:
        dependencies: set[str] = set()
        for line in content.decode("utf-8", errors="replace").splitlines():
            if line.lstrip().lower().startswith("mtllib "):
                dependencies.update(line.split(maxsplit=1)[1].split())
        for library in tuple(dependencies):
            normalized = normalize_dependency_path(primary, library)
            if normalized is None or normalized not in files:
                continue
            mtl = FileSetResolver._read(root, files[normalized])
            for line in mtl.decode("utf-8", errors="replace").splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[0].lower().startswith("map_"):
                    texture = normalize_dependency_path(normalized, " ".join(parts[1:]))
                    if texture is not None:
                        dependencies.add(
                            posixpath.relpath(
                                texture,
                                PurePosixPath(primary).parent.as_posix(),
                            )
                        )
        return dependencies

    @staticmethod
    def _html_dependencies(primary: str, content: bytes) -> set[str]:
        parser = _LocalReferenceParser()
        parser.feed(content.decode("utf-8", errors="replace"))
        return parser.references

    @staticmethod
    def _sequence_members(primary: str, files: dict[str, ProjectFile]) -> set[str]:
        path = PurePosixPath(primary)
        match = _SEQUENCE.match(path.name)
        image_suffixes = {".png", ".jpg", ".jpeg", ".exr", ".tif", ".tiff"}
        if match is None or path.suffix.lower() not in image_suffixes:
            return set()
        prefix, digits, suffix = match.groups()
        pattern = re.compile(
            rf"^{re.escape(prefix)}\d{{{len(digits)}}}{re.escape(suffix)}$",
            re.IGNORECASE,
        )
        return {
            posixpath.relpath(candidate, path.parent.as_posix())
            for candidate in files
            if PurePosixPath(candidate).parent == path.parent
            and pattern.match(PurePosixPath(candidate).name)
            and candidate != primary
        }

    @staticmethod
    def _member(file: ProjectFile, role: str) -> FileSetMember:
        return FileSetMember(file.path, file.content_hash, file.byte_length, role)


class _LocalReferenceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: set[str] = set()

    def handle_starttag(self, _tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name.lower() in {"src", "href", "poster"} and value:
                self.references.add(value)


def normalize_dependency_path(primary: str, dependency: str) -> str | None:
    parsed = urlsplit(dependency.strip())
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        return None
    decoded = unquote(parsed.path).replace("\\", "/")
    if not decoded or decoded.startswith("/") or ":" in decoded or "\x00" in decoded:
        return None
    base = PurePosixPath(primary).parent
    parts: list[str] = []
    for part in (base / decoded).parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(part)
    return PurePosixPath(*parts).as_posix() if parts else None


__all__ = [
    "FILE_SET_PARSER_VERSION",
    "FileSet",
    "FileSetKind",
    "FileSetMember",
    "FileSetResolver",
    "normalize_dependency_path",
]
