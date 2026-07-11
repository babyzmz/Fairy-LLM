from __future__ import annotations

from pathlib import Path

from fairy_core.providers import (
    ModelDelta,
    ProviderRegistry,
)
from fairy_core.transports.stdio import build_local_service
from tests.assistant.support import ScriptedProvider


def _scratch_task(service, request: str) -> dict[str, object]:
    conversation = service.invoke(
        "conversations.create",
        {"project_id": None, "workspace_type": "chat_scratch"},
    )
    return service.invoke(
        "tasks.create",
        {
            "conversation_id": conversation["id"],
            "user_request": request,
            "operation_mode": "answer",
            "execution_target": "local",
            "idempotency_key": f"task:{conversation['id']}",
        },
    )["task"]


def _turn(service, task: dict[str, object], key: str) -> dict[str, object]:
    return service.invoke(
        "assistant.turns.create",
        {
            "task_id": task["id"],
            "profile_id": "scripted",
            "idempotency_key": key,
        },
    )


def test_scratch_turn_persists_context_deltas_message_and_completion(
    tmp_path: Path,
) -> None:
    provider = ScriptedProvider(
        [
            (
                ModelDelta.text(profile_id="scripted", sequence=1, text="Hello "),
                ModelDelta.text(profile_id="scripted", sequence=2, text="from Fairy"),
                ModelDelta.usage_delta(
                    profile_id="scripted",
                    sequence=3,
                    usage={"input_tokens": 20, "output_tokens": 4},
                ),
                ModelDelta.done(
                    profile_id="scripted",
                    sequence=4,
                    finish_reason="stop",
                ),
            )
        ]
    )
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((provider,)),
    )
    try:
        task = _scratch_task(service, "Explain Fairy without tools")
        created = _turn(service, task, "turn:complete")

        completed = service.invoke(
            "assistant.turns.run",
            {"turn_id": created["id"]},
        )
        messages = service.invoke(
            "messages.list",
            {"conversation_id": task["conversation_id"]},
        )["items"]
        events = service.invoke("events.subscribe", {"cursor": 0})["items"]

        assert completed["status"] == "completed"
        assert completed["usage"] == {"input_tokens": 20, "output_tokens": 4}
        assert [(message["role"], message["content"]) for message in messages] == [
            ("user", "Explain Fairy without tools"),
            ("assistant", "Hello from Fairy"),
        ]
        assistant_events = [
            event
            for event in events
            if event["payload"].get("turn_id") == created["id"]
            and event["event_type"].startswith("assistant.")
        ]
        assert [event["event_type"] for event in assistant_events] == [
            "assistant.turn.started",
            "assistant.message.delta",
            "assistant.message.delta",
            "assistant.turn.completed",
        ]
        assert provider.requests[0].messages[-1].content == "Explain Fairy without tools"
        assert provider.requests[0].tools[0].name == "direct_answer"
        assert provider.requests[0].max_output_tokens <= 4_096
        assert task["memory_snapshot_id"] == created["memory_snapshot_id"]
    finally:
        service.close()


def test_natural_language_keywords_do_not_force_a_tool_route(tmp_path: Path) -> None:
    requests = (
        "What is the weather in Sydney?",
        "Show me today's news",
        "Remember that I prefer compact layouts",
    )
    provider = ScriptedProvider(
        [
            (
                ModelDelta.text(
                    profile_id="scripted",
                    sequence=1,
                    text=f"answer {index}",
                ),
                ModelDelta.done(
                    profile_id="scripted",
                    sequence=2,
                    finish_reason="stop",
                ),
            )
            for index in range(len(requests))
        ]
    )
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((provider,)),
    )
    try:
        for index, request in enumerate(requests):
            task = _scratch_task(service, request)
            turn = _turn(service, task, f"turn:keyword:{index}")
            result = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})
            assert result["status"] == "completed"

        assert len(provider.requests) == len(requests)
        assert all(request.tools[0].name == "direct_answer" for request in provider.requests)
    finally:
        service.close()


def test_cancellation_prevents_later_provider_deltas_from_becoming_durable(
    tmp_path: Path,
) -> None:
    provider = ScriptedProvider(
        [
            (
                ModelDelta.text(profile_id="scripted", sequence=1, text="kept"),
                ModelDelta.text(profile_id="scripted", sequence=2, text="discarded"),
                ModelDelta.done(
                    profile_id="scripted",
                    sequence=3,
                    finish_reason="stop",
                ),
            )
        ],
        cancel_after_first_delta=True,
    )
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((provider,)),
    )
    try:
        task = _scratch_task(service, "Cancel this turn")
        turn = _turn(service, task, "turn:cancel-stream")

        cancelled = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})
        events = service.invoke("events.subscribe", {"cursor": 0})["items"]
        delta_text = "".join(
            event["payload"]["text"]
            for event in events
            if event["event_type"] == "assistant.message.delta"
            and event["payload"].get("turn_id") == turn["id"]
        )
        messages = service.invoke(
            "messages.list",
            {"conversation_id": task["conversation_id"]},
        )["items"]

        assert cancelled["status"] == "cancelled"
        assert delta_text == "kept"
        assert [message["role"] for message in messages] == ["user"]
    finally:
        service.close()
