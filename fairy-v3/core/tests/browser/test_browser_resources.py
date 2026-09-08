from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest

from fairy_core.browser import BrowserService
from fairy_core.contracts.browser import (
    BrowserSessionListInput,
    BrowserSessionStartInput,
    BrowserSessionStatus,
    BrowserSnapshotInput,
    BrowserTabOpenInput,
)
from fairy_core.workspace.worker_transport import WorkerRpcError
from tests.browser.test_browser_service import FakeBrowserWorker


def test_browser_capacity_suspends_oldest_idle_session_and_preserves_recovery(tmp_path):
    worker = FakeBrowserWorker()
    browser = BrowserService(
        worker=worker,
        state_path=tmp_path / "sessions.json",
        profile_root=tmp_path / "profiles",
    )
    sessions = [
        browser.start(
            BrowserSessionStartInput(
                conversation_id=uuid4(),
                idempotency_key=f"start-{index}",
            )
        )
        for index in range(5)
    ]
    try:
        first = browser.get(sessions[0].id)
        assert first.status is BrowserSessionStatus.SUSPENDED
        assert first.tabs == sessions[0].tabs
        assert (
            sum(
                item.status is BrowserSessionStatus.ACTIVE
                for item in browser.list(
                    BrowserSessionListInput(),
                ).items
            )
            == 4
        )
        assert browser.get(sessions[-1].id).status is BrowserSessionStatus.ACTIVE
        recovered = BrowserService(
            worker=None,
            state_path=tmp_path / "sessions.json",
            profile_root=tmp_path / "profiles",
        )
        assert recovered.get(first.id).tabs == first.tabs
        resumed = browser.resume(first.id)
        assert resumed.id != first.id
        assert resumed.status is BrowserSessionStatus.ACTIVE
    finally:
        close = getattr(browser, "close", None)
        if close is not None:
            close()


def test_idle_browser_deadline_resets_on_snapshot_and_stop_needs_no_worker(tmp_path):
    now = [0.0]
    worker = FakeBrowserWorker()
    browser = BrowserService(
        worker=worker,
        state_path=tmp_path / "sessions.json",
        profile_root=tmp_path / "profiles",
        clock=lambda: now[0],
    )
    try:
        session = browser.start(BrowserSessionStartInput(idempotency_key="idle"))
        now[0] = 299
        browser.reap_idle_resources()
        assert browser.get(session.id).status is BrowserSessionStatus.ACTIVE
        browser.snapshot(BrowserSnapshotInput(session_id=session.id, tab_id=session.active_tab_id))
        now[0] = 598
        browser.reap_idle_resources()
        assert browser.get(session.id).status is BrowserSessionStatus.ACTIVE
        now[0] = 599
        browser.reap_idle_resources()
        assert browser.get(session.id).status is BrowserSessionStatus.SUSPENDED
        assert browser.get(session.id).tabs == session.tabs
        calls_before_stop = len(worker.calls)
        assert browser.stop(session.id).status is BrowserSessionStatus.STOPPED
        assert len(worker.calls) == calls_before_stop
        assert browser.get(session.id).tabs == ()
    finally:
        browser.close()


def test_idle_browser_is_pinned_when_activity_probe_fails(tmp_path):
    now = [0.0]
    worker = FakeBrowserWorker()
    browser = BrowserService(
        worker=worker,
        state_path=tmp_path / "sessions.json",
        profile_root=tmp_path / "profiles",
        clock=lambda: now[0],
    )

    def unavailable(_ids):
        raise RuntimeError("activity store offline")

    browser.configure_activity_probe(unavailable, maintenance=False)
    try:
        session = browser.start(
            BrowserSessionStartInput(
                task_id=uuid4(),
                idempotency_key="busy",
            )
        )
        now[0] = 600
        browser.reap_idle_resources()
        assert browser.get(session.id).status is BrowserSessionStatus.ACTIVE
        browser.configure_activity_probe(lambda ids: frozenset(ids), maintenance=False)
        browser.reap_idle_resources()
        assert browser.get(session.id).status is BrowserSessionStatus.ACTIVE
        browser.configure_activity_probe(lambda _ids: frozenset(), maintenance=False)
        browser.reap_idle_resources()
        assert browser.get(session.id).status is BrowserSessionStatus.SUSPENDED
    finally:
        browser.close()


def test_browser_capacity_keeps_unknown_task_activity_pinned(tmp_path):
    worker = FakeBrowserWorker()
    browser = BrowserService(
        worker=worker,
        state_path=tmp_path / "sessions.json",
        profile_root=tmp_path / "profiles",
    )
    sessions = [
        browser.start(
            BrowserSessionStartInput(
                conversation_id=uuid4(),
                task_id=uuid4(),
                idempotency_key=f"pinned-{index}",
            )
        )
        for index in range(4)
    ]
    try:
        with pytest.raises(WorkerRpcError) as error:
            browser.start(BrowserSessionStartInput(idempotency_key="no-capacity"))
        assert error.value.error_code == "BROWSER_CAPACITY_EXCEEDED"
        assert all(browser.get(item.id).status is BrowserSessionStatus.ACTIVE for item in sessions)
        assert not any(method == "browser.sessions.stop" for method, _ in worker.calls)
    finally:
        close = getattr(browser, "close", None)
        if close is not None:
            close()


def test_browser_close_interrupts_inflight_idle_release(tmp_path):
    entered, released = Event(), Event()

    class HangingStopWorker(FakeBrowserWorker):
        def call(self, method, params):
            if method == "browser.sessions.stop":
                entered.set()
                if not released.wait(3):
                    raise RuntimeError("stop did not finish")
                raise WorkerRpcError("worker closed")
            return super().call(method, params)

        def close(self):
            released.set()

    now = [0.0]
    browser = BrowserService(
        worker=HangingStopWorker(),
        state_path=tmp_path / "sessions.json",
        profile_root=tmp_path / "profiles",
        clock=lambda: now[0],
    )
    session = browser.start(BrowserSessionStartInput(idempotency_key="blocked-stop"))
    now[0] = 301
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(browser.reap_idle_resources)
        try:
            assert entered.wait(1)
            browser.close()
            assert released.is_set(), "Browser owner did not close its blocked Worker"
            with pytest.raises(WorkerRpcError, match="closed"):
                pending.result(timeout=1)
            assert browser.get(session.id).status is BrowserSessionStatus.ACTIVE
        finally:
            released.set()
            browser.close()


def test_failed_resource_release_keeps_capacity_reserved(tmp_path):
    class UnconfirmedStopWorker(FakeBrowserWorker):
        def call(self, method, params):
            if method == "browser.sessions.stop":
                return {"status": "active", "tabs": []}
            return super().call(method, params)

    browser = BrowserService(
        worker=UnconfirmedStopWorker(),
        state_path=tmp_path / "sessions.json",
        profile_root=tmp_path / "profiles",
    )
    try:
        sessions = [
            browser.start(
                BrowserSessionStartInput(
                    idempotency_key=f"retained-{index}",
                )
            )
            for index in range(4)
        ]
        with pytest.raises(WorkerRpcError) as error:
            browser.start(BrowserSessionStartInput(idempotency_key="fifth"))
        assert error.value.error_code == "BROWSER_CAPACITY_EXCEEDED"
        assert all(browser.get(item.id).status is BrowserSessionStatus.ACTIVE for item in sessions)
        assert len(browser.list(BrowserSessionListInput()).items) == 4
    finally:
        browser.close()


def test_invalid_tab_request_does_not_evict_other_conversations(tmp_path):
    class ThreeTabWorker(FakeBrowserWorker):
        def call(self, method, params):
            result = super().call(method, params)
            if method == "browser.sessions.start":
                tab = result["tabs"][0]
                result["tabs"].extend(
                    [
                        {**tab, "id": str(uuid4()), "active": False},
                        {**tab, "id": str(uuid4()), "active": False},
                    ]
                )
            return result

    browser = BrowserService(
        worker=ThreeTabWorker(),
        state_path=tmp_path / "sessions.json",
        profile_root=tmp_path / "profiles",
    )
    try:
        sessions = [
            browser.start(
                BrowserSessionStartInput(
                    conversation_id=uuid4(),
                    idempotency_key=f"full-{index}",
                )
            )
            for index in range(4)
        ]
        with pytest.raises(ValueError):
            browser.open_tab(BrowserTabOpenInput(session_id=sessions[-1].id, url="file:///blocked"))
        assert all(browser.get(item.id).status is BrowserSessionStatus.ACTIVE for item in sessions)
        with pytest.raises(KeyError):
            browser.open_tab(BrowserTabOpenInput(session_id=uuid4(), url="about:blank"))
        assert all(browser.get(item.id).status is BrowserSessionStatus.ACTIVE for item in sessions)
    finally:
        browser.close()
