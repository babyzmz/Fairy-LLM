from __future__ import annotations

from pathlib import Path
from typing import Any

from fairy_core.assistant.tools import ToolResult
from fairy_core.providers import ModelDelta, ProviderRegistry
from fairy_core.transports.stdio import build_local_service
from tests.assistant.support import RecordingToolExecutor, ScriptedProvider
from tests.assistant.test_application import _scratch_task, _turn


def test_tool_candidates_receive_core_scope_and_repeated_arguments_are_rejected(
    tmp_path: Path,
) -> None:
    forged = (
        '{"query":"Fairy architecture",'
        '"public_intent":"Check current sources before answering",'
        '"task_id":"forged",'
        '"project_root":"C:/forged","scope_digest":"' + "f" * 64 + '"}'
    )
    provider = ScriptedProvider(
        [
            (
                ModelDelta.tool_call(
                    profile_id="scripted",
                    sequence=1,
                    tool_call_id="call-1",
                    tool_name="web.search",
                    arguments_fragment=forged,
                ),
                ModelDelta.tool_call(
                    profile_id="scripted",
                    sequence=2,
                    tool_call_id="call-2",
                    tool_name="web.search",
                    arguments_fragment=forged,
                ),
                ModelDelta.done(
                    profile_id="scripted",
                    sequence=3,
                    finish_reason="tool_calls",
                ),
            ),
            (
                ModelDelta.text(
                    profile_id="scripted",
                    sequence=1,
                    text="Research complete",
                ),
                ModelDelta.done(
                    profile_id="scripted",
                    sequence=2,
                    finish_reason="stop",
                ),
            ),
        ]
    )
    executor = RecordingToolExecutor(summary="bounded evidence")
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((provider,)),
        tool_executor=executor,
    )
    try:
        task = _scratch_task(service, "Research Fairy architecture")
        turn = _turn(service, task, "turn:tools")

        completed = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})
        trace = service.invoke(
            "assistant.turns.trace.list",
            {"turn_id": turn["id"]},
        )
        with service._unit_of_work_factory() as unit_of_work:  # type: ignore[attr-defined]
            invocations = unit_of_work.assistant.list_tool_invocations(completed["id"])

        assert completed["status"] == "completed"
        assert len(executor.calls) == 1
        definition, scope, arguments = executor.calls[0]
        assert definition.name == "web.search"
        assert str(scope.task_id) == task["id"]
        assert arguments == {"query": "Fairy architecture"}
        assert len(invocations) == 1
        assert invocations[0].status.value == "completed"
        assert invocations[0].scope_digest == completed["scope_digest"]
        assert len(provider.requests) == 2
        assistant_protocol = [
            message
            for message in provider.requests[1].messages
            if message.role.value == "assistant" and message.tool_calls
        ]
        assert len(assistant_protocol) == 1
        assert [call.id for call in assistant_protocol[0].tool_calls] == [
            "call-1",
            "call-2",
        ]
        tool_messages = [
            message for message in provider.requests[1].messages if message.role.value == "tool"
        ]
        assert len(tool_messages) == 2
        assert "bounded evidence" in tool_messages[0].content
        assert "duplicate" in tool_messages[1].content.lower()
        assert [step["kind"] for step in trace["steps"]] == [
            "model",
            "reasoning",
            "tool",
            "observation",
            "model",
            "response",
        ]
        first_model, reasoning, tool, observation, final_model, response = trace["steps"]
        assert first_model["provider_attempt_id"] is not None
        assert final_model["provider_attempt_id"] is not None
        assert reasoning["public_summary"] == "Check current sources before answering"
        assert tool["public_summary"] == "Search public web or news sources."
        assert observation["public_summary"] == "bounded evidence"
        assert tool["parent_step_id"] == reasoning["id"]
        assert observation["parent_step_id"] == tool["id"]
        assert response["caused_by_step_id"] == final_model["id"]
        assert all(step["status"] == "succeeded" for step in trace["steps"])
    finally:
        service.close()


def test_malformed_tool_request_retries_once_before_dispatch(tmp_path: Path) -> None:
    provider = ScriptedProvider(
        [
            (
                ModelDelta.tool_call(
                    profile_id="scripted",
                    sequence=1,
                    tool_call_id="call-truncated",
                    tool_name="web.search",
                    arguments_fragment='{"query":"truncated"',
                ),
                ModelDelta.done(
                    profile_id="scripted",
                    sequence=2,
                    finish_reason="tool_calls",
                ),
            ),
            (
                ModelDelta.tool_call(
                    profile_id="scripted",
                    sequence=1,
                    tool_call_id="call-retry",
                    tool_name="web.search",
                    arguments_fragment=(
                        '{"query":"Fairy","public_intent":"Retry the bounded search"}'
                    ),
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
                    text="Recovered after the malformed tool request.",
                ),
                ModelDelta.done(
                    profile_id="scripted",
                    sequence=2,
                    finish_reason="stop",
                ),
            ),
        ]
    )
    executor = RecordingToolExecutor(summary="bounded evidence")
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((provider,)),
        tool_executor=executor,
    )
    try:
        task = _scratch_task(service, "Recover one malformed tool request")
        turn = _turn(service, task, "turn:malformed-tool-retry")

        completed = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})
        trace = service.invoke("assistant.turns.trace.list", {"turn_id": turn["id"]})

        assert completed["status"] == "completed"
        assert len(provider.requests) == 3
        assert len(executor.calls) == 1
        assert executor.calls[0][2] == {"query": "Fairy"}
        failed_model_steps = [
            step
            for step in trace["steps"]
            if step["kind"] == "model" and step["status"] == "failed"
        ]
        assert [step["public_summary"] for step in failed_model_steps] == [
            "Tool request could not be validated"
        ]
        assert any(
            message.role.value == "system" and "malformed or truncated" in message.content
            for message in provider.requests[1].messages
        )
    finally:
        service.close()


def test_direct_answer_is_always_available_and_never_dispatches_a_command(
    tmp_path: Path,
) -> None:
    provider = ScriptedProvider(
        [
            (
                ModelDelta.tool_call(
                    profile_id="scripted",
                    sequence=1,
                    tool_call_id="answer-1",
                    tool_name="direct_answer",
                    arguments_fragment='{"answer":"A direct response"}',
                ),
                ModelDelta.done(
                    profile_id="scripted",
                    sequence=2,
                    finish_reason="tool_calls",
                ),
            )
        ]
    )
    executor = RecordingToolExecutor()
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((provider,)),
        tool_executor=executor,
    )
    try:
        task = _scratch_task(service, "Answer directly")
        turn = _turn(service, task, "turn:direct-answer")

        completed = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})
        messages = service.invoke(
            "messages.list",
            {"conversation_id": task["conversation_id"]},
        )["items"]

        assert completed["status"] == "completed"
        assert messages[-1]["content"] == "A direct response"
        assert executor.calls == []
    finally:
        service.close()


def test_later_turn_does_not_replay_unpaired_historical_tool_messages(
    tmp_path: Path,
) -> None:
    provider = ScriptedProvider(
        [
            (
                ModelDelta.tool_call(
                    profile_id="scripted",
                    sequence=1,
                    tool_call_id="call-history",
                    tool_name="web.search",
                    arguments_fragment='{"query":"Fairy history"}',
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
                    text="First answer",
                ),
                ModelDelta.done(
                    profile_id="scripted",
                    sequence=2,
                    finish_reason="stop",
                ),
            ),
            (
                ModelDelta.text(
                    profile_id="scripted",
                    sequence=1,
                    text="Second answer",
                ),
                ModelDelta.done(
                    profile_id="scripted",
                    sequence=2,
                    finish_reason="stop",
                ),
            ),
        ]
    )
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((provider,)),
        tool_executor=RecordingToolExecutor(),
    )
    try:
        first_task = _scratch_task(service, "Research history")
        first_turn = _turn(service, first_task, "turn:history:first")
        service.invoke("assistant.turns.run", {"turn_id": first_turn["id"]})
        second_task = service.invoke(
            "tasks.create",
            {
                "conversation_id": first_task["conversation_id"],
                "user_request": "Continue without replaying protocol messages",
                "operation_mode": "answer",
                "execution_target": "local",
                "idempotency_key": "task:history:second",
            },
        )["task"]
        second_turn = _turn(service, second_task, "turn:history:second")

        completed = service.invoke(
            "assistant.turns.run",
            {"turn_id": second_turn["id"]},
        )

        assert completed["status"] == "completed"
        assert all(message.role.value != "tool" for message in provider.requests[2].messages)
    finally:
        service.close()


class _CancelDuringToolExecution:
    def __init__(self) -> None:
        self.service: Any | None = None
        self.turn_id: str | None = None

    def execute(self, definition, scope, arguments) -> ToolResult:
        del definition, scope, arguments
        assert self.service is not None
        assert self.turn_id is not None
        self.service.invoke(
            "assistant.turns.cancel",
            {"turn_id": self.turn_id, "expected_cancellation_revision": 0},
        )
        return ToolResult.create(
            public_summary="must not persist",
            model_content="must not persist",
            artifact_ids=(),
        )


def test_cancellation_during_tool_execution_cancels_command_without_result(
    tmp_path: Path,
) -> None:
    provider = ScriptedProvider(
        [
            (
                ModelDelta.tool_call(
                    profile_id="scripted",
                    sequence=1,
                    tool_call_id="call-cancel",
                    tool_name="web.search",
                    arguments_fragment='{"query":"cancel"}',
                ),
                ModelDelta.done(
                    profile_id="scripted",
                    sequence=2,
                    finish_reason="tool_calls",
                ),
            )
        ]
    )
    executor = _CancelDuringToolExecution()
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((provider,)),
        tool_executor=executor,
    )
    try:
        task = _scratch_task(service, "Cancel during tool")
        turn = _turn(service, task, "turn:tool-cancel")
        executor.service = service
        executor.turn_id = str(turn["id"])

        cancelled = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})

        with service._unit_of_work_factory() as unit_of_work:  # type: ignore[attr-defined]
            invocations = unit_of_work.assistant.list_tool_invocations(turn["id"])
            command = unit_of_work.commands.get_run(invocations[0].command_run_id)
        messages = service.invoke(
            "messages.list",
            {"conversation_id": task["conversation_id"]},
        )["items"]
        events = service.invoke("events.subscribe", {"cursor": 0})["items"]
        assert cancelled["status"] == "cancelled"
        assert invocations[0].status.value == "cancelled"
        assert command is not None and command.status.value == "cancelled"
        assert [message["role"] for message in messages] == ["user"]
        assert [
            event["event_type"]
            for event in events
            if event["payload"].get("turn_id") == turn["id"]
            and event["event_type"] == "assistant.turn.cancelled"
        ] == ["assistant.turn.cancelled"]
    finally:
        service.close()


def test_oversized_tool_arguments_fail_before_dispatch(tmp_path: Path) -> None:
    provider = ScriptedProvider(
        [
            (
                ModelDelta.tool_call(
                    profile_id="scripted",
                    sequence=1,
                    tool_call_id="call-large",
                    tool_name="web.search",
                    arguments_fragment='{"query":"' + "x" * 65_000 + '"}',
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
                    text="must not run",
                ),
                ModelDelta.done(
                    profile_id="scripted",
                    sequence=2,
                    finish_reason="stop",
                ),
            ),
        ]
    )
    executor = RecordingToolExecutor()
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((provider,)),
        tool_executor=executor,
    )
    try:
        task = _scratch_task(service, "Oversized candidate")
        turn = _turn(service, task, "turn:oversized-tool")

        failed = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})
        trace = service.invoke("assistant.turns.trace.list", {"turn_id": turn["id"]})

        assert failed["status"] == "failed"
        assert failed["error_code"] == "PROVIDER_PROTOCOL_ERROR"
        assert executor.calls == []
        failed_steps = [step for step in trace["steps"] if step["status"] == "failed"]
        assert failed_steps[-1]["public_detail"] == (
            "The selected model returned an unusable response. No pending tool action was applied."
        )
        assert "PROVIDER_PROTOCOL_ERROR" not in failed_steps[-1]["public_detail"]
    finally:
        service.close()


def test_cancellation_after_last_tool_delta_prevents_command_dispatch(
    tmp_path: Path,
) -> None:
    provider = ScriptedProvider(
        [
            (
                ModelDelta.tool_call(
                    profile_id="scripted",
                    sequence=1,
                    tool_call_id="call-cancelled-before-dispatch",
                    tool_name="web.search",
                    arguments_fragment='{"query":"cancelled"}',
                ),
            )
        ],
        cancel_after_first_delta=True,
    )
    executor = RecordingToolExecutor()
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((provider,)),
        tool_executor=executor,
    )
    try:
        task = _scratch_task(service, "Cancel before dispatch")
        turn = _turn(service, task, "turn:cancel-before-dispatch")

        cancelled = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})

        with service._unit_of_work_factory() as unit_of_work:  # type: ignore[attr-defined]
            invocations = unit_of_work.assistant.list_tool_invocations(turn["id"])
        assert cancelled["status"] == "cancelled"
        assert executor.calls == []
        assert invocations == ()
    finally:
        service.close()
