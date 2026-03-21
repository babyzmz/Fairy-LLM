from __future__ import annotations

import json
import uuid
from typing import Iterable

from app.storage.db import AppDatabase, get_app_database
from app.system_notifications.notification_model import SystemNotification


class NotificationStore:
    def __init__(self, db: AppDatabase | None = None) -> None:
        self.db = db or get_app_database()

    def list_recent(self, *, limit: int = 100) -> list[SystemNotification]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM system_notifications
                ORDER BY updated_at DESC, created_at DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [SystemNotification.from_row(dict(row)) for row in rows]

    def list_active(self, *, limit: int = 50) -> list[SystemNotification]:
        now = self.db.now()
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM system_notifications
                WHERE is_completed = 0
                  AND is_dismissed = 0
                  AND (snooze_until IS NULL OR snooze_until = '' OR snooze_until <= ?)
                  AND (expires_at IS NULL OR expires_at = '' OR expires_at > ?)
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (now, now, int(limit)),
            ).fetchall()
        return [SystemNotification.from_row(dict(row)) for row in rows]

    def get(self, notification_id: str) -> SystemNotification | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM system_notifications WHERE id = ? LIMIT 1",
                (notification_id,),
            ).fetchone()
        return SystemNotification.from_row(dict(row)) if row else None

    def dismiss(self, notification_id: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE system_notifications SET is_dismissed = 1, updated_at = ? WHERE id = ?",
                (self.db.now(), notification_id),
            )

    def dismiss_many(self, notification_ids: Iterable[str]) -> None:
        now = self.db.now()
        with self.db.connect() as conn:
            for notification_id in notification_ids:
                conn.execute(
                    "UPDATE system_notifications SET is_dismissed = 1, updated_at = ? WHERE id = ?",
                    (now, str(notification_id)),
                )

    def mark_completed(self, notification_id: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE system_notifications SET is_completed = 1, updated_at = ? WHERE id = ?",
                (self.db.now(), notification_id),
            )

    def mark_completed_many(self, notification_ids: Iterable[str]) -> None:
        now = self.db.now()
        with self.db.connect() as conn:
            for notification_id in notification_ids:
                conn.execute(
                    "UPDATE system_notifications SET is_completed = 1, updated_at = ? WHERE id = ?",
                    (now, str(notification_id)),
                )

    def snooze(self, notification_id: str, *, until_iso: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE system_notifications
                SET snooze_until = ?, updated_at = ?, is_dismissed = 0
                WHERE id = ?
                """,
                (until_iso, self.db.now(), notification_id),
            )

    def snooze_many(self, notification_ids: Iterable[str], *, until_iso: str) -> None:
        now = self.db.now()
        with self.db.connect() as conn:
            for notification_id in notification_ids:
                conn.execute(
                    """
                    UPDATE system_notifications
                    SET snooze_until = ?, updated_at = ?, is_dismissed = 0
                    WHERE id = ?
                    """,
                    (until_iso, now, str(notification_id)),
                )

    def upsert(self, notification: SystemNotification) -> SystemNotification:
        existing = self._get_by_canonical_key(notification.canonical_key)
        now = self.db.now()
        if existing is None:
            notification_id = notification.id or f"ntf_{uuid.uuid4().hex[:14]}"
            with self.db.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO system_notifications (
                        id, canonical_key, type, level, title, message,
                        created_at, updated_at, expires_at,
                        related_entity_type, related_entity_id,
                        is_dismissed, is_completed, snooze_until, metadata_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        notification_id,
                        notification.canonical_key,
                        notification.type,
                        notification.level,
                        notification.title,
                        notification.message,
                        notification.created_at or now,
                        now,
                        notification.expires_at or None,
                        notification.related_entity_type,
                        notification.related_entity_id,
                        0,
                        0,
                        notification.snooze_until or None,
                        json.dumps(notification.metadata or {}, ensure_ascii=False),
                    ),
                )
            created = self.get(notification_id)
            assert created is not None
            return created

        reset_state = existing.is_completed
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE system_notifications
                SET type = ?, level = ?, title = ?, message = ?, updated_at = ?,
                    expires_at = ?, related_entity_type = ?, related_entity_id = ?,
                    is_completed = ?, is_dismissed = ?, snooze_until = ?, metadata_json = ?
                WHERE canonical_key = ?
                """,
                (
                    notification.type,
                    notification.level,
                    notification.title,
                    notification.message,
                    now,
                    notification.expires_at or None,
                    notification.related_entity_type,
                    notification.related_entity_id,
                    0 if reset_state else int(existing.is_completed),
                    0 if reset_state else int(existing.is_dismissed),
                    None if reset_state else (existing.snooze_until or None),
                    json.dumps(notification.metadata or {}, ensure_ascii=False),
                    notification.canonical_key,
                ),
            )
        updated = self._get_by_canonical_key(notification.canonical_key)
        assert updated is not None
        return updated

    def sync(self, *, candidates: Iterable[SystemNotification], managed_types: set[str]) -> list[SystemNotification]:
        active_keys: set[str] = set()
        for candidate in candidates:
            active_keys.add(candidate.canonical_key)
            self.upsert(candidate)
        if managed_types:
            with self.db.connect() as conn:
                rows = conn.execute(
                    f"""
                    SELECT id, canonical_key FROM system_notifications
                    WHERE type IN ({", ".join("?" for _ in managed_types)})
                      AND is_completed = 0
                    """,
                    list(managed_types),
                ).fetchall()
                for row in rows:
                    if str(row["canonical_key"] or "") not in active_keys:
                        conn.execute(
                            "UPDATE system_notifications SET is_completed = 1, updated_at = ? WHERE id = ?",
                            (self.db.now(), str(row["id"] or "")),
                        )
        return self.list_active()

    def _get_by_canonical_key(self, canonical_key: str) -> SystemNotification | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM system_notifications WHERE canonical_key = ? LIMIT 1",
                (canonical_key,),
            ).fetchone()
        return SystemNotification.from_row(dict(row)) if row else None
