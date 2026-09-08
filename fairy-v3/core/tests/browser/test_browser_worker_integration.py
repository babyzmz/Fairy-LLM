from __future__ import annotations

import os
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from fairy_core.browser import BrowserService
from fairy_core.contracts.browser import BrowserSessionListInput, BrowserSessionStartInput
from fairy_core.contracts.browser import BrowserSessionStatus as Status
from fairy_core.workspace.worker_transport import SubprocessWorkerTransport


@pytest.mark.skipif(
    os.environ.get("FAIRY_TEST_REAL_BROWSER") != "1",
    reason="Opt-in real headless Edge Worker; not a Tauri WebView2 test",
)
def test_real_worker_twenty_conversation_switches_idle_release_and_restore(tmp_path):
    node = shutil.which("node")
    assert node is not None, "Node is required for the real Browser gate"
    desktop = Path(__file__).resolve().parents[3] / "desktop"
    worker = SubprocessWorkerTransport(
        program=node,
        args=(str(desktop / "browser-worker.mjs"),),
        current_directory=desktop,
        environment={},
        request_timeout_seconds=30,
    )
    now = [0.0]
    state_path = tmp_path / "sessions.json"
    browser = BrowserService(
        worker=worker,
        state_path=state_path,
        profile_root=tmp_path / "profiles",
        clock=lambda: now[0],
    )
    conversations = (uuid4(), uuid4())
    sessions = []
    try:
        assert browser.health().available
        for index in range(20):
            now[0] = float(index)
            session = browser.start(
                BrowserSessionStartInput(
                    conversation_id=conversations[index % 2],
                    idempotency_key=f"switch-{index}",
                )
            )
            assert session.status is Status.ACTIVE, session.public_error
            sessions.append(session)
            residents = [
                item
                for item in browser.list(BrowserSessionListInput()).items
                if item.status is Status.ACTIVE
            ]
            assert len(residents) == min(index + 1, 4)
        now[0] = 319
        browser.reap_idle_resources()
        assert all(browser.get(item.id).status is Status.SUSPENDED for item in sessions)
        resumed = browser.resume(sessions[0].id)
        assert resumed.status is Status.ACTIVE, resumed.public_error
        assert resumed.id != sessions[0].id
        assert resumed.active_tab_id != sessions[0].active_tab_id
        assert resumed.conversation_id == conversations[0]
        assert browser.get(sessions[1].id).conversation_id == conversations[1]
    finally:
        browser.close()
    restored = BrowserService(
        worker=None, state_path=state_path, profile_root=tmp_path / "profiles"
    )
    try:
        assert restored.get(sessions[0].id).tabs == sessions[0].tabs
        assert restored.get(sessions[1].id).status is Status.SUSPENDED
    finally:
        restored.close()
