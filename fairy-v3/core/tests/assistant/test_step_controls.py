from functools import partial
from uuid import UUID

import pytest

from fairy_core.providers import ProviderRegistry
from fairy_core.transports.stdio import build_local_service
from tests.assistant import test_workflow_controls as contracts
from tests.assistant.support import wait_for_turn
from tests.assistant.test_application import _scratch_task, _turn
from tests.assistant.test_step_tools import _provider, _TwoReads


@pytest.fixture
def step_engine(monkeypatch):
    monkeypatch.setattr(
        contracts, "build_local_service",
        partial(contracts.build_local_service, assistant_workflow_engine_version=4),
    )


def test_step_model_pause_and_resume(tmp_path, step_engine):
    contracts.test_active_turn_can_pause_and_resume_at_a_model_boundary(tmp_path)


@pytest.mark.parametrize("original", ["Draft a response", "Notify me when ready"])
def test_step_steering_keeps_the_original_turn_and_idempotent_revision(
    tmp_path, step_engine, original,
):
    contracts.test_steering_revises_one_turn_and_replays_idempotently(tmp_path, original)


@pytest.mark.parametrize("blocked_call", [1, 2])
def test_step_steering_reclassifies_before_further_execution(tmp_path, step_engine, blocked_call):
    contracts.test_steering_reclassifies_the_new_source_before_continuing(tmp_path, blocked_call)


def test_step_clarification_survives_core_reopen(tmp_path, step_engine):
    contracts.test_clarification_wait_survives_core_restart(tmp_path)


def test_steering_waits_for_running_reads_and_preserves_completed_facts(tmp_path):
    executor, provider = _TwoReads(), _provider()
    service = build_local_service(
        tmp_path, provider_registry=ProviderRegistry((provider,)), tool_executor=executor,
        assistant_workflow_engine_version=4,
    )
    try:
        task = _scratch_task(service, "Search two independent public sources")
        turn = _turn(service, task, "steer:running-reads")
        service.invoke("assistant.turns.start", {"turn_id": turn["id"]})
        assert executor.started.wait(3)
        instruction = "Summarize the retrieved sources; do not make changes."
        service.invoke("assistant.turns.steer", {
            "turn_id": turn["id"], "instruction": instruction,
            "expected_revision": 1, "idempotency_key": "steer:read-summary",
        })
        executor.release.set()
        assert wait_for_turn(service, turn["id"], timeout_seconds=6)["status"] == "completed"
        with service._unit_of_work_factory() as unit:
            workflow = unit.workflows.get(UUID(turn["workflow_run_id"]))
            invocations = unit.assistant.list_tool_invocations(UUID(turn["id"]))
        assert workflow.run.active_plan_revision == 2
        assert len(invocations) == 2
        assert all(invocation.status == "completed" for invocation in invocations)
        assert all(node.status == "succeeded" for node in workflow.nodes if (
            node.plan_revision == 1 and node.kind == "assistant.step.tool"
        ))
        assert any(node.status == "superseded" for node in workflow.nodes if (
            node.plan_revision == 1 and node.kind == "assistant.step.join"
        ))
        final_context = "\n".join(message.content for message in provider.requests[-1].messages)
        assert instruction in final_context
        assert "Result for first" in final_context
        assert "Result for second" in final_context
        assert len(executor.calls) == len(provider.requests) == 2
    finally:
        executor.release.set()
        service.close()
