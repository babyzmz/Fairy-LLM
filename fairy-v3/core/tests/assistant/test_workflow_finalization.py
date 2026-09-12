from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import pytest

from fairy_core.assistant.models import MessageRole
from fairy_core.assistant.workflow_plan import ASSISTANT_RESPONSE_FINALIZE_NODE_KIND
from fairy_core.providers import CancellationToken, ProviderRegistry
from fairy_core.transports.stdio import build_local_service
from fairy_core.workflow.errors import WorkflowFenceError, WorkflowRevisionError
from tests.assistant.support import ScriptedProvider
from tests.assistant.test_application import _scratch_task, _turn
from tests.workflow.test_continuations import _claim


def _prepare_final(service, key):
    task = _scratch_task(service, "Hello Fairy")
    turn = _turn(service, task, key)
    app = service._assistant_application
    app.prepare_turn(UUID(turn["id"]), CancellationToken())
    started, command = app._start_model_round(UUID(turn["id"]), 1)
    factory = service._unit_of_work_factory
    with factory() as unit:
        unit.workflows.resume(started.workflow_run_id)
        unit.commit()
    while True:
        (claim,) = _claim(factory, 1)
        with factory() as unit:
            snapshot = unit.workflows.get(claim.run_id)
            node = next(item for item in snapshot.nodes if item.id == claim.node_id)
            if node.kind == ASSISTANT_RESPONSE_FINALIZE_NODE_KIND:
                return started, command, claim
            unit.workflows.complete(claim, result={}, evidence_refs=())
            unit.commit()


def _finish(service, turn, command, claim):
    return service._assistant_application._complete_turn(
        turn_id=turn.id, run=command, content="Hello from Fairy", usage={"output_tokens": 3},
        workflow_claim=claim,
    )


def test_final_business_commit_is_replayable_without_a_second_message(tmp_path):
    provider = ScriptedProvider([])
    # This fixture walks the fixed legacy graph; v4 real-node finalization and
    # lost-receipt recovery are covered by test_step_workflow.
    service = build_local_service(
        tmp_path, provider_registry=ProviderRegistry((provider,)),
        assistant_workflow_engine_version=3,
    )
    service._workflow_scheduler.close()
    try:
        turn, command, claim = _prepare_final(service, "final-checkpoint")
        first = _finish(service, turn, command, claim)
        with service._unit_of_work_factory() as unit:
            original = unit.assistant.message_for_turn(turn.id, MessageRole.ASSISTANT)
            unit.workflows.abandon(claim)
            unit.commit()
        (resumed,) = _claim(service._unit_of_work_factory, 1)
        assert resumed.lease_fence > claim.lease_fence
        second = _finish(service, turn, command, resumed)
        assert first.status == second.status == "completed"
        with service._unit_of_work_factory() as unit:
            messages = unit.assistant.list_messages(
                conversation_id=turn.conversation_id, limit=100, cursor=None,
            ).items
            snapshot = unit.workflows.get(turn.workflow_run_id)
        replies = [item for item in messages if item.role is MessageRole.ASSISTANT]
        assert len(replies) == 1 and replies[0].id == original.id
        final = next(item for item in snapshot.nodes if item.id == claim.node_id)
        assert final.result["turn_id"] == str(turn.id)
        assert final.status == "running"
        assert provider.requests == []
        with pytest.raises(WorkflowRevisionError):
            service._assistant_application._complete_turn(
                turn_id=turn.id, run=command, content="A conflicting reply", usage={},
                workflow_claim=resumed,
            )
        with service._unit_of_work_factory() as unit:
            unit.workflows.abandon(resumed)
            unit.commit()
    finally:
        service.close()
    replacement_provider = ScriptedProvider([])
    reopened = build_local_service(
        tmp_path, provider_registry=ProviderRegistry((replacement_provider,)),
    )
    try:
        with reopened._unit_of_work_factory() as unit:
            reply = unit.assistant.message_for_turn(turn.id, MessageRole.ASSISTANT)
        assert reply.id == original.id
        assert reply.content == "Hello from Fairy"
        assert replacement_provider.requests == []
    finally:
        reopened.close()


@pytest.mark.parametrize("invalid", ["stale", "cancelled", "foreign-turn"])
def test_invalid_finalization_claim_cannot_commit_a_reply(tmp_path, invalid):
    service = build_local_service(
        tmp_path, provider_registry=ProviderRegistry((ScriptedProvider([]),)),
        assistant_workflow_engine_version=3,
    )
    service._workflow_scheduler.close()
    try:
        turn, command, claim = _prepare_final(service, "stale-final")
        if invalid == "stale":
            invalid_claim = replace(claim, lease_fence=claim.lease_fence + 1)
        elif invalid == "cancelled":
            with service._unit_of_work_factory() as unit:
                unit.workflows.cancel(claim.run_id)
                unit.commit()
            invalid_claim = claim
        else:
            other, _, invalid_claim = _prepare_final(service, "other-final")
        with pytest.raises(WorkflowFenceError):
            _finish(service, turn, command, invalid_claim)
        with service._unit_of_work_factory() as unit:
            assert unit.assistant.message_for_turn(turn.id, MessageRole.ASSISTANT) is None
            assert unit.assistant.get_turn(turn.id).status == "running"
            if invalid == "foreign-turn":
                assert unit.assistant.message_for_turn(other.id, MessageRole.ASSISTANT) is None
    finally:
        service.close()
