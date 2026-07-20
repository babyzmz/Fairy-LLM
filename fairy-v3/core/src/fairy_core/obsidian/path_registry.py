from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from fairy_core.domain.ids import new_id


class ObsidianPathRegistry:
    """Device-local mapping from opaque tokens to absolute Vault paths."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def register(self, path: Path, *, token: str | None = None) -> str:
        canonical = path.expanduser().resolve(strict=True)
        if not canonical.is_dir():
            raise ValueError("Obsidian Vault path must be a directory")
        normalized_token = self._token(token or str(new_id()))
        registry = self._load()
        existing = registry["paths"].get(normalized_token)
        record = {
            "path": str(canonical),
            "display_name": canonical.name or canonical.drive,
            "created_at": datetime.now(UTC).isoformat(),
        }
        if existing is not None and existing.get("path") != record["path"]:
            raise ValueError("Obsidian path token is already bound")
        registry["paths"][normalized_token] = existing or record
        self._save(registry)
        return normalized_token

    def resolve(self, token: str) -> Path:
        normalized_token = self._token(token)
        record = self._load()["paths"].get(normalized_token)
        if record is None or not isinstance(record.get("path"), str):
            raise KeyError("Obsidian local path token is unavailable")
        return Path(record["path"])

    def display_name(self, token: str) -> str:
        normalized_token = self._token(token)
        record = self._load()["paths"].get(normalized_token)
        if record is None:
            raise KeyError("Obsidian local path token is unavailable")
        value = record.get("display_name")
        return str(value) if value else Path(str(record["path"])).name

    @staticmethod
    def _token(value: str) -> str:
        try:
            return str(UUID(value.strip()))
        except (AttributeError, ValueError) as error:
            raise ValueError("Obsidian local path token is invalid") from error

    def _load(self) -> dict[str, object]:
        if not self._path.exists():
            return {"schema_version": 1, "paths": {}}
        parsed = json.loads(self._path.read_text(encoding="utf-8"))
        if (
            not isinstance(parsed, dict)
            or parsed.get("schema_version") != 1
            or not isinstance(parsed.get("paths"), dict)
        ):
            raise ValueError("Obsidian path registry is invalid")
        return parsed

    def _save(self, registry: dict[str, object]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(f".{self._path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(registry, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, self._path)


__all__ = ["ObsidianPathRegistry"]
