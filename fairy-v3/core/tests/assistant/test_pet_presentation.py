from pathlib import Path
from uuid import UUID

import pytest

from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.providers import ModelDelta, ProviderRegistry
from fairy_core.transports.stdio import build_local_service
from tests.assistant.support import ScriptedProvider, wait_for_turn


def test_pet_presentation_is_scoped_bounded_and_recovers_from_persistent_messages(tmp_path: Path):
    replies = ["First reply " + "🦋" * 1400, "Second reply"]
    provider = ScriptedProvider([(
        ModelDelta.text(profile_id="scripted", sequence=1, text=reply),
        ModelDelta.done(profile_id="scripted", sequence=2, finish_reason="stop"),
    ) for reply in replies])
    service = build_local_service(tmp_path, provider_registry=ProviderRegistry((provider,)))
    conversations, turns = [], []
    try:
        for index in range(2):
            conversation = service.invoke("conversations.create", {
                "project_id": None, "workspace_type": "chat_scratch",
            })
            conversations.append(conversation)
            empty = service.invoke("assistant.conversations.presentation.get", {
                "conversation_id": conversation["id"],
            })
            assert empty["turn"] is None and empty["reply"] is None
            turn = service.invoke("assistant.messages.submit", {
                "conversation_id": conversation["id"], "content": "Hello", "profile_id": "scripted",
                "source": "pet", "idempotency_key": f"presentation:{index}",
            })
            wait_for_turn(service, turn["id"])
            turns.append(turn)
    finally:
        service.close()
    restored = build_local_service(tmp_path)
    try:
        for index in (1, 0, 1):
            result = restored.invoke("assistant.conversations.presentation.get", {
                "conversation_id": conversations[index]["id"],
            })
            assert result["conversation_id"] == conversations[index]["id"]
            assert result["turn"]["id"] == turns[index]["id"]
            assert result["turn"]["status"] == "completed"
            assert result["reply"]["text"] == replies[index][:1200]
            assert set(result["turn"]) == {
                "id", "conversation_id", "status", "cancellation_revision", "cancellation_pending",
                "updated_at", "error_code",
            }
            assert set(result) == {"conversation_id", "turn", "reply"}
    finally:
        restored.close()


def test_pet_presentation_rejects_deleted_and_project_chats(tmp_path: Path):
    service = build_local_service(tmp_path)
    try:
        conversation = service.invoke("conversations.create", {
            "project_id": None, "workspace_type": "chat_scratch",
        })
        with service._unit_of_work_factory() as unit:
            current = unit.state.get_conversation(UUID(conversation["id"]))
            current.delete(expected_revision=current.revision)
            unit.state.save_conversation(current)
            unit.commit()
        with pytest.raises(InvalidTransitionError):
            service.invoke("assistant.conversations.presentation.get", {
                "conversation_id": conversation["id"],
            })
        project = service.invoke("projects.create", {
            "name": "Private project", "residency": "local_only",
        })
        project_chat = service.invoke("conversations.create", {
            "project_id": project["project"]["id"], "workspace_type": "project_chat",
        })
        with pytest.raises(InvalidTransitionError):
            service.invoke("assistant.conversations.presentation.get", {
                "conversation_id": project_chat["id"],
            })
    finally:
        service.close()
