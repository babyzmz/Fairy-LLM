from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event

from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from fairy_core.workflow.models import WorkflowRunStatus
from tests.workflow.test_repository import _node, _run


@pytest.mark.parametrize("finish", ["defer", "retry"])
def test_last_active_wait_or_retry_settles_requested_pause_after_reopen(tmp_path, finish):
    path = tmp_path / "pause.db"
    engine = create_sqlite_core_engine(path)
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    run, claim = _claimed(factory)
    with factory() as unit:
        unit.workflows.request_pause(run.id)
        unit.commit()
    with factory() as unit:
        if finish == "defer":
            unit.workflows.defer(
                claim, available_at=datetime.now(UTC) + timedelta(seconds=1), result={}
            )
        else:
            unit.workflows.retry(
                claim, available_at=datetime.now(UTC) + timedelta(seconds=1), error_code="TRANSIENT"
            )
        unit.commit()
    engine.dispose()
    engine = create_sqlite_core_engine(path)
    try:
        with SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")() as unit:
            snapshot = unit.workflows.get(run.id)
        assert snapshot.run.status is WorkflowRunStatus.PAUSED
        assert snapshot.run.pause_requested
    finally:
        engine.dispose()


def test_pause_does_not_use_activity_read_before_a_concurrent_defer(tmp_path):
    engine = create_sqlite_core_engine(tmp_path / "race.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    run, claim = _claimed(factory)
    armed = [True]

    def defer_before_pause_write(connection, cursor, statement, parameters, context, executemany):
        if armed[0] and statement.startswith("UPDATE core_workflow_runs"):
            armed[0] = False
            with factory() as other:
                other.workflows.defer(
                    claim,
                    available_at=datetime.now(UTC) + timedelta(seconds=1),
                    result={},
                )
                other.commit()

    event.listen(engine, "before_cursor_execute", defer_before_pause_write)
    try:
        with factory() as unit:
            snapshot = unit.workflows.request_pause(run.id)
            unit.commit()
        assert not armed[0]
        assert snapshot.run.status is WorkflowRunStatus.PAUSED
        assert snapshot.run.pause_requested
    finally:
        event.remove(engine, "before_cursor_execute", defer_before_pause_write)
        engine.dispose()


def _claimed(factory):
    run = _run("pause-race")
    with factory() as unit:
        unit.workflows.create(run, nodes=(_node(run, "wait"),), edges=())
        claims = unit.workflows.claim_ready(
            worker_id="test", lease_until=datetime.now(UTC) + timedelta(seconds=30), limit=1
        )
        unit.commit()
    return run, claims[0]
