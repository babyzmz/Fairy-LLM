from __future__ import annotations

import os
from collections.abc import Mapping

from fairy_core.assistant.tools import ToolExecutor, UnavailableToolExecutor
from fairy_core.providers import ProviderRegistry

from fairy_capabilities.models.openai_compatible import OpenAICompatibleProvider
from fairy_capabilities.settings import (
    EnvironmentProviderSecretResolver,
    ProviderSettings,
)


def build_provider_registry(
    environment: Mapping[str, str] | None = None,
) -> ProviderRegistry:
    configured = os.environ if environment is None else environment
    settings = ProviderSettings.from_environment(configured)
    resolver = EnvironmentProviderSecretResolver(
        settings.secret_environment_names,
        configured,
    )
    providers = tuple(
        OpenAICompatibleProvider(
            profile=profile,
            secret=resolver.try_resolve(profile.credential_ref),
        )
        for profile in settings.profiles
    )
    return ProviderRegistry(providers)


def build_tool_executor() -> ToolExecutor:
    """Return a fail-closed executor until capability adapters register handlers."""
    return UnavailableToolExecutor()
