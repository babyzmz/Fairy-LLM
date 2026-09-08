from datetime import UTC, datetime, timedelta
from functools import partial
from threading import Event, get_ident
from uuid import UUID

import pytest

from fairy_core.providers import ProviderRegistry
from fairy_core.transports.stdio import build_local_service
from fairy_core.workflow.errors import WorkflowFenceError
from tests.assistant.support import wait_for_turn
from tests.assistant.test_application import _scratch_task, _turn
from tests.assistant.test_step_media import _create_media_turn
from tests.assistant.test_step_workflow import _provider
from tests.media import test_media_service as media_contracts


@pytest.mark.parametrize("engine_version", [3, 4])
@pytest.mark.parametrize("takeover", [False, True])
@pytest.mark.parametrize("restart", [False, True])
def test_heartbeat_renews_only_commands_claimed_by_this_assistant(
    tmp_path, engine_version, takeover, restart,
):
    provider = _provider()
    service = build_local_service(
        tmp_path, provider_registry=ProviderRegistry((provider,)),
        assistant_workflow_engine_version=engine_version,
    )
    service._workflow_scheduler.close()
    try:
        task = _scratch_task(service, "Renew only this owned model command")
        turn = _turn(service, task, "owned-heartbeat")
        other_task = _scratch_task(service, "Do not renew this other conversation")
        other = _turn(service, other_task, "other-heartbeat")
        _, command = service._assistant_application._start_model_round(UUID(turn["id"]), 1)
        _, other_command = service._assistant_application._start_model_round(UUID(other["id"]), 1)
        with service._unit_of_work_factory() as unit:
            if takeover:
                assert unit.commands.abandon(
                    command.id, lease_owner=command.lease_owner, lease_fence=command.lease_fence,
                )
                unit.commands.claim(
                    command.id, worker_id="another-process",
                    lease_until=datetime.now(UTC) + timedelta(minutes=1),
                )
            expected = unit.commands.get_run(command.id)
            node = unit.workflows.get(UUID(turn["workflow_run_id"])).nodes[0]
            unit.commit()
        if restart:
            service.close()
            service = build_local_service(
                tmp_path, provider_registry=ProviderRegistry((provider,)),
                assistant_workflow_engine_version=engine_version,
            )
            service._workflow_scheduler.close()
        kind = "assistant.step.model" if engine_version == 4 else "assistant.model.round"
        adapter = service._workflow_scheduler._adapters.require(kind)
        assert adapter.heartbeat(node) is (not takeover and not restart)
        with service._unit_of_work_factory() as unit:
            renewed = unit.commands.get_run(command.id)
            assert unit.commands.get_run(other_command.id) == other_command
        if takeover or restart:
            assert renewed == expected
            if engine_version == 4:
                with service._unit_of_work_factory() as unit:
                    current_turn = unit.assistant.get_turn(UUID(turn["id"]))
                with pytest.raises(WorkflowFenceError):
                    adapter._command(current_turn, {"model_command_id": str(command.id)})
        else:
            assert renewed.lease_until > expected.lease_until
            assert renewed.lease_owner == expected.lease_owner
            assert renewed.lease_fence == expected.lease_fence
        assert not provider.requests
    finally:
        service.close()


def test_parent_heartbeat_does_not_renew_the_media_domain_lease(tmp_path, monkeypatch):
    entered, release = Event(), Event()
    original_generate = media_contracts.RecordingMediaProvider.generate_image

    def blocked(provider, request, cancellation):
        entered.set()
        assert release.wait(10)
        return original_generate(provider, request, cancellation)

    monkeypatch.setattr(media_contracts, "build_local_service", partial(
        media_contracts.build_local_service, assistant_workflow_engine_version=4,
    ))
    monkeypatch.setattr(media_contracts.RecordingMediaProvider, "generate_image", blocked)
    service, _, media, turn, task = _create_media_turn(tmp_path)
    try:
        service.invoke("assistant.turns.start", {"turn_id": turn["id"]})
        assert entered.wait(4)
        with service._unit_of_work_factory() as unit:
            invocation = unit.assistant.list_tool_invocations(UUID(turn["id"]))[0]
            command = unit.commands.get_run(invocation.command_run_id)
            nodes = unit.workflows.get(UUID(turn["workflow_run_id"])).nodes
            ledger_type = type(unit.commands)
        assert command.lease_owner != service._assistant_application._command_worker_id
        node = next(node for node in nodes if node.kind == "assistant.step.tool")
        caller, renewals = get_ident(), []
        original_renew = ledger_type.renew

        def observe(ledger, run_id, **kwargs):
            if get_ident() == caller:
                renewals.append(run_id)
            return original_renew(ledger, run_id, **kwargs)

        monkeypatch.setattr(ledger_type, "renew", observe)
        adapter = service._workflow_scheduler._adapters.require(node.kind)
        assert adapter.heartbeat(node)
        assert command.id not in renewals
        release.set()
        assert wait_for_turn(service, turn["id"], timeout_seconds=8)["status"] == "completed"
        assert len(media.image_requests) == 1
        with service._unit_of_work_factory() as unit:
            assert len(unit.state.list_media_jobs(UUID(task["id"]))) == 1
    finally:
        release.set()
        service.close()
