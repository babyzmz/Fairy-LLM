from pathlib import Path
from uuid import UUID

from fairy_core.providers import ModelDelta, ProviderRegistry
from fairy_core.transports.stdio import build_local_service
from tests.assistant.cancellation_support import BlockingToolStop
from tests.assistant.support import ScriptedProvider
from tests.assistant.test_application import _scratch_task, _turn


def test_composed_tool_chain_preserves_domain_cancellation(tmp_path: Path):
    executor = BlockingToolStop()
    executor.release_stop.set()
    provider = ScriptedProvider([(
        ModelDelta.tool_call(
            profile_id="scripted", sequence=1, tool_call_id="stop-through-chain",
            tool_name="web.search", arguments_fragment='{"query":"current sources"}',
        ),
        ModelDelta.done(profile_id="scripted", sequence=2, finish_reason="tool_calls"),
    )])
    service = build_local_service(
        tmp_path, provider_registry=ProviderRegistry((provider,)), tool_executor=executor,
    )
    try:
        turn = _turn(service, _scratch_task(service, "Read current sources"), "stop-chain")
        service.invoke("assistant.turns.start", {"turn_id": turn["id"]})
        assert executor.started.wait(3)
        with service._unit_of_work_factory() as unit:
            invocation = unit.assistant.list_tool_invocations(UUID(turn["id"]))[0]
            command = unit.commands.get_run(invocation.command_run_id)
        service._tool_executor.cancel_command(command)
        assert executor.stop_started.is_set()
    finally:
        executor.release_tool.set()
        service.close()
