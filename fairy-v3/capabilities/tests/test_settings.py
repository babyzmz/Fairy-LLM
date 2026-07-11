from __future__ import annotations

import json

import pytest

from fairy_capabilities.composition import build_provider_registry
from fairy_capabilities.settings import (
    EnvironmentProviderSecretResolver,
    ProviderSettings,
)


def _environment() -> dict[str, str]:
    return {
        "FAIRY_PROVIDER_PROFILES_JSON": json.dumps(
            [
                {
                    "id": "openrouter-free",
                    "display_name": "OpenRouter Free",
                    "kind": "openai_compatible",
                    "base_url": "https://openrouter.ai/api/v1",
                    "model_id": "cohere/north-mini-code:free",
                    "capabilities": ["text", "tools"],
                    "credential_ref": "openrouter",
                    "fallback_profile_id": None,
                    "timeout_seconds": 60,
                    "enabled": True,
                }
            ]
        ),
        "FAIRY_PROVIDER_SECRET_REFS_JSON": json.dumps(
            {"openrouter": "FAIRY_PROVIDER_SECRET_OPENROUTER"}
        ),
        "FAIRY_PROVIDER_SECRET_OPENROUTER": "test-only-secret",
    }


def test_settings_store_only_secret_environment_names() -> None:
    environment = _environment()

    settings = ProviderSettings.from_environment(environment)
    resolver = EnvironmentProviderSecretResolver(
        settings.secret_environment_names,
        environment,
    )

    assert settings.profiles[0].id == "openrouter-free"
    assert settings.secret_environment_names == {"openrouter": "FAIRY_PROVIDER_SECRET_OPENROUTER"}
    assert "test-only-secret" not in repr(settings)
    assert str(resolver.resolve("openrouter")) == "<redacted>"


def test_settings_reject_non_scoped_secret_environment_names() -> None:
    environment = _environment()
    environment["FAIRY_PROVIDER_SECRET_REFS_JSON"] = json.dumps(
        {"openrouter": "OPENROUTER_API_KEY"}
    )

    with pytest.raises(ValueError, match="FAIRY_PROVIDER_SECRET_"):
        ProviderSettings.from_environment(environment)


def test_composition_reports_presence_without_exposing_secret_reference() -> None:
    public = build_provider_registry(_environment()).list_public()

    assert len(public) == 1
    assert public[0].credential_required is True
    assert public[0].credential_configured is True
    assert not hasattr(public[0], "credential_ref")

    missing = _environment()
    del missing["FAIRY_PROVIDER_SECRET_OPENROUTER"]
    missing_public = build_provider_registry(missing).list_public()
    assert missing_public[0].credential_configured is False


def test_empty_environment_builds_provider_free_registry() -> None:
    assert build_provider_registry({}).list_public() == ()
