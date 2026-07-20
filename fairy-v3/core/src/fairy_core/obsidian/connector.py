from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import UUID

from fairy_core.contracts.obsidian import (
    ObsidianConnectorHealthModel,
    ObsidianSourceCreateInput,
    ObsidianSourceListInput,
    ObsidianSourceModel,
    ObsidianSourcePageModel,
    ObsidianSourceSyncInput,
    ObsidianSyncResultModel,
    ObsidianVaultItemContentModel,
    ObsidianVaultItemModel,
    ObsidianVaultItemPageModel,
    ObsidianVaultItemReadInput,
)
from fairy_core.domain.errors import IdempotencyConflictError, VersionConflictError
from fairy_core.domain.ids import new_id

_WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
_SUPPORTED_SUFFIXES = frozenset({".md", ".canvas"})
_EXCLUDED_PARTS = frozenset({".git", ".obsidian", ".trash"})
_MAX_ITEM_BYTES = 2 * 1024 * 1024


class ObsidianConnector:
    def __init__(
        self,
        environment: Mapping[str, str] | None = None,
        *,
        registry_path: Path | None = None,
    ) -> None:
        self._environment = dict(os.environ if environment is None else environment)
        self._registry_path = registry_path

    def health(self) -> ObsidianConnectorHealthModel:
        desktop_installed = any(path.is_file() for path in self._desktop_candidates())
        cli_available = self._cli_path() is not None
        if cli_available:
            status = "ready"
            summary = "Obsidian Desktop and the official CLI are available"
        elif desktop_installed:
            status = "cli_disabled"
            summary = "Enable Command line interface in Obsidian Settings > General"
        else:
            status = "not_installed"
            summary = "Install Obsidian 1.12.7 or newer to connect a Vault"
        return ObsidianConnectorHealthModel(
            desktop_installed=desktop_installed,
            cli_available=cli_available,
            status=status,
            public_summary=summary,
        )

    def create_source(self, request: ObsidianSourceCreateInput) -> ObsidianSourceModel:
        registry = self._load_registry()
        fingerprint = self._fingerprint(request.model_dump(mode="json"))
        prior = registry["requests"].get(request.idempotency_key)
        if prior is not None:
            if prior["fingerprint"] != fingerprint:
                raise IdempotencyConflictError("Obsidian source request changed")
            return self._source_model(registry["sources"][prior["source_id"]])
        vault = self._canonical_vault(request.vault_path)
        allowed = tuple(self._relative_directory(value) for value in request.allowed_directories)
        managed = self._relative_directory(request.managed_directory)
        now = datetime.now(UTC).isoformat()
        source_id = str(new_id())
        record = {
            "id": source_id,
            "project_id": str(request.project_id),
            "display_name": request.display_name.strip(),
            "vault_path": str(vault),
            "allowed_directories": list(dict.fromkeys(allowed)),
            "managed_directory": managed,
            "mode": request.mode.value,
            "status": "configured",
            "revision": 1,
            "items": {},
            "last_synced_at": None,
            "created_at": now,
            "updated_at": now,
        }
        registry["sources"][source_id] = record
        registry["requests"][request.idempotency_key] = {
            "fingerprint": fingerprint,
            "source_id": source_id,
        }
        self._save_registry(registry)
        return self._source_model(record)

    def list_sources(self, request: ObsidianSourceListInput) -> ObsidianSourcePageModel:
        registry = self._load_registry()
        items = tuple(
            self._source_model(record)
            for record in registry["sources"].values()
            if record["project_id"] == str(request.project_id)
        )
        return ObsidianSourcePageModel(items=tuple(sorted(items, key=lambda item: item.created_at)))

    def list_items(self, source_id: UUID) -> ObsidianVaultItemPageModel:
        record = self._source_record(source_id)
        items = tuple(
            self._item_model(source_id, item)
            for item in sorted(record["items"].values(), key=lambda value: value["relative_path"])
        )
        return ObsidianVaultItemPageModel(items=items, source_revision=int(record["revision"]))

    def read_item(self, request: ObsidianVaultItemReadInput) -> ObsidianVaultItemContentModel:
        record = self._source_record(request.source_id)
        if int(record["revision"]) != request.expected_source_revision:
            raise VersionConflictError("Obsidian source changed concurrently")
        relative = self._relative_file(request.relative_path)
        item = record["items"].get(relative)
        if item is None or item["content_hash"] != request.expected_content_hash:
            raise VersionConflictError("Obsidian item changed since it was indexed")
        vault = self._canonical_vault(str(record["vault_path"]))
        path = vault / PurePosixPath(relative)
        if self._unsafe_file(path, vault):
            raise ValueError("Obsidian item is outside the authorized Vault")
        data = path.read_bytes()
        content_hash = hashlib.sha256(data).hexdigest()
        if content_hash != request.expected_content_hash:
            raise VersionConflictError("Obsidian item changed since it was indexed")
        return ObsidianVaultItemContentModel(
            source_id=request.source_id,
            relative_path=relative,
            title=str(item["title"]),
            kind=str(item["kind"]),
            content_hash=content_hash,
            content=data.decode("utf-8"),
        )

    def sync(self, request: ObsidianSourceSyncInput) -> ObsidianSyncResultModel:
        registry = self._load_registry()
        record = registry["sources"].get(str(request.source_id))
        if record is None:
            raise KeyError(f"Obsidian source not found: {request.source_id}")
        if int(record["revision"]) != request.expected_revision:
            raise VersionConflictError("Obsidian source changed concurrently")
        scanned, failed = self._scan(record)
        previous = record["items"]
        changed = sum(previous.get(path) != item for path, item in scanned.items())
        deleted = len(set(previous).difference(scanned))
        now = datetime.now(UTC).isoformat()
        record["items"] = scanned
        record["revision"] = int(record["revision"]) + 1
        record["status"] = "ready" if failed == 0 else "partial"
        record["last_synced_at"] = now
        record["updated_at"] = now
        self._save_registry(registry)
        return ObsidianSyncResultModel(
            source=self._source_model(record),
            scanned_count=len(scanned),
            changed_count=changed,
            deleted_count=deleted,
            failed_count=failed,
        )

    def _scan(self, record: dict[str, object]) -> tuple[dict[str, object], int]:
        vault = self._canonical_vault(str(record["vault_path"]))
        allowed = tuple(record["allowed_directories"])
        roots = (vault,) if not allowed else tuple(vault / value for value in allowed)
        items: dict[str, object] = {}
        failed = 0
        for root in roots:
            try:
                canonical_root = root.resolve(strict=True)
            except (FileNotFoundError, OSError):
                failed += 1
                continue
            if not canonical_root.is_dir() or not self._within(canonical_root, vault):
                failed += 1
                continue
            for path in canonical_root.rglob("*"):
                try:
                    relative = path.relative_to(vault).as_posix()
                    if self._excluded(relative) or self._unsafe_file(path, vault):
                        continue
                    if path.suffix.casefold() not in _SUPPORTED_SUFFIXES:
                        continue
                    stat_result = path.stat(follow_symlinks=False)
                    if stat_result.st_size > _MAX_ITEM_BYTES:
                        failed += 1
                        continue
                    data = path.read_bytes()
                    text = data.decode("utf-8")
                    title = self._title(relative, text)
                    links = (
                        tuple(sorted(set(_WIKILINK.findall(text))))
                        if path.suffix.casefold() == ".md"
                        else ()
                    )
                    items[relative] = {
                        "relative_path": relative,
                        "title": title,
                        "kind": "markdown" if path.suffix.casefold() == ".md" else "canvas",
                        "content_hash": hashlib.sha256(data).hexdigest(),
                        "byte_length": len(data),
                        "links": list(links),
                        "modified_at": datetime.fromtimestamp(
                            stat_result.st_mtime,
                            UTC,
                        ).isoformat(),
                    }
                except (OSError, UnicodeDecodeError, ValueError):
                    failed += 1
        return items, failed

    def _source_record(self, source_id: UUID) -> dict[str, object]:
        record = self._load_registry()["sources"].get(str(source_id))
        if record is None:
            raise KeyError(f"Obsidian source not found: {source_id}")
        return record

    def _load_registry(self) -> dict[str, dict[str, object]]:
        if self._registry_path is None:
            raise RuntimeError("Obsidian source registry is unavailable")
        if not self._registry_path.exists():
            return {"sources": {}, "requests": {}}
        parsed = json.loads(self._registry_path.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict) or not isinstance(parsed.get("sources"), dict):
            raise ValueError("Obsidian source registry is invalid")
        parsed.setdefault("requests", {})
        return parsed

    def _save_registry(self, registry: dict[str, object]) -> None:
        assert self._registry_path is not None
        self._registry_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._registry_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(registry, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, self._registry_path)

    def _canonical_vault(self, value: str) -> Path:
        candidate = Path(value).expanduser().resolve(strict=True)
        if not candidate.is_dir() or self._is_reparse(candidate):
            raise ValueError("Obsidian Vault must be a real local directory")
        if str(candidate).startswith("\\\\") or candidate.drive == "":
            raise ValueError("Obsidian Vault must use a local drive")
        return candidate

    @staticmethod
    def _relative_directory(value: str) -> str:
        normalized = value.replace("\\", "/").strip().strip("/")
        if not normalized:
            return ""
        path = PurePosixPath(normalized)
        if path.is_absolute() or ".." in path.parts or ":" in normalized:
            raise ValueError("Obsidian directory must be Vault-relative")
        return path.as_posix()

    @classmethod
    def _relative_file(cls, value: str) -> str:
        normalized = cls._relative_directory(value)
        if not normalized or PurePosixPath(normalized).suffix.casefold() not in _SUPPORTED_SUFFIXES:
            raise ValueError("Obsidian item must be a supported Vault-relative file")
        return normalized

    @staticmethod
    def _within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    @classmethod
    def _unsafe_file(cls, path: Path, vault: Path) -> bool:
        return (
            path.is_symlink()
            or cls._is_reparse(path)
            or not path.is_file()
            or not cls._within(path.resolve(strict=True), vault)
        )

    @staticmethod
    def _is_reparse(path: Path) -> bool:
        attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
        return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)

    @staticmethod
    def _excluded(relative: str) -> bool:
        parts = PurePosixPath(relative).parts
        return any(part in _EXCLUDED_PARTS or part.startswith(".") for part in parts)

    @staticmethod
    def _title(relative: str, text: str) -> str:
        for line in text.splitlines()[:40]:
            if line.startswith("# "):
                return line[2:].strip()[:200]
        return PurePosixPath(relative).stem[:200]

    @staticmethod
    def _source_model(record: dict[str, object]) -> ObsidianSourceModel:
        return ObsidianSourceModel(
            id=record["id"],
            project_id=record["project_id"],
            display_name=record["display_name"],
            vault_display_path=record["vault_path"],
            allowed_directories=tuple(record["allowed_directories"]),
            managed_directory=record["managed_directory"],
            mode=record["mode"],
            status=record["status"],
            revision=record["revision"],
            item_count=len(record["items"]),
            last_synced_at=record["last_synced_at"],
            created_at=record["created_at"],
            updated_at=record["updated_at"],
        )

    @staticmethod
    def _item_model(source_id: UUID, record: dict[str, object]) -> ObsidianVaultItemModel:
        return ObsidianVaultItemModel(source_id=source_id, **record)

    @staticmethod
    def _fingerprint(payload: object) -> str:
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(encoded.encode()).hexdigest()

    def _cli_path(self) -> Path | None:
        located = shutil.which("obsidian", path=self._environment.get("PATH"))
        candidate = Path(located) if located is not None else None
        return candidate if candidate is not None and candidate.is_file() else None

    def _desktop_candidates(self) -> tuple[Path, ...]:
        local_app_data = Path(self._environment.get("LOCALAPPDATA", ""))
        program_files = Path(self._environment.get("PROGRAMFILES", r"C:\Program Files"))
        return (
            local_app_data / "Programs" / "Obsidian" / "Obsidian.exe",
            program_files / "Obsidian" / "Obsidian.exe",
        )


__all__ = ["ObsidianConnector"]
