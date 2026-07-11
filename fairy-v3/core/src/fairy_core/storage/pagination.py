from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

_CURSOR_VERSION = 1
_MAX_CURSOR_LENGTH = 2_048

@dataclass(frozen=True, slots=True)
class StatePage[T]:
    items: tuple[T, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class CursorPosition:
    created_at: datetime
    entity_id: UUID


def encode_cursor(
    *,
    collection: str,
    created_at: datetime,
    entity_id: UUID,
    scope: Mapping[str, str | None],
) -> str:
    normalized_time = _utc(created_at)
    payload = {
        "v": _CURSOR_VERSION,
        "c": collection,
        "t": normalized_time.isoformat(),
        "i": str(entity_id),
        "s": _scope_digest(collection, scope),
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return base64.urlsafe_b64encode(serialized).rstrip(b"=").decode("ascii")


def decode_cursor(
    cursor: str | None,
    *,
    collection: str,
    scope: Mapping[str, str | None],
) -> CursorPosition | None:
    if cursor is None:
        return None
    if not cursor or len(cursor) > _MAX_CURSOR_LENGTH:
        raise ValueError("cursor is invalid")
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = base64.b64decode(
            cursor + padding,
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(decoded)
        if not isinstance(payload, dict) or set(payload) != {"v", "c", "t", "i", "s"}:
            raise ValueError
        if payload["v"] != _CURSOR_VERSION or payload["c"] != collection:
            raise ValueError
        expected_scope = _scope_digest(collection, scope)
        if not isinstance(payload["s"], str) or not hmac.compare_digest(
            payload["s"],
            expected_scope,
        ):
            raise ValueError
        created_at = datetime.fromisoformat(payload["t"])
        if created_at.tzinfo is None:
            raise ValueError
        entity_id = UUID(payload["i"])
    except (
        binascii.Error,
        json.JSONDecodeError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
    ) as error:
        raise ValueError("cursor is invalid for this collection scope") from error
    return CursorPosition(created_at=_utc(created_at), entity_id=entity_id)


def validate_limit(limit: int) -> None:
    if isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")


def _scope_digest(collection: str, scope: Mapping[str, str | None]) -> str:
    canonical = json.dumps(
        {"collection": collection, "scope": dict(scope)},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
