from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from threading import Event, current_thread

import pytest

from fairy_core.assistant.repository import SqlAlchemyAssistantRepository
from fairy_core.domain.errors import IdempotencyConflictError, InvalidTransitionError
from fairy_core.providers import ModelDelta, ProviderRegistry
from fairy_core.transports.stdio import build_local_service
from tests.assistant.support import ScriptedProvider, wait_for_turn
from tests.assistant.test_application import BlockingProvider
from tests.assistant.test_message_ingress import _conversation, _submit


def test_cancellation_before_submission_survives_restart_and_prevents_model_work(tmp_path: Path):
    service = build_local_service(tmp_path)
    try:
        conversation = _conversation(service)
        cancelled = service.invoke("assistant.messages.cancel", {"idempotency_key": "pending:one"})
        assert cancelled["accepted"] is True
        assert cancelled["turn"] is None
    finally:
        service.close()
    provider = ScriptedProvider([])
    restored = build_local_service(tmp_path, provider_registry=ProviderRegistry((provider,)))
    try:
        with pytest.raises(InvalidTransitionError, match="cancelled"):
            restored.invoke("assistant.messages.submit", _submit(conversation, "pending:one"))
        assert restored.invoke("messages.list", {
            "conversation_id": conversation["id"], "limit": 10,
        })["items"] == []
        assert provider.requests == []
    finally:
        restored.close()


def test_cancellation_rechecks_conversation_after_a_concurrent_submission_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    provider = BlockingProvider()
    service = build_local_service(tmp_path, provider_registry=ProviderRegistry((provider,)))
    looked_up, release = Event(), Event()
    original = SqlAlchemyAssistantRepository.message_submission_conversation
    def delayed(repository, digest):
        result = original(repository, digest)
        if current_thread().name.startswith("cancel-scope-race") and not looked_up.is_set():
            assert result is None
            looked_up.set()
            assert release.wait(5)
        return result
    monkeypatch.setattr(SqlAlchemyAssistantRepository, "message_submission_conversation", delayed)
    try:
        first, second = _conversation(service), _conversation(service)
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="cancel-scope-race") as workers:
            wrong_cancel = workers.submit(service.invoke, "assistant.messages.cancel", {
                "idempotency_key": "scope:race", "conversation_id": first["id"],
            })
            try:
                assert looked_up.wait(2)
                turn = service.invoke("assistant.messages.submit", _submit(second, "scope:race"))
            finally:
                release.set()
            with pytest.raises(IdempotencyConflictError):
                wrong_cancel.result(timeout=2)
        current = service.invoke("assistant.turns.get", {"turn_id": turn["id"]})
        assert current["status"] != "cancelled"
        with service._unit_of_work_factory() as unit:
            assert not unit.assistant.message_cancellation_requested(
                sha256(b"scope:race").hexdigest(), None,
            )
    finally:
        release.set()
        service.close()


def test_cancel_during_turn_preparation_is_fast_and_conversation_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    provider = ScriptedProvider([])
    service = build_local_service(tmp_path, provider_registry=ProviderRegistry((provider,)))
    entered, release = Event(), Event()
    original = service._message_ingress._turn_factory
    def delayed(request):
        entered.set()
        assert release.wait(5)
        return original(request)
    monkeypatch.setattr(service._message_ingress, "_turn_factory", delayed)
    try:
        first, second = _conversation(service), _conversation(service)
        with ThreadPoolExecutor(max_workers=2) as workers:
            submitted = workers.submit(
                service.invoke, "assistant.messages.submit", _submit(first, "pending:slow"),
            )
            try:
                assert entered.wait(2)
                with pytest.raises(IdempotencyConflictError):
                    service.invoke("assistant.messages.cancel", {
                        "idempotency_key": "pending:slow", "conversation_id": second["id"],
                    })
                cancellation = workers.submit(service.invoke, "assistant.messages.cancel", {
                    "idempotency_key": "pending:slow", "conversation_id": first["id"],
                }).result(timeout=0.8)
                assert cancellation["accepted"] is True
            finally:
                release.set()
            turn = submitted.result(timeout=3)
        assert turn["status"] == "cancelled"
        assert provider.requests == []
        replay = service.invoke("assistant.messages.cancel", {
            "idempotency_key": "pending:slow", "conversation_id": first["id"],
        })
        assert replay["turn"]["id"] == turn["id"]
        assert replay["turn"]["cancellation_revision"] == turn["cancellation_revision"]
    finally:
        release.set()
        service.close()


def test_restart_cannot_execute_after_cancel_intent_committed_before_turn_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    provider = ScriptedProvider([])
    service = build_local_service(tmp_path, provider_registry=ProviderRegistry((provider,)))
    key = "pending:crash-window"
    def interrupted(_request):
        raise RuntimeError("injected process interruption")
    try:
        conversation = _conversation(service)
        with service._workflow_scheduler._lock:
            with monkeypatch.context() as patch:
                patch.setattr(service._message_ingress._scheduler, "start", interrupted)
                with pytest.raises(RuntimeError, match="interruption"):
                    service.invoke("assistant.messages.submit", _submit(conversation, key))
            with service._unit_of_work_factory() as unit:
                turn = unit.assistant.find_turn_by_idempotency_key(
                    "message-turn:" + sha256(key.encode()).hexdigest(),
                )
                assert turn is not None
                turn_id = str(turn.id)
            service.invoke("assistant.turns.pause", {"turn_id": turn_id})
            with monkeypatch.context() as patch:
                patch.setattr(service._message_ingress, "_turn_canceller", interrupted)
                with pytest.raises(RuntimeError, match="interruption"):
                    service.invoke("assistant.messages.cancel", {
                        "idempotency_key": key, "conversation_id": conversation["id"],
                    })
        assert provider.requests == []
    finally:
        service.close()
    restored_provider = ScriptedProvider([(
        ModelDelta.text(profile_id="scripted", sequence=1, text="Should not run"),
        ModelDelta.done(profile_id="scripted", sequence=2, finish_reason="stop"),
    )])
    restored = build_local_service(
        tmp_path, provider_registry=ProviderRegistry((restored_provider,)),
    )
    try:
        restored.invoke("assistant.turns.resume", {"turn_id": turn_id})
        settled = wait_for_turn(restored, turn_id, status="cancelled")
        assert settled["status"] == "cancelled"
        assert restored_provider.requests == []
    finally:
        restored.close()
