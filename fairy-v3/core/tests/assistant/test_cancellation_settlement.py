from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import monotonic, sleep
from uuid import UUID

import pytest

from fairy_core.domain.errors import InvalidTransitionError, ProjectBusyError
from fairy_core.providers import ModelDelta, ProviderRegistry
from fairy_core.transports.stdio import build_local_service
from tests.assistant.cancellation_support import BlockingToolStop
from tests.assistant.support import ScriptedProvider
from tests.assistant.test_application import _scratch_task, _turn


def test_cancel_acknowledges_before_slow_cleanup_but_waits_for_real_settlement(tmp_path: Path):
    executor = BlockingToolStop()
    provider = ScriptedProvider([(
        ModelDelta.tool_call(
            profile_id="scripted", sequence=1, tool_call_id="blocking-read",
            tool_name="web.search", arguments_fragment='{"query":"current sources"}',
        ),
        ModelDelta.done(profile_id="scripted", sequence=2, finish_reason="tool_calls"),
    )])
    service = build_local_service(
        tmp_path, provider_registry=ProviderRegistry((provider,)), tool_executor=executor,
    )
    callers = ThreadPoolExecutor(max_workers=1)
    try:
        task = _scratch_task(service, "Read current sources")
        other = _turn(service, _scratch_task(service, "Explain another topic"), "other-chat")
        turn = _turn(service, task, "stop-slow-tool")
        service.invoke("assistant.turns.start", {"turn_id": turn["id"]})
        assert executor.started.wait(3)
        future = callers.submit(service.invoke, "assistant.turns.cancel", {
            "turn_id": turn["id"], "expected_cancellation_revision": 0,
        })
        accepted = future.result(timeout=0.9)
        assert accepted["status"] == "cancelled"
        assert accepted["cancellation_pending"] is True
        events = service.invoke("events.subscribe", {"cursor": 0})["items"]
        assert any(
            event["event_type"] == "assistant.turn.cancel_requested"
            and event["payload"].get("turn_id") == turn["id"]
            for event in events
        )
        assert executor.stop_started.wait(1)
        assert service.invoke("assistant.turns.get", {
            "turn_id": other["id"],
        })["status"] == "created"
        with pytest.raises(InvalidTransitionError, match="stopping"):
            service.invoke("assistant.turns.retry", {
                "turn_id": turn["id"], "idempotency_key": "retry-before-stop",
            })
        with pytest.raises(InvalidTransitionError, match="stopping"):
            _turn(service, task, "new-turn-before-stop")
        executor.release_stop.set()
        with service._unit_of_work_factory() as unit:
            pending = unit.assistant.nonterminal_turns_for_tasks((UUID(task["id"]),))
        assert [item.id for item in pending] == [UUID(turn["id"])]
        with pytest.raises(ProjectBusyError, match="stopping"):
            service._cancel_assistant_turn_by_id(
                UUID(turn["id"]), expected_cancellation_revision=1,
                strict_tool_cancellation=True,
            )
        assert service.invoke("assistant.turns.get", {
            "turn_id": turn["id"],
        })["cancellation_pending"] is True
        executor.release_tool.set()
        deadline = monotonic() + 3
        while monotonic() < deadline:
            settled = service.invoke("assistant.turns.get", {"turn_id": turn["id"]})
            if not settled["cancellation_pending"]:
                break
            sleep(0.01)
        assert settled["cancellation_pending"] is False
        messages = service.invoke("messages.list", {
            "conversation_id": task["conversation_id"], "limit": 50,
        })["items"]
        assert not any(message["role"] == "assistant" for message in messages)
    finally:
        executor.release_stop.set()
        executor.release_tool.set()
        callers.shutdown(wait=True)
        service.close()
