from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import event

from fairy_core.workflow.models import WorkflowConcurrencyPolicy
from tests.workflow.test_repository import _factory, _node, _run


def test_ready_candidates_are_sql_bounded_and_fair_between_runs(tmp_path):
    factory = _factory(tmp_path / "fair.db")
    crowded = _run("crowded")
    others = tuple(_run(f"other-{index}") for index in range(3))
    queries = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        if (
            statement.lstrip().upper().startswith("SELECT")
            and "core_workflow_nodes.payload" in statement
            and "JOIN core_workflow_runs" in statement
        ):
            queries.append(statement)

    try:
        with factory() as unit:
            unit.workflows.create(
                crowded,
                nodes=tuple(
                    _node(crowded, f"node-{index}", policy=WorkflowConcurrencyPolicy.PARALLEL_READ)
                    for index in range(300)
                ),
                edges=(),
            )
            for run in others:
                unit.workflows.create(run, nodes=(_node(run, "one"),), edges=())
            unit.commit()
        event.listen(factory._engine, "after_cursor_execute", capture)
        with factory() as unit:
            claims = unit.workflows.claim_ready(
                worker_id="worker",
                lease_until=datetime.now(UTC) + timedelta(seconds=30),
                limit=4,
            )
            unit.commit()
        assert {claim.run_id for claim in claims} == {crowded.id, *(run.id for run in others)}
        assert queries and all("LIMIT" in query.upper() for query in queries)
    finally:
        factory._engine.dispose()


def test_conflicted_candidate_pages_do_not_starve_later_free_run(tmp_path):
    factory = _factory(tmp_path / "blocked.db")
    holder, free = _run("holder"), _run("free")
    try:
        with factory() as unit:
            unit.workflows.create(
                holder, nodes=(_node(holder, "held", resource_keys=("shared",)),), edges=()
            )
            unit.workflows.claim_ready(
                worker_id="worker",
                lease_until=datetime.now(UTC) + timedelta(seconds=30),
                limit=1,
            )
            for index in range(260):
                run = _run(f"blocked-{index}")
                unit.workflows.create(
                    run, nodes=(_node(run, "blocked", resource_keys=("shared",)),), edges=()
                )
            unit.workflows.create(free, nodes=(_node(free, "free"),), edges=())
            unit.commit()
        with factory() as unit:
            claims = unit.workflows.claim_ready(
                worker_id="worker",
                lease_until=datetime.now(UTC) + timedelta(seconds=30),
                limit=3,
            )
            unit.commit()
        assert [claim.run_id for claim in claims] == [free.id]
    finally:
        factory._engine.dispose()
