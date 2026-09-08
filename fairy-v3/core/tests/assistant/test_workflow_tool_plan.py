from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest

from fairy_core.assistant import workflow_plan
from fairy_core.assistant.models import ToolInvocation
from fairy_core.commanding.registry import build_default_registry
from fairy_core.persistence.sqlite import create_sqlite_core_engine
from fairy_core.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from fairy_core.workflow.models import WorkflowNodeStatus
from tests.assistant.test_models import _turn
from tests.workflow.test_continuations import _claim
from tests.workflow.test_repository import _node, _run


def _fixture():
    turn = _turn()
    turn.active_interpretation_revision = 1
    run = _run(f"tools:{turn.id}")
    turn.bind_workflow(run.id, engine_version=4)
    source = replace(_node(run, "model"), payload={"turn_id": str(turn.id)})
    registry = build_default_registry()
    names = ("web.search", "web.fetch", "system.notify", "web.search", "web.fetch")
    invocations = tuple(
        ToolInvocation.create(
            turn=turn, model_round=1, sequence=index + 1, provider_call_id=f"call-{index}",
            tool_name=name, scope_digest=turn.scope_digest, arguments={"fixture": index},
        )
        for index, name in enumerate(names)
    )
    definitions = {name: registry.get(name) for name in names}
    assert all(definition is not None for definition in definitions.values())
    return turn, run, source, invocations, definitions


def _compile(source, turn, invocations, definitions):
    compiler = getattr(workflow_plan, "assistant_tool_continuation", None)
    assert callable(compiler), "Assistant needs a real, scoped tool-node continuation compiler"
    return compiler(
        source=source, turn=turn, invocations=invocations, offered_definitions=definitions,
        resource_keys_by_invocation={},
    )


def test_tool_graph_preserves_serial_barriers_and_join_order_after_reopen(tmp_path):
    turn, run, source, invocations, definitions = _fixture()
    nodes, edges = _compile(source, turn, invocations, definitions)
    again, _ = _compile(source, turn, invocations, definitions)
    assert [node.id for node in again] == [node.id for node in nodes]
    assert len(nodes) == 6
    assert [node.payload["invocation_id"] for node in nodes[:-1]] == [
        str(item.id) for item in invocations
    ]
    assert nodes[-1].payload["invocation_ids"] == [str(item.id) for item in invocations]
    assert all("arguments" not in node.payload for node in nodes)
    assert all(node.status is WorkflowNodeStatus.PENDING for node in nodes)
    path = tmp_path / "tool-graph.db"
    engine = create_sqlite_core_engine(path)
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="left")
    try:
        with factory() as unit:
            unit.workflows.create(run, nodes=(source,), edges=())
            unit.commit()
        (claim,) = _claim(factory)
        with factory() as unit:
            unit.workflows.complete(
                claim, result={}, evidence_refs=(), next_nodes=nodes, next_edges=edges,
            )
            unit.commit()
        claims = _claim(factory)
        assert {claim.node_id for claim in claims} == {nodes[0].id, nodes[1].id}
        for claim in reversed(claims):
            with factory() as unit:
                unit.workflows.complete(claim, result={}, evidence_refs=())
                unit.commit()
        (write,) = _claim(factory)
        assert write.node_id == nodes[2].id
        assert _claim(factory) == ()
        with factory() as unit:
            unit.workflows.complete(write, result={}, evidence_refs=())
            unit.commit()
    finally:
        engine.dispose()
    engine = create_sqlite_core_engine(path)
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="left")
    try:
        with SqlAlchemyUnitOfWorkFactory(engine, tenant_id="right")() as unit:
            assert unit.workflows.get(run.id) is None
        claims = _claim(factory)
        assert {claim.node_id for claim in claims} == {nodes[3].id, nodes[4].id}
        with factory() as unit:
            unit.workflows.complete(claims[1], result={}, evidence_refs=())
            unit.commit()
        assert _claim(factory) == ()
        with factory() as unit:
            unit.workflows.complete(claims[0], result={}, evidence_refs=())
            unit.commit()
        (join,) = _claim(factory)
        assert join.node_id == nodes[-1].id
    finally:
        engine.dispose()


@pytest.mark.parametrize("invalid", ["turn", "scope", "round", "order", "duplicate", "definition"])
def test_tool_graph_rejects_cross_turn_or_inconsistent_model_batch(invalid):
    turn, _, source, invocations, definitions = _fixture()
    if invalid == "turn":
        invocations = (replace(invocations[0], turn_id=uuid4()), *invocations[1:])
    elif invalid == "scope":
        invocations = (replace(invocations[0], scope_digest="e" * 64), *invocations[1:])
    elif invalid == "round":
        invocations = (replace(invocations[0], model_round=2), *invocations[1:])
    elif invalid == "order":
        invocations = tuple(reversed(invocations))
    elif invalid == "duplicate":
        invocations = (invocations[0], invocations[0])
    else:
        definitions = {"web.search": definitions["web.fetch"]}
    with pytest.raises(ValueError):
        _compile(source, turn, invocations, definitions)


def test_distinct_turns_do_not_share_node_ids_or_join_targets():
    left = _fixture()
    right = _fixture()
    a, _ = _compile(left[2], left[0], left[3], left[4])
    b, _ = _compile(right[2], right[0], right[3], right[4])
    assert {node.id for node in a}.isdisjoint(node.id for node in b)
    assert set(a[-1].payload["invocation_ids"]).isdisjoint(b[-1].payload["invocation_ids"])


def test_compiled_domain_resource_blocks_another_run_until_release(tmp_path):
    engine = create_sqlite_core_engine(tmp_path / "resources.db")
    factory = SqlAlchemyUnitOfWorkFactory(engine, tenant_id="local")
    graphs = []
    try:
        for fixture in (_fixture(), _fixture()):
            turn, run, source, invocations, definitions = fixture
            nodes, edges = workflow_plan.assistant_tool_continuation(
                source=source, turn=turn, invocations=invocations[:1],
                offered_definitions=definitions,
                resource_keys_by_invocation={invocations[0].id: ("external-account:shared",)},
            )
            graphs.append(nodes)
            with factory() as unit:
                unit.workflows.create(run, nodes=(source,), edges=())
                unit.commit()
            claims = _claim(factory)
            source_claim = next(claim for claim in claims if claim.node_id == source.id)
            # The second root may be claimed alongside the first graph's read.
            for claim in claims:
                if claim.node_id != source.id:
                    with factory() as unit:
                        unit.workflows.abandon(claim)
                        unit.commit()
            with factory() as unit:
                unit.workflows.complete(
                    source_claim, result={}, evidence_refs=(), next_nodes=nodes, next_edges=edges,
                )
                unit.commit()
        (first,) = _claim(factory)
        assert first.node_id in {graph[0].id for graph in graphs}
        assert _claim(factory) == ()
        with factory() as unit:
            unit.workflows.complete(first, result={}, evidence_refs=())
            unit.commit()
        next_claims = _claim(factory)
        assert any(claim.node_id in {graph[0].id for graph in graphs}
                   and claim.node_id != first.node_id for claim in next_claims)
    finally:
        engine.dispose()
