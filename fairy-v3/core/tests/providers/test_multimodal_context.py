from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from uuid import UUID

import pytest

from fairy_core.domain.errors import CapabilityUnavailableError
from fairy_core.providers import (
    ModelDelta,
    ProviderCapability,
    ProviderRegistry,
)
from fairy_core.transports.stdio import build_local_service
from tests.assistant.support import ScriptedProvider

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
_PNG_HASH = hashlib.sha256(_PNG).hexdigest()


def _task(service) -> dict[str, object]:
    conversation = service.invoke(
        "conversations.create",
        {"project_id": None, "workspace_type": "chat_scratch"},
    )
    return service.invoke(
        "tasks.create",
        {
            "conversation_id": conversation["id"],
            "user_request": "Explain the captured game state",
            "operation_mode": "answer",
            "execution_target": "local",
            "idempotency_key": "task:vision",
        },
    )["task"]


def _attachment(*, persistence: str = "ephemeral", content_hash: str = _PNG_HASH):
    return {
        "media_type": "image/png",
        "png_base64": base64.b64encode(_PNG).decode("ascii"),
        "content_hash": content_hash,
        "width": 1,
        "height": 1,
        "source_label": "Game window",
        "captured_at_ms": 1_784_000_000_000,
        "persistence": persistence,
    }


@pytest.mark.parametrize(
    ("cancel_after_first_delta", "expected_status"),
    [(False, "completed"), (True, "cancelled")],
)
def test_multimodal_turn_binds_untrusted_image_and_zeros_ephemeral_buffer(
    tmp_path: Path,
    cancel_after_first_delta: bool,
    expected_status: str,
) -> None:
    provider = ScriptedProvider(
        [
            (
                ModelDelta.text(profile_id="scripted", sequence=1, text="Visible state"),
                ModelDelta.done(
                    profile_id="scripted",
                    sequence=2,
                    finish_reason="stop",
                ),
            )
        ],
        cancel_after_first_delta=cancel_after_first_delta,
        capabilities=frozenset(
            {
                ProviderCapability.TEXT,
                ProviderCapability.TOOLS,
                ProviderCapability.VISION,
            }
        ),
    )
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((provider,)),
    )
    try:
        task = _task(service)
        turn = service.invoke(
            "assistant.turns.create",
            {
                "task_id": task["id"],
                "profile_id": "scripted",
                "idempotency_key": f"turn:vision:{expected_status}",
                "image_attachments": [_attachment()],
            },
        )

        result = service.invoke("assistant.turns.run", {"turn_id": turn["id"]})

        assert result["status"] == expected_status
        request = provider.requests[0]
        assert ProviderCapability.VISION in request.required_capabilities
        assert "untrusted screen content" in request.messages[0].content.lower()
        image = request.messages[-1].images[0]
        assert image.task_id == UUID(str(task["id"]))
        assert image.content_hash == _PNG_HASH
        assert image.untrusted_data is True
        assert image.label == "untrusted_screen_content"
        assert provider.observed_image_bytes == [_PNG]
        assert not any(image.data)
        assert not (tmp_path / "perception" / str(task["id"])).exists()
    finally:
        service.close()


def test_image_hash_and_vision_capability_are_revalidated_by_core(tmp_path: Path) -> None:
    provider = ScriptedProvider(
        [],
        capabilities=frozenset({ProviderCapability.TEXT, ProviderCapability.TOOLS}),
    )
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((provider,)),
    )
    try:
        task = _task(service)
        with pytest.raises(ValueError, match="hash"):
            service.invoke(
                "assistant.turns.create",
                {
                    "task_id": task["id"],
                    "profile_id": "scripted",
                    "idempotency_key": "turn:bad-hash",
                    "image_attachments": [_attachment(content_hash="0" * 64)],
                },
            )
        with pytest.raises(CapabilityUnavailableError, match="vision"):
            service.invoke(
                "assistant.turns.create",
                {
                    "task_id": task["id"],
                    "profile_id": "scripted",
                    "idempotency_key": "turn:no-vision",
                    "image_attachments": [_attachment()],
                },
            )
    finally:
        service.close()


def test_confirmed_conversation_image_is_written_only_inside_managed_store(
    tmp_path: Path,
) -> None:
    provider = ScriptedProvider(
        [
            (
                ModelDelta.text(profile_id="scripted", sequence=1, text="Saved"),
                ModelDelta.done(
                    profile_id="scripted",
                    sequence=2,
                    finish_reason="stop",
                ),
            )
        ],
        capabilities=frozenset({ProviderCapability.TEXT, ProviderCapability.VISION}),
    )
    service = build_local_service(
        tmp_path,
        provider_registry=ProviderRegistry((provider,)),
    )
    try:
        task = _task(service)
        turn = service.invoke(
            "assistant.turns.create",
            {
                "task_id": task["id"],
                "profile_id": "scripted",
                "idempotency_key": "turn:persisted-vision",
                "image_attachments": [_attachment(persistence="conversation")],
            },
        )
        service.invoke("assistant.turns.run", {"turn_id": turn["id"]})

        stored = tmp_path / "perception" / str(task["id"]) / f"{_PNG_HASH}.png"
        assert stored.read_bytes() == _PNG
    finally:
        service.close()
