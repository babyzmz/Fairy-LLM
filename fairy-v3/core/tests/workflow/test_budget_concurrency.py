from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import pytest

from fairy_core.workflow.errors import WorkflowBudgetExceeded
from fairy_core.workflow.models import WorkflowBudget
from fairy_core.workflow.repository import SqlAlchemyWorkflowRepository
from tests.workflow.test_repository import _factory, _node, _run


@pytest.mark.parametrize("capacity", [1, 2])
def test_concurrent_budget_reservations_count_every_admitted_call(tmp_path, monkeypatch, capacity):
    factory = _factory(tmp_path / "budget.db")
    run = _run(
        "simultaneous", budget=replace(WorkflowBudget.normal(), max_tool_invocations=capacity)
    )
    with factory() as unit:
        unit.workflows.create(run, nodes=(_node(run, "pending"),), edges=())
        unit.commit()
    barrier = Barrier(2)
    original = SqlAlchemyWorkflowRepository._locked_run

    def locked(repository, run_id):
        row = original(repository, run_id)
        barrier.wait(timeout=5)
        return row

    monkeypatch.setattr(SqlAlchemyWorkflowRepository, "_locked_run", locked)

    def reserve():
        with factory() as unit:
            try:
                unit.workflows.reserve_budget(run.id, tool_invocations=1)
                unit.commit()
                return True
            except WorkflowBudgetExceeded:
                return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(lambda _: reserve(), range(2)))
    with factory() as unit:
        snapshot = unit.workflows.get(run.id)
    assert sum(results) == capacity
    assert snapshot.run.tool_invocations_used == capacity
