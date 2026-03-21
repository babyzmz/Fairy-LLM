from __future__ import annotations

import json
import math
import uuid
from typing import Any

from app.storage.db import AppDatabase, get_app_database


class MessageRepo:
    def __init__(self, db: AppDatabase | None = None) -> None:
        self.db = db or get_app_database()

    def add_message(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        content_type: str = "text",
        token_estimate: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        message_id = f"msg_{uuid.uuid4().hex[:14]}"
        estimate = int(token_estimate if token_estimate is not None else max(1, math.ceil(len(content) / 1.8)))
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO messages (id, session_id, role, content, content_type, token_estimate, created_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    session_id,
                    role,
                    content,
                    content_type,
                    estimate,
                    self.db.now(),
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
        return message_id

    def list_recent(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM messages
                WHERE session_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (session_id, int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]
