from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from fairy_core.assistant.tools import ToolExecutor
from fairy_core.providers import ProviderRegistry
from fairy_core.research.ports import FetchPort, SearchPort

from fairy_capabilities.models.openai_compatible import OpenAICompatibleProvider
from fairy_capabilities.settings import (
    EnvironmentProviderSecretResolver,
    ProviderSettings,
)
from fairy_capabilities.web.brave import BraveSearchAdapter
from fairy_capabilities.web.fetch import SafeWebFetcher
from fairy_capabilities.web.tools import WebToolExecutor


@dataclass(frozen=True, slots=True)
class WebCapabilities:
    search_port: SearchPort
    fetch_port: FetchPort
    executor: WebToolExecutor


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


def build_web_capabilities(
    environment: Mapping[str, str] | None = None,
) -> WebCapabilities:
    configured = os.environ if environment is None else environment
    settings = ProviderSettings.from_environment(configured)
    resolver = EnvironmentProviderSecretResolver(
        settings.secret_environment_names,
        configured,
    )
    credential_ref = configured.get(
        "FAIRY_WEB_BRAVE_CREDENTIAL_REF",
        "brave",
    ).strip()
    search = BraveSearchAdapter(
        secret=resolver.try_resolve(credential_ref or None),
    )
    fetch = SafeWebFetcher()
    executor = WebToolExecutor(
        search_port=search,
        fetch_port=fetch,
    )
    return WebCapabilities(
        search_port=search,
        fetch_port=fetch,
        executor=executor,
    )


def build_tool_executor(
    environment: Mapping[str, str] | None = None,
) -> ToolExecutor:
    return build_web_capabilities(environment).executor
