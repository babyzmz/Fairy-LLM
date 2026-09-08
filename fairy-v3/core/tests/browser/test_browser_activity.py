from __future__ import annotations

from threading import enumerate as enumerate_threads

import pytest

from fairy_core.assistant.models import ProviderAttempt
from fairy_core.browser import BrowserService
from fairy_core.contracts.browser import BrowserSessionStartInput, BrowserSessionStatus
from fairy_core.domain.models import TaskStatus
from fairy_core.model_catalog.models import ModelEndpointKind
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from fairy_core.providers import ModelExecutionRole
from fairy_core.transports.stdio import build_local_service
from fairy_core.workspace.worker_transport import WorkerRpcError
from tests.assistant.test_repository_contract import _seed_task, _turn
from tests.browser.test_browser_service import FakeBrowserWorker


def test_browser_activity_uses_persisted_tasks_and_unsettled_cancel_across_tenants(tmp_path):
    from fairy_core.browser.activity import BrowserTaskActivityProbe

    path = tmp_path / "activity.db"
    engine = create_sqlite_core_engine(path)
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="left")
    other = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="right")
    waiting, _ = _seed_task(factory, label="waiting")
    cancelled, scope = _seed_task(factory, label="cancelled")
    idle, _ = _seed_task(factory, label="idle")
    foreign, _ = _seed_task(other, label="foreign")
    waiting.status, cancelled.status, idle.status = (
        TaskStatus.AWAITING_APPROVAL,
        TaskStatus.FAILED,
        TaskStatus.READY,
    )
    turn = _turn(cancelled, scope, key="cancelled")
    attempt = ProviderAttempt.create(
        turn=turn,
        model_round=1,
        attempt_number=1,
        profile_id="test",
        model_id="test",
        endpoint_kind=ModelEndpointKind.CHAT,
        model_role=ModelExecutionRole.PRIMARY,
    )
    turn.cancel()
    with factory() as unit:
        for task in (waiting, cancelled, idle):
            unit.state.save_task(task)
        unit.assistant.save_turn(turn)
        unit.assistant.save_provider_attempt(attempt)
        unit.commit()
    engine.dispose()
    engine = create_sqlite_core_engine(path)
    try:
        ids = (waiting.id, cancelled.id, idle.id, foreign.id)
        left_probe = BrowserTaskActivityProbe(SqlAlchemyUnitOfWorkFactory(engine, tenant_id="left"))
        right_probe = BrowserTaskActivityProbe(
            SqlAlchemyUnitOfWorkFactory(engine, tenant_id="right")
        )
        # Unknown foreign bindings fail closed, never borrowing another tenant's idle state.
        assert left_probe(ids) == frozenset((waiting.id, cancelled.id, foreign.id))
        assert right_probe(ids) == frozenset(ids)
        assert left_probe((idle.id,)) == frozenset()
    finally:
        engine.dispose()


def test_local_core_owns_browser_idle_policy_without_startup_warmup(tmp_path, monkeypatch):
    worker = FakeBrowserWorker()
    now = [0.0]
    browsers = []

    def browser_factory(**options):
        options["worker"] = worker
        browser = BrowserService(**options, clock=lambda: now[0])
        browsers.append(browser)
        return browser

    monkeypatch.setattr("fairy_core.transports.stdio.BrowserService", browser_factory)
    before = set(enumerate_threads())
    service = build_local_service(tmp_path, environment={})
    engine = create_sqlite_core_engine(tmp_path / "core.db")
    try:
        assert worker.calls == []
        assert not any(
            t.name == "fairy-browser-resources" for t in set(enumerate_threads()) - before
        )
        factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
        task, _ = _seed_task(factory, label="idle-browser")
        task.status = TaskStatus.READY
        with factory() as unit:
            unit.state.save_task(task)
            unit.commit()
        browser = browsers[0]
        session = browser.start(
            BrowserSessionStartInput(
                task_id=task.id,
                conversation_id=task.conversation_id,
                idempotency_key="core-idle",
            )
        )
        now[0] = 301
        browser.reap_idle_resources()
        assert browser.get(session.id).status is BrowserSessionStatus.SUSPENDED
    finally:
        service.close()
        engine.dispose()
    assert not any(t.name == "fairy-browser-resources" for t in set(enumerate_threads()) - before)
    with pytest.raises(WorkerRpcError, match="shutting down"):
        browsers[0].start(BrowserSessionStartInput(idempotency_key="after-close"))
