from __future__ import annotations

import pytest

from fairy_core.providers.models import (
    ModelMessage,
    ModelRequest,
    ModelRole,
    ProviderCapability,
    ProviderKind,
    ProviderProfile,
)
from fairy_core.providers.ports import SecretValue


def test_cloud_profile_requires_https_without_url_credentials() -> None:
    profile = ProviderProfile.create(
        profile_id="cloud-default",
        display_name="Cloud default",
        kind=ProviderKind.OPENAI_COMPATIBLE,
        base_url="https://models.example.test/v1/",
        model_id="fairy-large",
        capabilities=frozenset({ProviderCapability.TEXT, ProviderCapability.TOOLS}),
        credential_ref="cloud-primary",
        fallback_profile_id=None,
        timeout_seconds=45,
        enabled=True,
    )

    assert profile.base_url == "https://models.example.test/v1"
    assert profile.model_id == "fairy-large"
    for invalid in (
        "http://models.example.test/v1",
        "https://user:secret@models.example.test/v1",
        "https://models.example.test/v1?token=secret",
        "file:///models",
    ):
        with pytest.raises(ValueError, match="base_url"):
            ProviderProfile.create(
                profile_id="bad",
                display_name="Bad",
                kind=ProviderKind.OPENAI_COMPATIBLE,
                base_url=invalid,
                model_id="model",
                capabilities=frozenset({ProviderCapability.TEXT}),
                credential_ref=None,
                fallback_profile_id=None,
                timeout_seconds=30,
                enabled=True,
            )


def test_local_profile_allows_only_literal_loopback_http() -> None:
    assert (
        ProviderProfile.create(
            profile_id="local",
            display_name="Local",
            kind=ProviderKind.LOCAL_OPENAI_COMPATIBLE,
            base_url="http://127.0.0.1:11434/v1",
            model_id="qwen",
            capabilities=frozenset({ProviderCapability.TEXT}),
            credential_ref=None,
            fallback_profile_id=None,
            timeout_seconds=20,
            enabled=True,
        ).base_url
        == "http://127.0.0.1:11434/v1"
    )
    for invalid in (
        "http://localhost:11434/v1",
        "http://192.168.1.2:11434/v1",
        "https://127.0.0.1:11434/v1",
    ):
        with pytest.raises(ValueError, match="loopback"):
            ProviderProfile.create(
                profile_id="local",
                display_name="Local",
                kind=ProviderKind.LOCAL_OPENAI_COMPATIBLE,
                base_url=invalid,
                model_id="qwen",
                capabilities=frozenset({ProviderCapability.TEXT}),
                credential_ref=None,
                fallback_profile_id=None,
                timeout_seconds=20,
                enabled=True,
            )


def test_profile_and_request_require_text_and_requested_modalities() -> None:
    with pytest.raises(ValueError, match="text"):
        ProviderProfile.create(
            profile_id="audio-only",
            display_name="Audio only",
            kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://models.example.test/v1",
            model_id="audio",
            capabilities=frozenset({ProviderCapability.TTS}),
            credential_ref="audio",
            fallback_profile_id=None,
            timeout_seconds=30,
            enabled=True,
        )

    request = ModelRequest.create(
        profile_id="cloud-default",
        messages=(ModelMessage.create(role=ModelRole.USER, content="Hello"),),
        tools=(),
        required_capabilities=frozenset({ProviderCapability.TEXT}),
        max_output_tokens=512,
    )
    assert request.messages[0].content == "Hello"
    with pytest.raises(ValueError, match="messages"):
        ModelRequest.create(
            profile_id="cloud-default",
            messages=(),
            tools=(),
            required_capabilities=frozenset({ProviderCapability.TEXT}),
            max_output_tokens=512,
        )


def test_secret_value_never_reveals_itself_through_string_conversion() -> None:
    secret = SecretValue.from_text("super-secret-token")

    assert str(secret) == "<redacted>"
    assert repr(secret) == "SecretValue(<redacted>)"
    assert "super-secret-token" not in f"{secret!r} {secret}"
    assert secret.reveal() == "super-secret-token"

    with pytest.raises(ValueError, match="secret"):
        SecretValue.from_text("   ")
