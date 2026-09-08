from __future__ import annotations

from uuid import UUID

import pytest

from fairy_core.assistant.models import MessageRole
from fairy_core.providers import ModelDelta, ProviderRegistry
from fairy_core.transports.stdio import build_local_service
from fairy_core.workflow.repository import SqlAlchemyWorkflowRepository
from fairy_core.workflow.scheduler import WorkflowRetryableError
from tests.assistant.support import ScriptedProvider, wait_for_turn
from tests.assistant.test_application import _scratch_task, _turn


def _provider():
    return ScriptedProvider(
        [
            (
                ModelDelta.text(profile_id="scripted", sequence=1, text="Hello from Fairy"),
                ModelDelta.done(profile_id="scripted", sequence=2, finish_reason="stop"),
            )
        ]
    )


@pytest.mark.parametrize(
    "lost_completion",
    [None, "assistant.step.model", "assistant.step.finalize"],
)
def test_step_engine_commits_real_model_and_final_nodes_without_repeating_work(
    tmp_path,
    monkeypatch,
    lost_completion,
):
    provider = _provider()
    original_complete = SqlAlchemyWorkflowRepository.complete
    injected = []

    def complete(repository, claim, **kwargs):
        snapshot = repository.get(claim.run_id)
        node = next(item for item in snapshot.nodes if item.id == claim.node_id)
        if node.kind == lost_completion and not injected:
            assert node.result is not None
            injected.append(claim.node_id)
            raise WorkflowRetryableError("TRANSIENT_COMPLETION_FAILURE")
        return original_complete(repository, claim, **kwargs)

    monkeypatch.setattr(SqlAlchemyWorkflowRepository, "complete", complete)
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((provider,)),
        assistant_workflow_engine_version=4,
    )
    try:
        task = _scratch_task(service, "Hello Fairy")
        turn = _turn(service, task, "step-engine")
        service.invoke("assistant.turns.start", {"turn_id": turn["id"]})
        completed = wait_for_turn(service, turn["id"])
        finished = service._workflow_scheduler.wait(UUID(turn["workflow_run_id"]), timeout=5)
        assert finished.run.status == "completed"
        assert completed["execution_engine_version"] == 4
        assert len(provider.requests) == 1
        with service._unit_of_work_factory() as unit:
            snapshot = unit.workflows.get(UUID(turn["workflow_run_id"]))
            messages = unit.assistant.list_messages(
                conversation_id=UUID(task["conversation_id"]),
                limit=100,
                cursor=None,
            ).items
        replies = [message for message in messages if message.role is MessageRole.ASSISTANT]
        assert len(replies) == 1 and replies[0].content == "Hello from Fairy"
        assert {node.kind for node in snapshot.nodes} == {
            "assistant.request.interpret",
            "assistant.step.route",
            "assistant.step.model",
            "assistant.step.verify",
            "assistant.step.finalize",
        }
        assert snapshot.run.model_rounds_used == 1
        if lost_completion:
            assert len(injected) == 1
            recovered = next(node for node in snapshot.nodes if node.id == injected[0])
            assert recovered.attempt_count == 2
    finally:
        service.close()


def test_opt_in_does_not_rebind_an_existing_legacy_turn_after_restart(tmp_path):
    old = build_local_service(tmp_path, provider_registry=ProviderRegistry((_provider(),)))
    try:
        task = _scratch_task(old, "Hello old engine")
        original = _turn(old, task, "legacy-remains-bound")
        assert original["execution_engine_version"] == 3
    finally:
        old.close()
    provider = ScriptedProvider([*_provider().rounds, *_provider().rounds])
    new = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((provider,)),
        assistant_workflow_engine_version=4,
    )
    try:
        new.invoke("assistant.turns.start", {"turn_id": original["id"]})
        recovered = wait_for_turn(new, original["id"])
        task = _scratch_task(new, "Hello new engine")
        fresh = _turn(new, task, "step-engine-fresh")
        new.invoke("assistant.turns.start", {"turn_id": fresh["id"]})
        wait_for_turn(new, fresh["id"])
        assert recovered["execution_engine_version"] == 3
        assert recovered["workflow_run_id"] == original["workflow_run_id"]
        assert fresh["execution_engine_version"] == 4
        assert fresh["workflow_run_id"] != original["workflow_run_id"]
        assert len(provider.requests) == 2
    finally:
        new.close()


@pytest.mark.parametrize("retry_reason", ["malformed_tool", "verification"])
def test_step_retries_run_in_a_new_model_node_and_keep_one_final_reply(tmp_path, retry_reason):
    first = (
        (
            ModelDelta.tool_call(
                profile_id="scripted",
                sequence=1,
                tool_call_id="bad",
                tool_name="web.search",
                arguments_fragment='{"query":',
            ),
            ModelDelta.done(profile_id="scripted", sequence=2, finish_reason="tool_calls"),
        )
        if retry_reason == "malformed_tool"
        else _provider().rounds[0]
    )
    provider = ScriptedProvider([first, *_provider().rounds])
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((provider,)),
        assistant_workflow_engine_version=4,
    )
    verification_calls = []

    def verify(_turn_id):
        verification_calls.append(_turn_id)
        return (
            "The response must include the requested result."
            if len(verification_calls) == 1
            else None
        )

    try:
        if retry_reason == "verification":
            service._assistant_application._execution_completion_hook = verify
        task = _scratch_task(service, "Hello Fairy")
        turn = _turn(service, task, f"retry:{retry_reason}")
        service.invoke("assistant.turns.start", {"turn_id": turn["id"]})
        wait_for_turn(service, turn["id"])
        snapshot = service._workflow_scheduler.wait(UUID(turn["workflow_run_id"]), timeout=5)
        assert snapshot.run.status == "completed"
        assert len(provider.requests) == 2
        assert sum(node.kind == "assistant.step.model" for node in snapshot.nodes) == 2
        assert snapshot.run.model_rounds_used == 2
        assert any(
            "previous tool request" in message.content or "requested result" in message.content
            for message in provider.requests[1].messages
        )
        with service._unit_of_work_factory() as unit:
            messages = unit.assistant.list_messages(
                conversation_id=UUID(task["conversation_id"]),
                limit=100,
                cursor=None,
            ).items
        assert sum(message.role is MessageRole.ASSISTANT for message in messages) == 1
    finally:
        service.close()
