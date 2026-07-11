from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection

from fairy_core.commanding.settings import ExecutionSettings
from fairy_core.commanding.types import PermissionProfile
from fairy_core.domain.errors import IdempotencyConflictError, VersionConflictError
from fairy_core.persistence.tenant import normalize_tenant_id
from fairy_core.storage.schema import execution_setting_updates, execution_settings

Clock = Callable[[], datetime]


class SqlAlchemyExecutionSettingsRepository:
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

    def get(self) -> ExecutionSettings:
        row = (
            self._connection.execute(
                select(execution_settings).where(execution_settings.c.tenant_id == self._tenant_id)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return ExecutionSettings.defaults(now=self._now())
        return ExecutionSettings(
            profile=PermissionProfile(row["profile"]),
            capability_overrides=dict(row["capability_overrides"]),
            revision=int(row["revision"]),
            updated_at=_aware(row["updated_at"]),
        )

    def update(
        self,
        *,
        profile: PermissionProfile,
        capability_overrides: Mapping[str, bool],
        expected_revision: int,
        idempotency_key: str,
    ) -> ExecutionSettings:
        if expected_revision < 0:
            raise ValueError("expected execution settings revision cannot be negative")
        canonical_key = _idempotency_key(idempotency_key)
        normalized_overrides = dict(sorted(capability_overrides.items()))
        fingerprint = _request_fingerprint(
            profile=profile,
            capability_overrides=normalized_overrides,
            expected_revision=expected_revision,
        )
        existing = self._request(canonical_key)
        if existing is not None:
            return _replayed_settings(existing, fingerprint=fingerprint)
        now = self._now()
        next_settings = ExecutionSettings(
            profile=profile,
            capability_overrides=normalized_overrides,
            revision=expected_revision + 1,
            updated_at=now,
        )
        values = {
            "tenant_id": self._tenant_id,
            "profile": next_settings.profile.value,
            "capability_overrides": dict(next_settings.capability_overrides),
            "revision": next_settings.revision,
            "updated_at": next_settings.updated_at,
        }
        dialect_insert = (
            postgresql_insert(execution_setting_updates)
            if self._connection.dialect.name == "postgresql"
            else sqlite_insert(execution_setting_updates)
        )
        reserved = self._connection.execute(
            dialect_insert.values(
                tenant_id=self._tenant_id,
                idempotency_key=canonical_key,
                request_fingerprint=fingerprint,
                profile=next_settings.profile.value,
                capability_overrides=dict(next_settings.capability_overrides),
                expected_revision=expected_revision,
                result_revision=next_settings.revision,
                result_updated_at=next_settings.updated_at,
            ).on_conflict_do_nothing(
                index_elements=[
                    execution_setting_updates.c.tenant_id,
                    execution_setting_updates.c.idempotency_key,
                ]
            )
        )
        if reserved.rowcount != 1:
            replay = self._request(canonical_key)
            if replay is None:
                raise VersionConflictError("execution settings request could not be reserved")
            return _replayed_settings(replay, fingerprint=fingerprint)
        if expected_revision == 0:
            dialect_insert = (
                postgresql_insert(execution_settings)
                if self._connection.dialect.name == "postgresql"
                else sqlite_insert(execution_settings)
            )
            inserted = self._connection.execute(
                dialect_insert.values(**values).on_conflict_do_nothing(
                    index_elements=[execution_settings.c.tenant_id]
                )
            )
            if inserted.rowcount == 1:
                return next_settings

        changed = self._connection.execute(
            update(execution_settings)
            .where(
                execution_settings.c.tenant_id == self._tenant_id,
                execution_settings.c.revision == expected_revision,
            )
            .values(
                profile=next_settings.profile.value,
                capability_overrides=dict(next_settings.capability_overrides),
                revision=next_settings.revision,
                updated_at=next_settings.updated_at,
            )
        )
        if changed.rowcount != 1:
            raise VersionConflictError("execution settings revision changed")
        return next_settings

    def _request(self, idempotency_key: str):
        return (
            self._connection.execute(
                select(execution_setting_updates).where(
                    execution_setting_updates.c.tenant_id == self._tenant_id,
                    execution_setting_updates.c.idempotency_key == idempotency_key,
                )
            )
            .mappings()
            .one_or_none()
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("execution settings clock must be timezone-aware")
        return value.astimezone(UTC)


def _aware(value: datetime | str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _idempotency_key(value: str) -> str:
    if value != value.strip() or not value or len(value) > 512:
        raise ValueError("execution settings idempotency key is invalid")
    if any(ord(character) < 32 for character in value):
        raise ValueError("execution settings idempotency key is invalid")
    return value


def _request_fingerprint(
    *,
    profile: PermissionProfile,
    capability_overrides: Mapping[str, bool],
    expected_revision: int,
) -> str:
    payload = json.dumps(
        {
            "profile": profile.value,
            "capability_overrides": dict(capability_overrides),
            "expected_revision": expected_revision,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _replayed_settings(row, *, fingerprint: str) -> ExecutionSettings:
    if row["request_fingerprint"] != fingerprint:
        raise IdempotencyConflictError("execution settings idempotency key was reused")
    return ExecutionSettings(
        profile=PermissionProfile(row["profile"]),
        capability_overrides=dict(row["capability_overrides"]),
        revision=int(row["result_revision"]),
        updated_at=_aware(row["result_updated_at"]),
    )


__all__ = ["SqlAlchemyExecutionSettingsRepository"]
