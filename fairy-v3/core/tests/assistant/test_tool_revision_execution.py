from uuid import UUID

import pytest

from fairy_core.providers import ModelDelta, ProviderRegistry
from fairy_core.transports.stdio import build_local_service
from tests.assistant.support import RecordingToolExecutor, ScriptedProvider
from tests.assistant.test_application import _scratch_task, _turn


@pytest.mark.parametrize("reuse_provider_id", [False, True])
def test_same_revision_repeat_is_explained_without_reexecution_or_database_failure(
    tmp_path, reuse_provider_id,
):
    rounds = [
        (
            ModelDelta.tool_call(
                profile_id="scripted", sequence=1,
                tool_call_id="call" if reuse_provider_id else f"call-{index}",
                tool_name="web.search",
                arguments_fragment='{"query":"Fairy"}',
            ),
            ModelDelta.done(profile_id="scripted", sequence=2, finish_reason="tool_calls"),
        )
        for index in range(2)
    ]
    rounds.append((
        ModelDelta.text(profile_id="scripted", sequence=1, text="Here is the recorded result."),
        ModelDelta.done(profile_id="scripted", sequence=2, finish_reason="stop"),
    ))
    provider = ScriptedProvider(rounds)
    executor = RecordingToolExecutor(summary="bounded evidence")
    service = build_local_service(
        tmp_path, provider_registry=ProviderRegistry((provider,)),
        tool_executor=executor, assistant_workflow_engine_version=4,
    )
    try:
        task = _scratch_task(service, "Search public Fairy sources")
        turn = _turn(service, task, "same-revision-repeat")
        finished = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})
        assert finished["status"] == "completed", finished
        assert len(executor.calls) == 1
        assert len(provider.requests) == 3
        with service._unit_of_work_factory() as unit:
            invocations = unit.assistant.list_tool_invocations(UUID(turn["id"]))
            assert len(invocations) == 1
            assert invocations[0].status == "completed"
        context = "\n".join(item.content for item in provider.requests[-1].messages)
        assert "already attempted" in context
        assert "bounded evidence" in context
    finally:
        service.close()
