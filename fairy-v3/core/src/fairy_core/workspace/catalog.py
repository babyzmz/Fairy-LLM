from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import PurePosixPath

from fairy_core.workspace.models import ProjectFile

_MAGIC_TYPES: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"PK\x03\x04", "application/zip"),
    (b"glTF", "model/gltf-binary"),
    (b"RIFF", "application/riff"),
    (b"OggS", "application/ogg"),
    (b"fLaC", "audio/flac"),
    (b"SQLite format 3\x00", "application/vnd.sqlite3"),
)
_EXTENSION_TYPES = {
    ".gltf": "model/gltf+json",
    ".glb": "model/gltf-binary",
    ".obj": "model/obj",
    ".stl": "model/stl",
    ".ply": "model/ply",
    ".step": "model/step",
    ".stp": "model/step",
    ".iges": "model/iges",
    ".igs": "model/iges",
    ".ifc": "model/ifc",
}


@dataclass(frozen=True, slots=True)
class FileDescriptor:
    path: str
    byte_length: int
    content_hash: str
    kind: str
    language: str | None
    extension: str | None
    media_type: str
    claimed_media_type: str | None
    media_type_conflict: bool


class WorkspaceFileCatalog:
    def describe(self, indexed: ProjectFile, prefix: bytes) -> FileDescriptor:
        suffix = PurePosixPath(indexed.path).suffix.lower()
        claimed = mimetypes.guess_type(indexed.path)[0]
        detected = self._detect(prefix, suffix, indexed.kind)
        return FileDescriptor(
            path=indexed.path,
            byte_length=indexed.byte_length,
            content_hash=indexed.content_hash,
            kind=indexed.kind,
            language=indexed.language,
            extension=suffix[1:] if suffix else None,
            media_type=detected,
            claimed_media_type=claimed,
            media_type_conflict=claimed is not None and not _compatible(claimed, detected),
        )

    @staticmethod
    def _detect(prefix: bytes, suffix: str, kind: str) -> str:
        for magic, media_type in _MAGIC_TYPES:
            if prefix.startswith(magic):
                if media_type == "application/riff":
                    if prefix[8:12] == b"WEBP":
                        return "image/webp"
                    if prefix[8:12] == b"WAVE":
                        return "audio/wav"
                return media_type
        if len(prefix) >= 12 and prefix[4:8] == b"ftyp":
            return "video/mp4"
        if suffix in _EXTENSION_TYPES:
            return _EXTENSION_TYPES[suffix]
        if kind in {"source", "manifest", "config", "text"}:
            return mimetypes.guess_type(f"file{suffix}")[0] or "text/plain"
        return "application/octet-stream"


def _compatible(claimed: str, detected: str) -> bool:
    if claimed == detected:
        return True
    if claimed.startswith("text/") and detected.startswith("text/"):
        return True
    aliases = {
        frozenset({"application/json", "text/json"}),
        frozenset({"model/gltf-binary", "application/octet-stream"}),
    }
    return frozenset({claimed, detected}) in aliases


__all__ = ["FileDescriptor", "WorkspaceFileCatalog"]
