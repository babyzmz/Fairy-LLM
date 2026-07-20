from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection

from fairy_core.domain.errors import IdempotencyConflictError, VersionConflictError
from fairy_core.memory.settings import MemorySettings
from fairy_core.persistence.tenant import normalize_tenant_id
from fairy_core.storage.schema import memory_setting_updates, memory_settings

Clock = Callable[[], datetime]


class SqlAlchemyMemorySettingsRepository:
    def __init__(
        self,
        connection: Connection,
        *,
        tenant_id: str,
        clock: Clock | None = None,
    ) -> None:
        self._connection = connection
        self._tenant_id = normalize_tenant_id(tenant_id)
        self._clock = clock or (lambda: datetime.now(UTC))

    def get(self) -> MemorySettings:
        row = (
            self._connection.execute(
                select(memory_settings).where(memory_settings.c.tenant_id == self._tenant_id)
            )
            .mappings()
            .one_or_none()
        )
        return MemorySettings.defaults(now=self._now()) if row is None else _settings(row)

    def update(
        self,
        *,
        enabled: bool,
        retention_days: int,
        export_to_obsidian: bool,
        sync_normalized_content: bool,
        expected_revision: int,
        idempotency_key: str,
    ) -> MemorySettings:
        if expected_revision < 0:
            raise ValueError("expected memory settings revision cannot be negative")
        key = _idempotency_key(idempotency_key)
        fingerprint = _request_fingerprint(
            enabled=enabled,
            retention_days=retention_days,
            export_to_obsidian=export_to_obsidian,
            sync_normalized_content=sync_normalized_content,
            expected_revision=expected_revision,
        )
        existing = self._request(key)
        if existing is not None:
            return _replayed(existing, fingerprint=fingerprint)
        changed = MemorySettings(
            enabled=enabled,
            retention_days=retention_days,
            export_to_obsidian=export_to_obsidian,
            sync_normalized_content=sync_normalized_content,
            revision=expected_revision + 1,
            updated_at=self._now(),
        )
        values = {
            "tenant_id": self._tenant_id,
            "enabled": changed.enabled,
            "retention_days": changed.retention_days,
            "export_to_obsidian": changed.export_to_obsidian,
            "sync_normalized_content": changed.sync_normalized_content,
            "revision": changed.revision,
            "updated_at": changed.updated_at,
        }
        dialect_insert = self._insert(memory_setting_updates)
        reserved = self._connection.execute(
            dialect_insert.values(
                tenant_id=self._tenant_id,
                idempotency_key=key,
                request_fingerprint=fingerprint,
                enabled=changed.enabled,
                retention_days=changed.retention_days,
                export_to_obsidian=changed.export_to_obsidian,
                sync_normalized_content=changed.sync_normalized_content,
                expected_revision=expected_revision,
                result_revision=changed.revision,
                result_updated_at=changed.updated_at,
            ).on_conflict_do_nothing(
                index_elements=[
                    memory_setting_updates.c.tenant_id,
                    memory_setting_updates.c.idempotency_key,
                ]
            )
        )
        if reserved.rowcount != 1:
            replay = self._request(key)
            if replay is None:
                raise VersionConflictError("memory settings request could not be reserved")
            return _replayed(replay, fingerprint=fingerprint)
        if expected_revision == 0:
            inserted = self._connection.execute(
                self._insert(memory_settings)
                .values(**values)
                .on_conflict_do_nothing(index_elements=[memory_settings.c.tenant_id])
            )
            if inserted.rowcount == 1:
                return changed
        updated = self._connection.execute(
            update(memory_settings)
            .where(
                memory_settings.c.tenant_id == self._tenant_id,
                memory_settings.c.revision == expected_revision,
            )
            .values(**{name: value for name, value in values.items() if name != "tenant_id"})
        )
        if updated.rowcount != 1:
            raise VersionConflictError("memory settings revision changed")
        return changed

    def _insert(self, table):
        return (
            postgresql_insert(table)
            if self._connection.dialect.name == "postgresql"
            else sqlite_insert(table)
        )

    def _request(self, key: str):
        return (
            self._connection.execute(
                select(memory_setting_updates).where(
                    memory_setting_updates.c.tenant_id == self._tenant_id,
                    memory_setting_updates.c.idempotency_key == key,
                )
            )
            .mappings()
            .one_or_none()
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("memory settings clock must be timezone-aware")
        return value.astimezone(UTC)


def _settings(row) -> MemorySettings:
    return MemorySettings(
        enabled=bool(row["enabled"]),
        retention_days=int(row["retention_days"]),
        export_to_obsidian=bool(row["export_to_obsidian"]),
        sync_normalized_content=bool(row["sync_normalized_content"]),
        revision=int(row["revision"]),
        updated_at=_aware(row["updated_at"]),
    )


def _replayed(row, *, fingerprint: str) -> MemorySettings:
    if row["request_fingerprint"] != fingerprint:
        raise IdempotencyConflictError("memory settings idempotency key was reused")
    return MemorySettings(
        enabled=bool(row["enabled"]),
        retention_days=int(row["retention_days"]),
        export_to_obsidian=bool(row["export_to_obsidian"]),
        sync_normalized_content=bool(row["sync_normalized_content"]),
        revision=int(row["result_revision"]),
        updated_at=_aware(row["result_updated_at"]),
    )


def _aware(value: datetime | str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _idempotency_key(value: str) -> str:
    if value != value.strip() or not value or len(value) > 512:
        raise ValueError("memory settings idempotency key is invalid")
    if any(ord(character) < 32 for character in value):
        raise ValueError("memory settings idempotency key is invalid")
    return value


def _request_fingerprint(**values: object) -> str:
    payload = json.dumps(values, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = ["SqlAlchemyMemorySettingsRepository"]
