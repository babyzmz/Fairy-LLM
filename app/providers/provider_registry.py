from __future__ import annotations

from dataclasses import replace

from app.providers.provider_schema import ProviderConfig


class ProviderRegistry:
    def __init__(self) -> None:
        self._presets = {
            "doubao_seed2": ProviderConfig(
                provider_id="doubao_seed2",
                provider_type="cloud",
                display_name="豆包 Seed 2.0",
                base_url="https://ark.cn-beijing.volces.com/api/v3",
                model="doubao-seed-2-0-pro-260215",
                api_key_ref="ARK_API_KEY",
                api_key_env_name="ARK_API_KEY",
                api_style="responses",
                stream=True,
                timeout_seconds=45,
                max_retries=1,
                supports_tools=True,
                supports_vision=True,
                supports_reasoning=True,
            ),
            "qwen_dashscope": ProviderConfig(
                provider_id="qwen_dashscope",
                provider_type="cloud",
                display_name="Qwen (DashScope)",
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                model="qwen-plus",
                api_key_ref="DASHSCOPE_API_KEY",
                api_key_env_name="DASHSCOPE_API_KEY",
                api_style="chat_completions",
                stream=True,
                timeout_seconds=45,
                max_retries=1,
                supports_tools=True,
                supports_vision=True,
                supports_reasoning=True,
            ),
            "openai_compatible_custom": ProviderConfig(
                provider_id="openai_compatible_custom",
                provider_type="cloud",
                display_name="OpenAI-compatible Custom",
                base_url="",
                model="",
                api_key_ref="OPENAI_COMPATIBLE_CUSTOM_API_KEY",
                api_key_env_name="OPENAI_COMPATIBLE_CUSTOM_API_KEY",
                api_style="chat_completions",
                stream=True,
                timeout_seconds=45,
                max_retries=1,
                supports_tools=True,
                supports_vision=False,
                supports_reasoning=True,
            ),
        }

    def list_presets(self) -> list[ProviderConfig]:
        return [replace(item) for item in self._presets.values()]

    def get_preset(self, provider_id: str) -> ProviderConfig:
        preset = self._presets.get(provider_id)
        if preset is None:
            raise KeyError(f"Unknown provider preset: {provider_id}")
        return replace(preset)

    def ensure_runtime_config(self, payload: dict[str, object] | None, provider_id: str) -> ProviderConfig:
        config = self.get_preset(provider_id)
        data = dict(payload or {})
        for field_name in (
            "provider_type",
            "display_name",
            "base_url",
            "model",
            "api_key_ref",
            "api_key_env_name",
            "api_style",
            "stream",
            "timeout_seconds",
            "max_retries",
            "enabled",
            "supports_tools",
            "supports_vision",
            "supports_reasoning",
            "extra_headers",
            "extra_body",
        ):
            if field_name not in data:
                continue
            setattr(config, field_name, data[field_name])

        config.stream = bool(config.stream)
        config.enabled = bool(config.enabled)
        config.timeout_seconds = max(5, int(config.timeout_seconds))
        config.max_retries = max(0, int(config.max_retries))
        config.extra_headers = dict(config.extra_headers or {})
        config.extra_body = dict(config.extra_body or {})
        return config
