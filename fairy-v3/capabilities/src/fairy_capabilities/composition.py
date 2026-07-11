from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from fairy_core.assistant.tools import ToolExecutor
from fairy_core.information import InformationCapabilityHealth
from fairy_core.providers import ProviderCapability, ProviderRegistry
from fairy_core.research.ports import FetchPort, SearchPort
from fairy_core.voice import VoiceRegistry

from fairy_capabilities.information.alpha_vantage import AlphaVantageAdapter
from fairy_capabilities.information.frankfurter import FrankfurterAdapter
from fairy_capabilities.information.open_meteo import OpenMeteoAdapter
from fairy_capabilities.information.timezones import TimeZoneService
from fairy_capabilities.information.tools import InformationToolExecutor
from fairy_capabilities.models.openai_compatible import OpenAICompatibleProvider
from fairy_capabilities.settings import (
    EnvironmentProviderSecretResolver,
    ProviderSettings,
)
from fairy_capabilities.voice import OpenAIAudioAdapter
from fairy_capabilities.web.brave import BraveSearchAdapter
from fairy_capabilities.web.fetch import SafeWebFetcher
from fairy_capabilities.web.tools import WebToolExecutor


@dataclass(frozen=True, slots=True)
class WebCapabilities:
    search_port: SearchPort
    fetch_port: FetchPort
    executor: WebToolExecutor


@dataclass(frozen=True, slots=True)
class InformationCapabilities:
    weather: OpenMeteoAdapter
    timezones: TimeZoneService
    fx: FrankfurterAdapter
    markets: AlphaVantageAdapter
    executor: InformationToolExecutor

    def health(self) -> tuple[InformationCapabilityHealth, ...]:
        return (
            self.weather.health(),
            self.timezones.health(),
            self.fx.health(),
            self.markets.health(),
        )


@dataclass(frozen=True, slots=True)
class CapabilityBundle:
    web: WebCapabilities
    information: InformationCapabilities

    @property
    def executor(self) -> InformationToolExecutor:
        return self.information.executor


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


def build_voice_registry(
    environment: Mapping[str, str] | None = None,
) -> VoiceRegistry:
    configured = os.environ if environment is None else environment
    settings = ProviderSettings.from_environment(configured)
    resolver = EnvironmentProviderSecretResolver(
        settings.secret_environment_names,
        configured,
    )
    providers: list[OpenAIAudioAdapter] = []
    try:
        for profile in settings.profiles:
            if not profile.capabilities & frozenset(
                {ProviderCapability.STT, ProviderCapability.TTS}
            ):
                continue
            providers.append(
                OpenAIAudioAdapter(
                    profile=profile,
                    secret=resolver.try_resolve(profile.credential_ref),
                )
            )
        return VoiceRegistry(providers)
    except BaseException:
        for provider in providers:
            provider.close()
        raise


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
        "FAIRY_PROVIDER_BRAVE_CREDENTIAL_REF",
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
    return build_capability_bundle(environment).executor


def build_information_capabilities(
    environment: Mapping[str, str] | None = None,
    *,
    news_search: SearchPort,
    delegate: ToolExecutor,
) -> InformationCapabilities:
    configured = os.environ if environment is None else environment
    settings = ProviderSettings.from_environment(configured)
    resolver = EnvironmentProviderSecretResolver(
        settings.secret_environment_names,
        configured,
    )
    credential_ref = configured.get(
        "FAIRY_PROVIDER_ALPHA_VANTAGE_CREDENTIAL_REF",
        "alpha_vantage",
    ).strip()
    weather = OpenMeteoAdapter()
    timezones = TimeZoneService()
    fx = FrankfurterAdapter()
    markets = AlphaVantageAdapter(
        secret=resolver.try_resolve(credential_ref or None),
    )
    executor = InformationToolExecutor(
        weather=weather,
        timezones=timezones,
        fx=fx,
        markets=markets,
        news_search=news_search,
        delegate=delegate,
    )
    return InformationCapabilities(
        weather=weather,
        timezones=timezones,
        fx=fx,
        markets=markets,
        executor=executor,
    )


def build_capability_bundle(
    environment: Mapping[str, str] | None = None,
) -> CapabilityBundle:
    web = build_web_capabilities(environment)
    try:
        information = build_information_capabilities(
            environment,
            news_search=web.search_port,
            delegate=web.executor,
        )
    except BaseException:
        web.executor.close()
        raise
    return CapabilityBundle(web=web, information=information)
