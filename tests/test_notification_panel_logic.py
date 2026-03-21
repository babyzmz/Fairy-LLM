from __future__ import annotations

import unittest
from pathlib import Path

from app.storage.db import AppDatabase
from app.system_notifications.notification_model import SystemNotification
from app.system_notifications.notification_presenter import notification_counts, notification_matches_filter
from app.system_notifications.notification_store import NotificationStore


class NotificationPanelLogicTest(unittest.TestCase):
    def setUp(self) -> None:
        self.test_dir = Path("data/test_notification_panel_logic")
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self.db = AppDatabase(self.test_dir / "fairy_test.db")
        self.store = NotificationStore(self.db)

    def tearDown(self) -> None:
        for file_path in self.test_dir.glob("*"):
            if file_path.is_file():
                file_path.unlink(missing_ok=True)

    def _upsert(self, *, notification_id: str, canonical_key: str, level: str, snooze_until: str = "", completed: bool = False, dismissed: bool = False) -> None:
        item = SystemNotification(
            id=notification_id,
            canonical_key=canonical_key,
            type=canonical_key,
            level=level,
            title=canonical_key,
            message="test",
            created_at=self.db.now(),
            updated_at=self.db.now(),
            snooze_until=snooze_until,
        )
        stored = self.store.upsert(item)
        if completed:
            self.store.mark_completed(stored.id)
        if dismissed:
            self.store.dismiss(stored.id)

    def test_batch_dismiss_and_snooze_affect_filters(self) -> None:
        self._upsert(notification_id="n1", canonical_key="c1", level="action_required")
        self._upsert(notification_id="n2", canonical_key="c2", level="critical")
        items = [item.to_dict() for item in self.store.list_recent(limit=10)]
        counts = notification_counts(items)
        self.assertEqual(counts["active"], 2)
        self.assertEqual(counts["action_required"], 1)
        self.assertEqual(counts["critical"], 1)

        ids = [item.id for item in self.store.list_recent(limit=10)]
        self.store.snooze_many(ids[:1], until_iso="2999-01-01T00:00:00+00:00")
        self.store.dismiss_many(ids[1:])
        items = [item.to_dict() for item in self.store.list_recent(limit=10)]
        counts = notification_counts(items)
        self.assertEqual(counts["active"], 0)
        self.assertEqual(counts["snoozed"], 1)
        self.assertEqual(counts["resolved"], 1)

    def test_filters_do_not_mutate_state(self) -> None:
        self._upsert(notification_id="n1", canonical_key="c1", level="action_required")
        self._upsert(notification_id="n2", canonical_key="c2", level="critical")
        items = [item.to_dict() for item in self.store.list_recent(limit=10)]
        critical_items = [item for item in items if notification_matches_filter(item, "critical")]
        action_items = [item for item in items if notification_matches_filter(item, "action_required")]
        self.assertEqual(len(critical_items), 1)
        self.assertEqual(len(action_items), 1)
        counts = notification_counts(items)
        self.assertEqual(counts["active"], 2)

    def test_batch_completed_marks_resolved(self) -> None:
        self._upsert(notification_id="n1", canonical_key="c1", level="suggestion")
        items = self.store.list_recent(limit=10)
        self.store.mark_completed_many([item.id for item in items])
        recent = [item.to_dict() for item in self.store.list_recent(limit=10)]
        counts = notification_counts(recent)
        self.assertEqual(counts["resolved"], 1)


if __name__ == "__main__":
    unittest.main()
