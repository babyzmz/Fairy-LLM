from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest

from fairy_core.domain.errors import IdempotencyConflictError, InvalidTransitionError
from fairy_core.providers import ModelDelta, ProviderRegistry
from fairy_core.providers.ports import ProviderUnavailableError
from fairy_core.transports.stdio import build_local_service
from tests.assistant.support import ScriptedProvider, wait_for_turn
from tests.assistant.test_application import BlockingProvider


def _conversation(service):
    return service.invoke("conversations.create", {
        "project_id": None, "workspace_type": "chat_scratch",
    })


def _submit(conversation, key, **overrides):
    return {
        "conversation_id": conversation["id"], "content": "Read this request",
        "profile_id": "scripted", "idempotency_key": key, "source": "pet",
        **overrides,
    }


def test_message_ingress_replays_once_and_isolates_conversations(tmp_path: Path) -> None:
    provider = BlockingProvider()
    service = build_local_service(tmp_path, provider_registry=ProviderRegistry((provider,)))
    try:
        first, second = _conversation(service), _conversation(service)
        request = _submit(first, "ingress:first")
        turn = service.invoke("assistant.messages.submit", request)
        replay = service.invoke("assistant.messages.submit", request)
        other = service.invoke("assistant.messages.submit", _submit(second, "ingress:second"))
        assert replay["id"] == turn["id"]
        assert other["conversation_id"] == second["id"]
        assert turn["conversation_id"] == first["id"]
        assert other["task_id"] != turn["task_id"]
        for conversation in (first, second):
            messages = service.invoke("messages.list", {
                "conversation_id": conversation["id"], "limit": 100,
            })["items"]
            assert [(message["role"], message["content"]) for message in messages] == [
                ("user", "Read this request"),
            ]
        for changes in ({"content": "Different request"}, {"conversation_id": second["id"]}):
            with pytest.raises(IdempotencyConflictError, match="idempotency"):
                service.invoke("assistant.messages.submit", {**request, **changes})
        with pytest.raises(InvalidTransitionError, match=r"active|stopping|busy"):
            service.invoke("assistant.messages.submit", _submit(first, "ingress:third"))
        with service._unit_of_work_factory() as unit:
            conversation = unit.state.get_conversation(UUID(first["id"]))
            assert str(conversation.active_task_id) == turn["task_id"]
    finally:
        service.close()


def test_message_ingress_rejects_missing_model_before_creating_task(tmp_path: Path) -> None:
    service = build_local_service(
        tmp_path, provider_registry=ProviderRegistry((ScriptedProvider([]),)),
    )
    try:
        conversation = _conversation(service)
        with pytest.raises(ProviderUnavailableError, match=r"profile|[Pp]rovider"):
            service.invoke("assistant.messages.submit", _submit(
                conversation, "ingress:invalid", profile_id="missing-profile",
            ))
        with service._unit_of_work_factory() as unit:
            restored = unit.state.get_conversation(UUID(conversation["id"]))
            assert restored.active_task_id is None
    finally:
        service.close()


def test_message_replay_preserves_a_user_pause(tmp_path: Path) -> None:
    provider = BlockingProvider()
    service = build_local_service(tmp_path, provider_registry=ProviderRegistry((provider,)))
    try:
        request = _submit(_conversation(service), "ingress:paused")
        # Hold dispatch, not persistence, to pause before the first node is claimed.
        with service._workflow_scheduler._lock:
            turn = service.invoke("assistant.messages.submit", request)
            paused = service.invoke("assistant.turns.pause", {"turn_id": turn["id"]})
            assert paused["workflow_summary"]["status"] == "paused"
            replay = service.invoke("assistant.messages.submit", request)
            assert replay["workflow_summary"]["status"] == "paused"
    finally:
        service.close()


def test_partial_submission_keeps_its_original_source_and_model_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = BlockingProvider()
    alternate = ScriptedProvider([], profile_id="alternate")
    service = build_local_service(
        tmp_path, provider_registry=ProviderRegistry((provider, alternate)),
    )
    try:
        request = _submit(_conversation(service), "ingress:partial")
        def interrupted(_request):
            raise RuntimeError("interrupted before Turn")
        with monkeypatch.context() as patch:
            patch.setattr(service._message_ingress, "_turn_factory", interrupted)
            with pytest.raises(RuntimeError, match="interrupted before Turn"):
                service.invoke("assistant.messages.submit", request)
        for changes in ({"source": "stt"}, {"profile_id": "alternate"}):
            with pytest.raises(IdempotencyConflictError, match="idempotency"):
                service.invoke("assistant.messages.submit", {**request, **changes})
        accepted = service.invoke("assistant.messages.submit", request)
        assert accepted["conversation_id"] == request["conversation_id"]
        assert accepted["profile_id"] == "scripted"
        assert alternate.requests == []
    finally:
        service.close()


def test_completed_submission_replays_after_restart_without_a_model_call(tmp_path: Path) -> None:
    provider = ScriptedProvider([(
        ModelDelta.text(profile_id="scripted", sequence=1, text="Verified reply"),
        ModelDelta.done(profile_id="scripted", sequence=2, finish_reason="stop"),
    )])
    service = build_local_service(tmp_path, provider_registry=ProviderRegistry((provider,)))
    try:
        request = _submit(_conversation(service), "ingress:restart")
        turn = service.invoke("assistant.messages.submit", request)
        completed = wait_for_turn(service, turn["id"])
        assert completed["status"] == "completed"
    finally:
        service.close()
    restarted_provider = ScriptedProvider([])
    restored = build_local_service(
        tmp_path, provider_registry=ProviderRegistry((restarted_provider,)),
    )
    try:
        replay = restored.invoke("assistant.messages.submit", request)
        assert replay["id"] == completed["id"]
        assert replay["status"] == "completed"
        assert restarted_provider.requests == []
        messages = restored.invoke("messages.list", {
            "conversation_id": request["conversation_id"], "limit": 100,
        })["items"]
        assert [(message["role"], message["content"]) for message in messages] == [
            ("user", "Read this request"), ("assistant", "Verified reply"),
        ]
    finally:
        restored.close()


def test_concurrent_duplicate_submissions_have_one_turn(tmp_path: Path) -> None:
    provider = BlockingProvider()
    service = build_local_service(tmp_path, provider_registry=ProviderRegistry((provider,)))
    try:
        request = _submit(_conversation(service), "ingress:concurrent")
        with ThreadPoolExecutor(max_workers=4) as callers:
            results = list(callers.map(
                lambda _: service.invoke("assistant.messages.submit", request), range(4),
            ))
        assert len({item["id"] for item in results}) == 1
        assert len({item["task_id"] for item in results}) == 1
        messages = service.invoke("messages.list", {
            "conversation_id": request["conversation_id"], "limit": 100,
        })["items"]
        assert len(messages) == 1
    finally:
        service.close()


def test_pet_submission_cannot_target_a_project_conversation(tmp_path: Path) -> None:
    service = build_local_service(
        tmp_path, provider_registry=ProviderRegistry((ScriptedProvider([]),)),
    )
    try:
        project = service.invoke("projects.create", {
            "name": "Bound project", "residency": "local_only",
        })
        conversation = service.invoke("conversations.create", {
            "project_id": project["project"]["id"], "workspace_type": "project_chat",
        })
        with pytest.raises(InvalidTransitionError, match="scratch chat"):
            service.invoke("assistant.messages.submit", _submit(conversation, "ingress:project"))
        with service._unit_of_work_factory() as unit:
            assert unit.state.get_conversation(UUID(conversation["id"])).active_task_id is None
    finally:
        service.close()


def test_disabled_profile_does_not_leave_a_submission_task(tmp_path: Path) -> None:
    provider = ScriptedProvider([])
    provider.profile = replace(provider.profile, enabled=False)
    service = build_local_service(tmp_path, provider_registry=ProviderRegistry((provider,)))
    try:
        conversation = _conversation(service)
        with pytest.raises(ProviderUnavailableError, match="disabled"):
            service.invoke("assistant.messages.submit", _submit(conversation, "ingress:disabled"))
        with service._unit_of_work_factory() as unit:
            assert unit.state.get_conversation(UUID(conversation["id"])).active_task_id is None
    finally:
        service.close()
