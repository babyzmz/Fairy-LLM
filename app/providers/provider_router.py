from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable

from app.modes import ModeManager, build_game_mode_prompt_overlay
from app.providers.cloud_clients import DoubaoClient, OpenAICompatibleClient
from app.providers.provider_registry import ProviderRegistry
from app.providers.provider_schema import ProviderConfig, ProviderHTTPResponse, ProviderRequestError, ProviderRouteDecision
from app.settings import GameModeSettings, SecretStore, load_game_mode_settings


logger = logging.getLogger(__name__)


class ProviderRouter:
    def __init__(self) -> None:
        self.registry = ProviderRegistry()
        self.secret_store = SecretStore()
        self.mode_manager = ModeManager()
        self._game_settings = load_game_mode_settings()
        self._decision = ProviderRouteDecision(
            active_mode=self.mode_manager.get_active_mode(),
            provider_id="local_server",
            model="",
            route_type="local",
        )
        self._openai_client = OpenAICompatibleClient()
        self._doubao_client = DoubaoClient()

    def reload_settings(self) -> None:
        self._game_settings = load_game_mode_settings()
        self.mode_manager.reload_settings()

    def get_game_settings(self) -> GameModeSettings:
        return self._game_settings

    def current_route_decision(self) -> ProviderRouteDecision:
        return self._decision

    def build_game_overlay(self) -> str:
        if not self.mode_manager.is_game_mode_active():
            return ""
        return build_game_mode_prompt_overlay(self._game_settings)

    def runtime_snapshot(self) -> dict[str, object]:
        return {
            "active_mode": self.mode_manager.get_active_mode(),
            "game_mode_enabled": self._game_settings.enabled,
            "game_mode_auto": self._game_settings.auto_switch_when_game_detected,
            "global_cloud_enabled": self._game_settings.global_enabled,
            "provider_id": self._decision.provider_id,
            "provider_model": self._decision.model,
            "provider_route": self._decision.route_type,
            "provider_fallback_used": self._decision.fallback_used,
            "provider_fallback_reason": self._decision.fallback_reason,
        }

    def test_provider(self, provider: ProviderConfig) -> tuple[bool, str]:
        api_key = self._resolve_api_key(provider)
        if not api_key:
            return False, "API key is missing."
        try:
            client = self._client_for(provider)
            ok, detail = client.test_connection(provider, api_key)
            return ok, detail
        except ProviderRequestError as exc:
            return False, str(exc)
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
        stream: bool,
        has_images: bool,
        local_call: Callable[[], Any],
    ) -> Any:
        active_mode = self.mode_manager.refresh()
        if active_mode == "game_mode" or self._game_settings.global_enabled:
            return self._complete_via_cloud(
                active_mode=active_mode,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=stream,
                has_images=has_images,
                local_call=local_call,
            )

        self._decision = ProviderRouteDecision(
            active_mode=active_mode,
            provider_id="local_server",
            model="",
            route_type="local",
            stream=stream,
        )
        return local_call()

    def _complete_via_cloud(
        self,
        *,
        active_mode: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
        stream: bool,
        has_images: bool,
        local_call: Callable[[], Any],
    ) -> Any:
        provider = self._selected_provider()
        missing_reason = self._validate_provider(provider)
        if missing_reason:
            logger.warning("cloud provider incomplete, falling back to local: %s", missing_reason)
            self._decision = ProviderRouteDecision(
                active_mode=active_mode,
                provider_id=provider.provider_id,
                model=provider.model,
                route_type="local_fallback_vision" if has_images else "local_fallback",
                fallback_used=True,
                fallback_reason=missing_reason,
                stream=stream,
                warning=missing_reason,
            )
            return local_call()

        if has_images and not provider.supports_vision:
            reason = "Cloud provider does not support vision requests."
            logger.info("cloud vision fallback: %s", reason)
            self._decision = ProviderRouteDecision(
                active_mode=active_mode,
                provider_id=provider.provider_id,
                model=provider.model,
                route_type="local_fallback_vision" if has_images else "local_fallback",
                fallback_used=True,
                fallback_reason=reason,
                stream=stream,
                warning=reason,
            )
            return local_call()

        started = time.perf_counter()
        try:
            api_key = self._resolve_api_key(provider)
            response = self._client_for(provider).create_completion(
                provider,
                api_key,
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=stream and provider.stream,
            )
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            self._decision = ProviderRouteDecision(
                active_mode=active_mode,
                provider_id=provider.provider_id,
                model=provider.model,
                route_type="cloud_vision" if has_images else "cloud",
                stream=stream and provider.stream,
            )
            logger.info(
                "provider_request_done mode=%s provider=%s model=%s stream=%s elapsed_ms=%s fallback=%s",
                active_mode,
                provider.provider_id,
                provider.model,
                stream and provider.stream,
                elapsed_ms,
                False,
            )
            return response
        except ProviderRequestError as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            logger.warning(
                "provider_request_failed mode=%s provider=%s model=%s kind=%s status=%s elapsed_ms=%s",
                active_mode,
                provider.provider_id,
                provider.model,
                exc.kind,
                exc.status_code,
                elapsed_ms,
            )
            if self._should_fallback(exc.kind):
                self._decision = ProviderRouteDecision(
                    active_mode=active_mode,
                    provider_id=provider.provider_id,
                    model=provider.model,
                    route_type="local_fallback_vision" if has_images else "local_fallback",
                    fallback_used=True,
                    fallback_reason=exc.kind,
                    stream=stream,
                    warning=str(exc),
                )
                return local_call()
            self._decision = ProviderRouteDecision(
                active_mode=active_mode,
                provider_id=provider.provider_id,
                model=provider.model,
                route_type="cloud_error",
                fallback_used=False,
                fallback_reason=exc.kind,
                stream=stream,
                warning=str(exc),
            )
            return ProviderHTTPResponse.error(
                "Cloud model is currently unavailable. Retry later or enable local fallback.",
                status_code=exc.status_code or 500,
                metadata={"provider_id": provider.provider_id, "kind": exc.kind},
            )

    def _selected_provider(self) -> ProviderConfig:
        settings = self._game_settings
        provider_id = settings.selected_provider_id
        runtime_payload = {
            provider_id: settings.providers.get(provider_id, self.registry.get_preset(provider_id))
        }.get(provider_id)
        if isinstance(runtime_payload, ProviderConfig):
            return runtime_payload
        return self.registry.ensure_runtime_config(runtime_payload, provider_id)

    def _resolve_api_key(self, provider: ProviderConfig) -> str:
        if provider.api_key_ref:
            secret = self.secret_store.get(provider.api_key_ref)
            if secret:
                return "".join(secret.split()).strip()
        if provider.api_key_env_name:
            return "".join(os.getenv(provider.api_key_env_name, "").split()).strip()
        return ""

    def _validate_provider(self, provider: ProviderConfig) -> str:
        if not provider.enabled:
            return "Selected cloud provider is disabled."
        if not provider.base_url.strip():
            return "Cloud provider base URL is empty."
        if not provider.model.strip():
            return "Cloud provider model is empty."
        if not self._resolve_api_key(provider).strip():
            return "Cloud provider API key is missing."
        return ""

    def _client_for(self, provider: ProviderConfig):
        if provider.provider_id == "doubao_seed2":
            return self._doubao_client
        return self._openai_client

    def _should_fallback(self, error_kind: str) -> bool:
        fallback = self._game_settings.fallback
        if not fallback.enabled:
            return False
        if error_kind == "timeout":
            return fallback.on_timeout
        if error_kind == "auth":
            return fallback.on_auth_error
        if error_kind == "network":
            return fallback.on_network_error
        return fallback.on_provider_error
