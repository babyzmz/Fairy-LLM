from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from fairy_core.providers import ProviderRegistry
from fairy_core.transports.stdio import build_local_service
from tests.assistant.test_application import _scratch_task, _turn
from tests.assistant.test_step_workflow import _provider


@pytest.mark.parametrize("takeover", [False, True])
def test_workflow_failure_claims_only_expired_model_commands(tmp_path, takeover):
    provider = _provider()
    service = build_local_service(
        tmp_path, provider_registry=ProviderRegistry((provider,)),
        assistant_workflow_engine_version=4,
    )
    service._workflow_scheduler.close()
    try:
        task = _scratch_task(service, "Fail this model node safely")
        turn = _turn(service, task, "failure-authority")
        other_task = _scratch_task(service, "Leave this model alone")
        other = _turn(service, other_task, "other-authority")
        with service._unit_of_work_factory() as unit:
            unit.workflows.resume(UUID(turn["workflow_run_id"]))
            (claim,) = unit.workflows.claim_ready(
                worker_id="node-owner", lease_until=datetime.now(UTC) + timedelta(minutes=1),
                limit=1,
            )
            node = unit.workflows.get_node(claim.run_id, claim.node_id)
            unit.commit()
        _, command = service._assistant_application._start_model_round(UUID(turn["id"]), 1)
        _, other_command = service._assistant_application._start_model_round(UUID(other["id"]), 1)
        with service._unit_of_work_factory() as unit:
            assert unit.commands.abandon(
                command.id, lease_owner=command.lease_owner, lease_fence=command.lease_fence,
            )
            if takeover:
                unit.commands.claim(
                    command.id, worker_id="another-live-worker",
                    lease_until=datetime.now(UTC) + timedelta(minutes=1),
                )
            expected = unit.commands.get_run(command.id)
            unit.commit()
        adapter = service._workflow_scheduler._adapters.require("assistant.step.model")
        with service._unit_of_work_factory() as unit:
            unit.workflows.fail(claim, error_code="WORKFLOW_NODE_FAILED")
            adapter.settle_failure_in_unit(unit, node, claim, "WORKFLOW_NODE_FAILED")
            unit.commit()
        with service._unit_of_work_factory() as unit:
            failed = unit.assistant.get_turn(UUID(turn["id"]))
            run = unit.workflows.get_run(claim.run_id)
            settled = unit.commands.get_run(command.id)
            assert unit.commands.get_run(other_command.id) == other_command
            assert unit.assistant.get_turn(UUID(other["id"])).status == "running"
        assert failed.status == run.status == "failed"
        assert failed.error_code == run.error_code == "WORKFLOW_NODE_FAILED"
        if takeover:
            assert settled == expected
        else:
            assert settled.status == "failed"
            assert settled.lease_fence > expected.lease_fence
        assert not provider.requests
    finally:
        service.close()
