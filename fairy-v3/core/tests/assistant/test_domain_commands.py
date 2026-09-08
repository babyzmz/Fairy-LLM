from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID

import pytest

from fairy_core.domain.errors import IdempotencyConflictError, InvalidTransitionError
from fairy_core.providers import ProviderRegistry
from fairy_core.transports.stdio import build_local_service
from tests.assistant.test_application import BlockingProvider


def _dispatch(service, text, key, **context):
    return service.invoke("assistant.commands.dispatch", {
        "text": text, "idempotency_key": key, **context,
    })


def test_new_command_is_durable_idempotent_and_clear_preserves_the_original(tmp_path: Path):
    service = build_local_service(tmp_path)
    try:
        with ThreadPoolExecutor(max_workers=4) as workers:
            results = list(workers.map(
                lambda _: _dispatch(service, "/new", "new:once"), range(4),
            ))
        first = results[0]["conversation"]
        assert all(item["conversation"]["id"] == first["id"] for item in results)
        second = _dispatch(service, "/clear", "new:second", conversation_id=first["id"])
        assert second["conversation"]["id"] != first["id"]
        with service._unit_of_work_factory() as unit:
            original = unit.state.get_conversation(UUID(first["id"]))
            assert original.deleted_at is None
            assert original.base_version_id is not None
        with pytest.raises(IdempotencyConflictError):
            _dispatch(service, "/clear", "new:once")
    finally:
        service.close()
    restored = build_local_service(tmp_path)
    try:
        replay = _dispatch(restored, "/new", "new:once")
        assert replay["conversation"]["id"] == first["id"]
        assert replay["conversation"]["base_version_id"] == first["base_version_id"]
    finally:
        restored.close()


@pytest.mark.parametrize("text", [
    "Explain /new", "`/new`", "```\n/new\n```", "/new now", "/stop other", "/unknown",
    "/permission root", "/help\n/new",
])
def test_only_an_exact_explicit_command_is_executed(tmp_path: Path, text: str):
    service = build_local_service(tmp_path)
    try:
        with pytest.raises(ValueError):
            _dispatch(service, text, "invalid:request")
    finally:
        service.close()


def test_navigation_returns_actions_without_changing_device_permissions(tmp_path: Path):
    service = build_local_service(tmp_path)
    try:
        before = service.invoke("permissions.update", {
            "profile": "standard", "capability_overrides": {}, "expected_revision": 0,
            "idempotency_key": "initial:settings",
        })
        result = _dispatch(service, "/permission autonomous", "permission:one")
        assert result["ui_action"] == {"kind": "request_permission", "profile": "autonomous"}
        assert service.invoke("permissions.get", {}) == before
        assert _dispatch(service, "/project", "project:one")["ui_action"] == {
            "kind": "show_project", "profile": None,
        }
        assert "/stop" in _dispatch(service, "/help", "help:one")["notice"]
    finally:
        service.close()


def test_stop_requires_the_explicit_turn_to_belong_to_the_conversation(tmp_path: Path):
    provider = BlockingProvider()
    service = build_local_service(tmp_path, provider_registry=ProviderRegistry((provider,)))
    try:
        first = _dispatch(service, "/new", "chat:first")["conversation"]
        second = _dispatch(service, "/new", "chat:second")["conversation"]
        turn = service.invoke("assistant.messages.submit", {
            "conversation_id": first["id"], "content": "Hello", "profile_id": "scripted",
            "source": "chat", "idempotency_key": "message:first",
        })
        context = {"turn_id": turn["id"], "expected_cancellation_revision": 0}
        with pytest.raises(InvalidTransitionError, match="conversation"):
            _dispatch(service, "/stop", "stop:wrong", conversation_id=second["id"], **context)
        unchanged = service.invoke("assistant.turns.get", {"turn_id": turn["id"]})
        assert unchanged["status"] != "cancelled"
        result = _dispatch(service, "/stop", "stop:right", conversation_id=first["id"], **context)
        assert result["turn"]["status"] == "cancelled"
        replay = _dispatch(service, "/stop", "stop:right", conversation_id=first["id"], **context)
        assert replay["turn"]["id"] == turn["id"]
        assert replay["turn"]["cancellation_revision"] == result["turn"]["cancellation_revision"]
    finally:
        service.close()


def test_new_command_rechecks_current_registry_policy(tmp_path: Path):
    service = build_local_service(tmp_path)
    try:
        assert any(item["name"] == "new" and item["available"] for item in
                   service.invoke("capabilities.get", {})["slash_commands"])
        service.invoke("permissions.update", {
            "profile": "standard", "capability_overrides": {"workspace.create_scratch": False},
            "expected_revision": 0, "idempotency_key": "disable:new",
        })
        with pytest.raises(InvalidTransitionError, match="unavailable"):
            _dispatch(service, "/new", "new:disabled")
    finally:
        service.close()


def test_replaying_new_does_not_resurrect_a_deleted_chat(tmp_path: Path):
    service = build_local_service(tmp_path)
    try:
        created = _dispatch(service, "/new", "deleted:one")["conversation"]
        with service._unit_of_work_factory() as unit:
            conversation = unit.state.get_conversation(UUID(created["id"]))
            conversation.delete(expected_revision=conversation.revision)
            unit.state.save_conversation(conversation)
            unit.commit()
        with pytest.raises(InvalidTransitionError, match="unavailable"):
            _dispatch(service, "/new", "deleted:one")
    finally:
        service.close()
