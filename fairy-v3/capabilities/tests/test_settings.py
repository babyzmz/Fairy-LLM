from __future__ import annotations

import json
from pathlib import Path

import pytest

from fairy_capabilities.composition import (
    build_capability_bundle,
    build_provider_registry,
    build_web_capabilities,
)
from fairy_capabilities.settings import (
    EnvironmentProviderSecretResolver,
    ProviderSettings,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


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


def test_openrouter_profile_preset_is_secret_free_and_allowlisted() -> None:
    preset_path = _REPOSITORY_ROOT / "config" / "openrouter.providers.json"
    raw = preset_path.read_text(encoding="utf-8")
    settings = ProviderSettings.from_environment(
        {
            "FAIRY_PROVIDER_PROFILES_JSON": raw,
            "FAIRY_PROVIDER_SECRET_REFS_JSON": json.dumps(
                {"openrouter": "FAIRY_PROVIDER_SECRET_OPENROUTER"}
            ),
        }
    )

    assert [profile.model_id for profile in settings.profiles] == [
        "deepseek/deepseek-v4-pro",
        "z-ai/glm-5.2",
        "moonshotai/kimi-k2.7-code",
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "qwen/qwen3-coder:free",
    ]
    assert all(profile.fallback_profile_id is None for profile in settings.profiles)
    assert "tencent/hy3:free" not in raw
    assert "openrouter/free" not in raw
    assert "sk-or-" not in raw


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


def test_web_composition_uses_scoped_secret_reference_without_exposing_value() -> None:
    secret = "brave-test-secret"
    environment = {
        "FAIRY_PROVIDER_SECRET_REFS_JSON": json.dumps({"brave": "FAIRY_PROVIDER_SECRET_BRAVE"}),
        "FAIRY_PROVIDER_SECRET_BRAVE": secret,
        "FAIRY_PROVIDER_BRAVE_CREDENTIAL_REF": "brave",
    }

    configured = build_web_capabilities(environment)
    unavailable = build_web_capabilities({})
    try:
        assert configured.search_port.health().status == "available"
        assert unavailable.search_port.health().status == "unavailable"
        assert configured.executor.fetch_port is configured.fetch_port
        assert secret not in repr(configured)
    finally:
        configured.executor.close()
        unavailable.executor.close()


def test_information_composition_reports_alpha_vantage_key_presence_only() -> None:
    secret = "alpha-test-secret"
    environment = {
        "FAIRY_PROVIDER_SECRET_REFS_JSON": json.dumps(
            {"alpha_vantage": "FAIRY_PROVIDER_SECRET_ALPHA_VANTAGE"}
        ),
        "FAIRY_PROVIDER_SECRET_ALPHA_VANTAGE": secret,
        "FAIRY_PROVIDER_ALPHA_VANTAGE_CREDENTIAL_REF": "alpha_vantage",
    }

    configured = build_capability_bundle(environment)
    unavailable = build_capability_bundle({})
    try:
        assert configured.information.markets.health().status == "available"
        assert unavailable.information.markets.health().status == "unavailable"
        assert [health.provider for health in configured.information.health()] == [
            "open_meteo",
            "python_zoneinfo",
            "frankfurter_v2",
            "alpha_vantage",
        ]
        assert secret not in repr(configured)
    finally:
        configured.executor.close()
        unavailable.executor.close()


def test_capability_composition_preserves_a_typed_host_delegate() -> None:
    class ClosingDelegate:
        def __init__(self) -> None:
            self.close_count = 0

        def execute(self, definition, scope, arguments):  # pragma: no cover - delegation shape
            raise AssertionError((definition, scope, arguments))

        def close(self) -> None:
            self.close_count += 1

    delegate = ClosingDelegate()
    bundle = build_capability_bundle({}, delegate=delegate)
    bundle.executor.close()

    assert delegate.close_count == 1
