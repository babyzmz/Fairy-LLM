from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import event

from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from fairy_core.workflow.models import WorkflowEdge, WorkflowNodeStatus
from tests.workflow.test_repository import _node, _run


def test_hundred_branch_dependency_promotion_has_bounded_database_roundtrips(tmp_path):
    engine = create_sqlite_core_engine(tmp_path / "dependencies.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    run = _run("fanout")
    root = _node(run, "root")
    children = tuple(_node(run, f"read-{index}") for index in range(100))
    join = _node(run, "join")
    edges = tuple(WorkflowEdge(run.id, 1, root.id, child.id) for child in children)
    edges += tuple(WorkflowEdge(run.id, 1, child.id, join.id) for child in children)
    with factory() as unit:
        unit.workflows.create(run, nodes=(root, *children, join), edges=edges)
        (claim,) = unit.workflows.claim_ready(
            worker_id="test",
            lease_until=datetime.now(UTC) + timedelta(seconds=30),
            limit=1,
        )
        unit.commit()
    statements = []

    def record(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "after_cursor_execute", record)
    try:
        with factory() as unit:
            snapshot = unit.workflows.complete(claim, result={}, evidence_refs=())
            unit.commit()
        by_id = {node.id: node for node in snapshot.nodes}
        assert all(by_id[child.id].status is WorkflowNodeStatus.READY for child in children)
        assert by_id[join.id].status is WorkflowNodeStatus.PENDING
        assert len(statements) <= 15, f"100 branches required {len(statements)} SELECTs"
        print(f"dependency_100_branches_selects={len(statements)}")
    finally:
        event.remove(engine, "after_cursor_execute", record)
        engine.dispose()
