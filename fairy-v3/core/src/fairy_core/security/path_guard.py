from __future__ import annotations

import ntpath
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from fairy_core.domain.errors import ScopeViolationError


@dataclass(frozen=True, slots=True)
class WriteLease:
    requested_path: str
    canonical_path: Path
    parent_path: Path
    parent_identity: tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class ReadLease:
    requested_path: str
    canonical_path: Path
    target_identity: tuple[int, int]
    parent_identity: tuple[int, int, int]


class PathGuard:
    def __init__(
        self,
        *,
        project_root: Path,
        allowed_roots: tuple[Path, ...],
        forbidden_roots: tuple[Path, ...],
    ) -> None:
        self._project_root = project_root.resolve(strict=False)
        self._allowed_roots = tuple(path.resolve(strict=False) for path in allowed_roots)
        self._forbidden_roots = tuple(path.resolve(strict=False) for path in forbidden_roots)

    def validate_write(self, candidate: str | Path) -> Path:
        raw = str(candidate)
        self._validate_raw_windows_path(raw)
        path = Path(raw)
        if not path.is_absolute():
            path = self._project_root / path
        canonical = path.resolve(strict=False)

        if not self._is_within(canonical, self._project_root):
            self._deny(raw, "path leaves project root")
        if any(self._is_within(canonical, root) for root in self._forbidden_roots):
            self._deny(raw, "path is inside a forbidden root")
        if not any(self._is_within(canonical, root) for root in self._allowed_roots):
            self._deny(raw, "path is outside allowed roots")
        return canonical

    def validate_read(self, candidate: str | Path) -> Path:
        raw = str(candidate)
        self._validate_raw_windows_path(raw)
        path = Path(raw)
        if not path.is_absolute():
            path = self._project_root / path
        try:
            canonical = path.resolve(strict=True)
        except OSError as error:
            raise ScopeViolationError(f"read path does not exist: {raw}") from error
        if not self._is_within(canonical, self._project_root):
            self._deny(raw, "path leaves project root")
        if any(self._is_within(canonical, root) for root in self._forbidden_roots):
            self._deny(raw, "path is inside a forbidden root")
        if not any(self._is_within(canonical, root) for root in self._allowed_roots):
            self._deny(raw, "path is outside allowed roots")
        if not canonical.is_file():
            self._deny(raw, "read path is not a file")
        self._reject_reparse_components(path)
        return canonical

    def issue_read_lease(self, candidate: str | Path) -> ReadLease:
        canonical = self.validate_read(candidate)
        return ReadLease(
            requested_path=str(candidate),
            canonical_path=canonical,
            target_identity=self._read_identity(canonical),
            parent_identity=self._identity(canonical.parent),
        )

    def revalidate_read_lease(self, lease: ReadLease) -> Path:
        canonical = self.validate_read(lease.requested_path)
        if (
            canonical != lease.canonical_path
            or self._read_identity(canonical) != lease.target_identity
            or self._identity(canonical.parent) != lease.parent_identity
        ):
            raise ScopeViolationError(
                "read target identity changed after authorization",
                code="PATH_IDENTITY_CHANGED",
            )
        return canonical

    def issue_write_lease(self, candidate: str | Path) -> WriteLease:
        canonical = self.validate_write(candidate)
        parent = self._nearest_existing_parent(canonical.parent)
        return WriteLease(
            requested_path=str(candidate),
            canonical_path=canonical,
            parent_path=parent,
            parent_identity=self._identity(parent),
        )

    def revalidate_write_lease(self, lease: WriteLease) -> Path:
        canonical = self.validate_write(lease.requested_path)
        parent = self._nearest_existing_parent(canonical.parent)
        if (
            canonical != lease.canonical_path
            or parent != lease.parent_path
            or self._identity(parent) != lease.parent_identity
        ):
            raise ScopeViolationError(
                "write parent identity changed after authorization",
                code="PATH_IDENTITY_CHANGED",
            )
        return canonical

    @staticmethod
    def _validate_raw_windows_path(raw: str) -> None:
        normalized = raw.replace("/", "\\")
        lowered = normalized.casefold()
        if "\x00" in raw:
            raise ScopeViolationError("path contains NUL")
        if lowered.startswith(("\\\\", "\\\\?\\", "\\\\.\\")):
            raise ScopeViolationError("UNC and device paths are not allowed")
        drive, tail = ntpath.splitdrive(normalized)
        if drive:
            raise ScopeViolationError("drive-qualified paths are not allowed")
        if ":" in tail:
            raise ScopeViolationError("alternate data streams are not allowed")

    @staticmethod
    def _is_within(candidate: Path, root: Path) -> bool:
        try:
            candidate_text = os.path.normcase(str(candidate))
            root_text = os.path.normcase(str(root))
            return os.path.commonpath((candidate_text, root_text)) == root_text
        except ValueError:
            return False

    @staticmethod
    def _nearest_existing_parent(path: Path) -> Path:
        current = path
        while not current.exists():
            parent = current.parent
            if parent == current:
                raise ScopeViolationError("no existing parent for write target")
            current = parent
        return current.resolve(strict=True)

    @staticmethod
    def _identity(path: Path) -> tuple[int, int, int]:
        stat = path.stat(follow_symlinks=False)
        return stat.st_dev, stat.st_ino, stat.st_ctime_ns

    @staticmethod
    def _read_identity(path: Path) -> tuple[int, int]:
        metadata = path.stat(follow_symlinks=False)
        return metadata.st_dev, metadata.st_ino

    def _reject_reparse_components(self, requested: Path) -> None:
        relative = (
            requested if not requested.is_absolute() else requested.relative_to(self._project_root)
        )
        current = self._project_root
        for part in relative.parts:
            current /= part
            metadata = current.stat(follow_symlinks=False)
            attributes = getattr(metadata, "st_file_attributes", 0)
            if current.is_symlink() or bool(
                attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            ):
                self._deny(str(requested), "reparse points are not allowed")

    @staticmethod
    def _deny(raw: str, reason: str) -> None:
        raise ScopeViolationError(f"{reason}: {raw}")
