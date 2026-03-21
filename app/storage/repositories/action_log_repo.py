from __future__ import annotations

import json
import uuid
from typing import Any

from app.storage.db import AppDatabase, get_app_database


class ActionLogRepo:
    def __init__(self, db: AppDatabase | None = None) -> None:
        self.db = db or get_app_database()

    def add_event(self, *, session_id: str | None, event_type: str, event_data: dict[str, Any] | None = None) -> str:
        action_id = f"act_{uuid.uuid4().hex[:14]}"
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO action_logs (id, session_id, event_type, event_data_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    action_id,
                    session_id,
                    event_type,
                    json.dumps(event_data or {}, ensure_ascii=False),
                    self.db.now(),
                ),
            )
        return action_id

    def list_by_session(self, session_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM action_logs
                WHERE session_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (session_id, int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]
