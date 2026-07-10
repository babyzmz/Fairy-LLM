from __future__ import annotations

import json
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from fairy_core.security.path_guard import PathGuard

_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class _JournalEntry:
    relative_path: str
    existed: bool


class FileChangesetTransaction:
    def __init__(
        self,
        root: Path,
        *,
        guard: PathGuard,
        entries: tuple[_JournalEntry, ...],
    ) -> None:
        self._root = root
        self._guard = guard
        self._entries = entries

    @classmethod
    def recover(cls, root: Path, *, guard: PathGuard) -> None:
        if not root.exists():
            return
        manifest_path = root / "manifest.json"
        if not manifest_path.is_file():
            shutil.rmtree(root)
            return
        transaction = cls(
            root,
            guard=guard,
            entries=_load_entries(manifest_path),
        )
        if (root / "applied").is_file():
            transaction.cleanup()
            return
        transaction.rollback()
        transaction.cleanup()

    @classmethod
    def prepare(
        cls,
        root: Path,
        *,
        guard: PathGuard,
        relative_paths: tuple[str, ...],
    ) -> FileChangesetTransaction:
        unique: dict[Path, str] = {}
        for relative_path in relative_paths:
            target = guard.validate_write(relative_path)
            unique.setdefault(target, relative_path)
        if root.exists():
            raise RuntimeError(f"changeset transaction already exists: {root}")
        root.mkdir(parents=True)
        entries: list[_JournalEntry] = []
        try:
            for index, (target, relative_path) in enumerate(unique.items()):
                existed = target.exists()
                if existed:
                    lease = guard.issue_write_lease(relative_path)
                    target = guard.revalidate_write_lease(lease)
                    if not target.is_file():
                        raise IsADirectoryError(target)
                    _durable_write(root / "backups" / f"{index}.bin", target.read_bytes())
                entries.append(_JournalEntry(relative_path=relative_path, existed=existed))
            _durable_write(
                root / "manifest.json",
                json.dumps(
                    {
                        "schema_version": _SCHEMA_VERSION,
                        "entries": [
                            {
                                "relative_path": entry.relative_path,
                                "existed": entry.existed,
                            }
                            for entry in entries
                        ],
                    },
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8"),
            )
        except BaseException:
            shutil.rmtree(root, ignore_errors=True)
            raise
        return cls(root, guard=guard, entries=tuple(entries))

    def rollback(self) -> None:
        staging_root = self._root / "rollback"
        for index, entry in enumerate(self._entries):
            if entry.existed:
                backup = self._root / "backups" / f"{index}.bin"
                if not backup.is_file():
                    raise RuntimeError(f"changeset backup is missing: {backup}")
                atomic_write_scoped(
                    guard=self._guard,
                    relative_path=entry.relative_path,
                    content=backup.read_bytes(),
                    staging_root=staging_root,
                )
            else:
                unlink_scoped(
                    guard=self._guard,
                    relative_path=entry.relative_path,
                )

    def mark_applied(self) -> None:
        _durable_write(self._root / "applied", b"applied\n")

    def cleanup(self, *, ignore_errors: bool = False) -> None:
        shutil.rmtree(self._root, ignore_errors=ignore_errors)


def atomic_write_scoped(
    *,
    guard: PathGuard,
    relative_path: str,
    content: bytes,
    staging_root: Path,
) -> Path:
    target = guard.validate_write(relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lease = guard.issue_write_lease(relative_path)
    staging_root.mkdir(parents=True, exist_ok=True)
    staged = staging_root / f"{uuid4()}.tmp"
    try:
        _durable_write(staged, content)
        checked_target = guard.revalidate_write_lease(lease)
        if checked_target != target:
            raise RuntimeError("write target changed after authorization")
        if target.is_file():
            staged.chmod(stat.S_IMODE(target.stat().st_mode))
        os.replace(staged, target)
    finally:
        staged.unlink(missing_ok=True)
    return target


def unlink_scoped(*, guard: PathGuard, relative_path: str) -> None:
    target = guard.validate_write(relative_path)
    if not target.exists():
        return
    lease = guard.issue_write_lease(relative_path)
    checked_target = guard.revalidate_write_lease(lease)
    if checked_target != target:
        raise RuntimeError("unlink target changed after authorization")
    if target.is_dir():
        raise IsADirectoryError(target)
    target.unlink()


def _load_entries(path: Path) -> tuple[_JournalEntry, ...]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"invalid changeset journal: {path}") from error
    if not isinstance(payload, dict) or payload.get("schema_version") != _SCHEMA_VERSION:
        raise RuntimeError(f"unsupported changeset journal: {path}")
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list):
        raise RuntimeError(f"invalid changeset journal entries: {path}")
    entries: list[_JournalEntry] = []
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict):
            raise RuntimeError(f"invalid changeset journal entry: {path}")
        relative_path = raw_entry.get("relative_path")
        existed = raw_entry.get("existed")
        if not isinstance(relative_path, str) or not relative_path or not isinstance(existed, bool):
            raise RuntimeError(f"invalid changeset journal entry: {path}")
        entries.append(_JournalEntry(relative_path=relative_path, existed=existed))
    return tuple(entries)


def _durable_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4()}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
