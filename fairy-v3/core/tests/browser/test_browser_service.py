from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from fairy_core.browser import BrowserService
from fairy_core.commanding.registry import ApprovalPolicy, SideEffect, build_default_registry
from fairy_core.contracts.browser import (
    BrowserActionInput,
    BrowserSessionListInput,
    BrowserSessionStartInput,
    BrowserSessionStatus,
    BrowserSnapshotInput,
)


class FakeBrowserWorker:
    def __init__(self) -> None:
        self.tab_id = uuid4()
        self.calls: list[tuple[str, dict[str, object]]] = []

    def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
        self.calls.append((method, params))
        session_id = str(params.get("session_id", uuid4()))
        if method == "browser.health":
            return {
                "available": True,
                "browser_name": "Microsoft Edge",
                "browser_version": "1",
                "error_code": None,
                "diagnostic": None,
            }
        if method == "browser.snapshots.get":
            return {
                "session_id": session_id,
                "tab_id": str(self.tab_id),
                "page_revision": 2,
                "url": "about:blank",
                "title": "New tab",
                "aria_snapshot": "- document",
                "viewport_width": 1365,
                "viewport_height": 768,
                "screenshot_data_url": None,
                "captured_at": datetime.now(UTC).isoformat(),
            }
        status = "stopped" if method == "browser.sessions.stop" else "active"
        session = {
            "status": status,
            "active_tab_id": None if status == "stopped" else str(self.tab_id),
            "tabs": []
            if status == "stopped"
            else [
                {
                    "id": str(self.tab_id),
                    "session_id": session_id,
                    "title": "New tab",
                    "url": "about:blank",
                    "active": True,
                    "loading": False,
                    "revision": 2,
                }
            ],
        }
        if method == "browser.actions.execute":
            return {
                "session": session,
                "tab": session["tabs"][0],
                "public_summary": "Browser reload completed",
                "replayed": False,
            }
        return session


def service(tmp_path: Path, worker: FakeBrowserWorker | None = None) -> BrowserService:
    return BrowserService(
        worker=worker,
        state_path=tmp_path / "browser" / "sessions.json",
        profile_root=tmp_path / "browser" / "profiles",
    )


def test_browser_session_is_scoped_idempotent_and_recoverable(tmp_path: Path) -> None:
    worker = FakeBrowserWorker()
    browser = service(tmp_path, worker)
    conversation_id = uuid4()
    request = BrowserSessionStartInput(
        conversation_id=conversation_id,
        initial_url="about:blank",
        idempotency_key="browser-start",
    )

    started = browser.start(request)
    replayed = browser.start(request)

    assert started.id == replayed.id
    assert started.status is BrowserSessionStatus.ACTIVE
    assert browser.list(BrowserSessionListInput(conversation_id=conversation_id)).items == (
        started,
    )
    assert [name for name, _params in worker.calls].count("browser.sessions.start") == 1

    recovered = service(tmp_path).get(started.id)
    assert recovered.status is BrowserSessionStatus.INTERRUPTED


def test_browser_action_and_snapshot_preserve_page_revision(tmp_path: Path) -> None:
    worker = FakeBrowserWorker()
    browser = service(tmp_path, worker)
    session = browser.start(
        BrowserSessionStartInput(initial_url="about:blank", idempotency_key="browser-action")
    )
    tab_id = UUID(str(session.active_tab_id))

    action = browser.execute(
        BrowserActionInput(
            session_id=session.id,
            tab_id=tab_id,
            kind="reload",
            expected_page_revision=2,
            idempotency_key="reload-once",
        )
    )
    snapshot = browser.snapshot(
        BrowserSnapshotInput(session_id=session.id, tab_id=tab_id, include_screenshot=False)
    )

    assert action.public_summary == "Browser reload completed"
    assert snapshot.page_revision == 2
    assert snapshot.aria_snapshot == "- document"
    assert snapshot.viewport_width == 1365
    assert snapshot.viewport_height == 768


def test_browser_rejects_credential_urls_before_worker_call(tmp_path: Path) -> None:
    worker = FakeBrowserWorker()
    browser = service(tmp_path, worker)

    with pytest.raises(ValueError, match="credentials"):
        browser.start(
            BrowserSessionStartInput(
                initial_url="https://user:secret@example.com/",
                idempotency_key="blocked-url",
            )
        )

    assert all(name != "browser.sessions.start" for name, _params in worker.calls)


def test_browser_tools_use_risk_based_approval() -> None:
    tools = {
        definition.name: definition
        for definition in build_default_registry().definitions()
        if definition.name.startswith("browser.")
    }

    assert tools["browser.snapshot"].side_effect is SideEffect.READ
    assert tools["browser.snapshot"].approval_policy is ApprovalPolicy.NEVER
    assert tools["browser.click"].side_effect is SideEffect.WRITE
    assert tools["browser.click"].approval_policy is ApprovalPolicy.PROFILE
