from __future__ import annotations

from dataclasses import replace

from app.providers.cloud_clients.openai_compatible_client import OpenAICompatibleClient
from app.providers.provider_schema import ProviderConfig


class DoubaoClient(OpenAICompatibleClient):
    def create_completion(self, provider: ProviderConfig, api_key: str, messages, *, max_tokens: int, temperature: float, stream: bool):
        if provider.api_style != "responses":
            provider = replace(provider, api_style="responses")
        return super().create_completion(
            provider,
            api_key,
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=stream,
        )
