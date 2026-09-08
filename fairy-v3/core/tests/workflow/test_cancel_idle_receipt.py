from threading import Event

import pytest

from fairy_core.workflow.scheduler import (
    WorkflowAdapterRegistry,
    WorkflowNodeResult,
    WorkflowScheduler,
)
from tests.workflow.test_repository import _factory, _node, _run


def test_cancel_receipt_waits_for_actual_adapter_return_and_is_run_scoped(tmp_path):
    factory = _factory(tmp_path / "idle.db")
    runs = [_run("first"), _run("second")]
    nodes = [_node(run, str(index)) for index, run in enumerate(runs)]
    entered = [Event(), Event()]
    release = [Event(), Event()]

    class Adapter:
        def execute(self, node, cancellation):
            index = int(node.node_key)
            entered[index].set()
            assert release[index].wait(5)
            cancellation.raise_if_cancelled()
            return WorkflowNodeResult(output={"finished": True})

    scheduler = WorkflowScheduler(
        unit_of_work_factory=factory,
        adapters=WorkflowAdapterRegistry({"test.echo": Adapter()}),
    )
    try:
        with factory() as unit:
            for run, node in zip(runs, nodes, strict=True):
                unit.workflows.create(run, nodes=(node,), edges=())
            unit.commit()
        scheduler.wake()
        assert all(event.wait(2) for event in entered)
        with pytest.raises(ValueError, match="cancelled"):
            scheduler.cancelled_run_idle(runs[0].id)
        scheduler.cancel(runs[0].id)
        receipt = scheduler.cancelled_run_idle(runs[0].id)
        assert scheduler.cancelled_run_idle(runs[0].id) is receipt
        assert not receipt.done(), "A cancel request is not proof of physical adapter return"
        release[0].set()
        assert receipt.result(timeout=2) is None
        assert not release[1].is_set()
        assert scheduler.cancelled_run_idle(runs[0].id).done()
        with factory() as unit:
            assert unit.workflows.get_run(runs[1].id).status == "running"
    finally:
        for event in release:
            event.set()
        scheduler.close()
