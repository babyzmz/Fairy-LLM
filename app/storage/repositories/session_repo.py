from __future__ import annotations

import uuid
from typing import Any

from app.storage.db import AppDatabase, get_app_database


class SessionRepo:
    def __init__(self, db: AppDatabase | None = None) -> None:
        self.db = db or get_app_database()

    def create_session(self, *, title: str, mode: str, provider: str, model: str) -> str:
        session_id = f"session_{uuid.uuid4().hex[:12]}"
        now = self.db.now()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (id, title, mode, provider, model, created_at, updated_at, archived)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (session_id, title, mode, provider, model, now, now),
            )
        return session_id

    def update_runtime(self, session_id: str, *, mode: str, provider: str, model: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE sessions
                SET mode = ?, provider = ?, model = ?, updated_at = ?
                WHERE id = ?
                """,
                (mode, provider, model, self.db.now(), session_id),
            )

    def touch(self, session_id: str) -> None:
        with self.db.connect() as conn:
            conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (self.db.now(), session_id))

    def rename_if_default(self, session_id: str, title: str) -> None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT title FROM sessions WHERE id = ? LIMIT 1", (session_id,)).fetchone()
            if row is None:
                return
            current = str(row["title"] or "")
            if current.startswith("Fairy Session"):
                conn.execute("UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?", (title, self.db.now(), session_id))

    def get(self, session_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id = ? LIMIT 1", (session_id,)).fetchone()
        return dict(row) if row else None
