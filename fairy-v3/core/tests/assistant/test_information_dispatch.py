from __future__ import annotations

from pathlib import Path

from fairy_core.providers import ModelDelta, ProviderRegistry
from fairy_core.transports.stdio import build_local_service
from tests.assistant.support import RecordingToolExecutor, ScriptedProvider
from tests.assistant.test_application import _scratch_task, _turn


def test_model_selected_information_tool_uses_registered_schema_and_command_bus(
    tmp_path: Path,
) -> None:
    provider = ScriptedProvider(
        [
            (
                ModelDelta.tool_call(
                    profile_id="scripted",
                    sequence=1,
                    tool_call_id="weather-1",
                    tool_name="info.weather",
                    arguments_fragment='{"location":"Sydney","units":"metric"}',
                ),
                ModelDelta.done(
                    profile_id="scripted",
                    sequence=2,
                    finish_reason="tool_calls",
                ),
            ),
            (
                ModelDelta.text(
                    profile_id="scripted",
                    sequence=1,
                    text="Sydney weather is ready",
                ),
                ModelDelta.done(
                    profile_id="scripted",
                    sequence=2,
                    finish_reason="stop",
                ),
            ),
        ]
    )
    executor = RecordingToolExecutor(summary="weather fixture")
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((provider,)),
        tool_executor=executor,
    )
    try:
        task = _scratch_task(service, "What is the weather in Sydney?")
        turn = _turn(service, task, "turn:information:weather")

        completed = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})

        assert completed["status"] == "completed"
        assert len(executor.calls) == 1
        definition, scope, arguments = executor.calls[0]
        assert definition.name == "info.weather"
        assert arguments == {"location": "Sydney", "units": "metric"}
        assert str(scope.task_id) == task["id"]
        assert any(tool.name == "info.weather" for tool in provider.requests[0].tools)
    finally:
        service.close()
