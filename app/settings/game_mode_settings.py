from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.config import BASE_DIR
from app.providers.provider_registry import ProviderRegistry
from app.providers.provider_schema import ProviderConfig


GAME_MODE_SETTINGS_FILE = BASE_DIR / "config" / "game_mode_settings.json"


@dataclass(slots=True)
class FallbackSettings:
    enabled: bool = True
    on_timeout: bool = True
    on_auth_error: bool = False
    on_network_error: bool = True
    on_provider_error: bool = True
    local_provider: str = "local_server"
    local_model: str = "Qwen3.5-4B-Q4_K_M"


@dataclass(slots=True)
class GameModeSettings:
    enabled: bool = False
    auto_switch_when_game_detected: bool = False
    global_enabled: bool = False
    selected_provider_id: str = "doubao_seed2"
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    fallback: FallbackSettings = field(default_factory=FallbackSettings)
    system_prompt_overlay: str = ""
    persona_intensity: str = "normal"
    brevity_level: str = "short"
    verdict_strength: str = "strong"
    interruption_policy: str = "low_interrupt"
    auto_switch_debounce_seconds: int = 8
    known_game_processes: list[str] = field(
        default_factory=lambda: [
            "genshinimpact.exe",
            "starrail.exe",
            "zzz.exe",
            "wuwa.exe",
            "wowclassic.exe",
            "wow.exe",
            "league of legends.exe",
            "valorant.exe",
            "cs2.exe",
        ]
    )


def build_default_game_mode_settings() -> GameModeSettings:
    registry = ProviderRegistry()
    presets = {item.provider_id: item for item in registry.list_presets()}
    return GameModeSettings(providers=presets)


def _provider_to_dict(provider: ProviderConfig) -> dict[str, object]:
    return asdict(provider)


def _provider_from_dict(provider_id: str, payload: dict[str, object], registry: ProviderRegistry) -> ProviderConfig:
    return registry.ensure_runtime_config(payload, provider_id)


def load_game_mode_settings(path: Path = GAME_MODE_SETTINGS_FILE) -> GameModeSettings:
    defaults = build_default_game_mode_settings()
    if not path.exists():
        return defaults
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return defaults

    registry = ProviderRegistry()
    providers_payload = payload.get("providers", {}) if isinstance(payload.get("providers"), dict) else {}
    providers: dict[str, ProviderConfig] = {}
    for provider_id in {*(providers_payload.keys()), *defaults.providers.keys()}:
        if provider_id not in defaults.providers:
            continue
        providers[provider_id] = _provider_from_dict(
            provider_id,
            providers_payload.get(provider_id, {}),
            registry,
        )

    fallback_payload = payload.get("fallback", {}) if isinstance(payload.get("fallback"), dict) else {}
    fallback = FallbackSettings(
        enabled=bool(fallback_payload.get("enabled", defaults.fallback.enabled)),
        on_timeout=bool(fallback_payload.get("on_timeout", defaults.fallback.on_timeout)),
        on_auth_error=bool(fallback_payload.get("on_auth_error", defaults.fallback.on_auth_error)),
        on_network_error=bool(fallback_payload.get("on_network_error", defaults.fallback.on_network_error)),
        on_provider_error=bool(fallback_payload.get("on_provider_error", defaults.fallback.on_provider_error)),
        local_provider=str(fallback_payload.get("local_provider", defaults.fallback.local_provider)),
        local_model=str(fallback_payload.get("local_model", defaults.fallback.local_model)),
    )
    return GameModeSettings(
        enabled=bool(payload.get("enabled", defaults.enabled)),
        auto_switch_when_game_detected=bool(
            payload.get("auto_switch_when_game_detected", defaults.auto_switch_when_game_detected)
        ),
        global_enabled=bool(payload.get("global_enabled", defaults.global_enabled)),
        selected_provider_id=str(payload.get("selected_provider_id", defaults.selected_provider_id)),
        providers=providers,
        fallback=fallback,
        system_prompt_overlay=str(payload.get("system_prompt_overlay", defaults.system_prompt_overlay)),
        persona_intensity=str(payload.get("persona_intensity", defaults.persona_intensity)),
        brevity_level=str(payload.get("brevity_level", defaults.brevity_level)),
        verdict_strength=str(payload.get("verdict_strength", defaults.verdict_strength)),
        interruption_policy=str(payload.get("interruption_policy", defaults.interruption_policy)),
        auto_switch_debounce_seconds=max(
            2,
            int(payload.get("auto_switch_debounce_seconds", defaults.auto_switch_debounce_seconds)),
        ),
        known_game_processes=[
            str(item).lower()
            for item in payload.get("known_game_processes", defaults.known_game_processes)
            if str(item).strip()
        ],
    )


def save_game_mode_settings(settings: GameModeSettings, path: Path = GAME_MODE_SETTINGS_FILE) -> None:
    payload = {
        "enabled": settings.enabled,
        "auto_switch_when_game_detected": settings.auto_switch_when_game_detected,
        "global_enabled": settings.global_enabled,
        "selected_provider_id": settings.selected_provider_id,
        "providers": {provider_id: _provider_to_dict(provider) for provider_id, provider in settings.providers.items()},
        "fallback": asdict(settings.fallback),
        "system_prompt_overlay": settings.system_prompt_overlay,
        "persona_intensity": settings.persona_intensity,
        "brevity_level": settings.brevity_level,
        "verdict_strength": settings.verdict_strength,
        "interruption_policy": settings.interruption_policy,
        "auto_switch_debounce_seconds": settings.auto_switch_debounce_seconds,
        "known_game_processes": settings.known_game_processes,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
