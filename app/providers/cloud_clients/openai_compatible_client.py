from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

import requests

from app.providers.provider_schema import ProviderConfig, ProviderHTTPResponse, ProviderRequestError


class OpenAICompatibleClient:
    def create_completion(
        self,
        provider: ProviderConfig,
        api_key: str,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int,
        temperature: float,
        stream: bool,
    ) -> ProviderHTTPResponse:
        api_key = "".join(str(api_key or "").split()).strip()
        headers = {"Content-Type": "application/json", **provider.extra_headers}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        base_url = provider.base_url.rstrip("/")
        if not base_url:
            raise ProviderRequestError("config", "Cloud provider base URL is empty.")

        if provider.api_style == "responses":
            url = base_url + "/responses"
            payload: dict[str, Any] = {
                "model": provider.model,
                "input": self._convert_messages_for_responses(messages),
                "temperature": temperature,
                "max_output_tokens": max_tokens,
                "stream": stream,
            }
        else:
            url = base_url + "/chat/completions"
            payload = {
                "model": provider.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": stream,
            }
        payload.update(provider.extra_body or {})

        try:
            resp = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=provider.timeout_seconds,
                stream=stream,
            )
        except requests.exceptions.Timeout as exc:
            raise ProviderRequestError("timeout", "Cloud provider request timed out.") from exc
        except requests.exceptions.ConnectionError as exc:
            raise ProviderRequestError("network", "Cloud provider network connection failed.") from exc
        except requests.exceptions.InvalidHeader as exc:
            raise ProviderRequestError("config", "API Key 包含非法空白字符，请重新粘贴并保存。") from exc
        except requests.RequestException as exc:
            raise ProviderRequestError("provider", f"Cloud provider request failed: {exc}") from exc

        if resp.status_code in {401, 403}:
            raise ProviderRequestError("auth", "Cloud provider authentication failed.", status_code=resp.status_code)
        if not resp.ok:
            raise ProviderRequestError(
                "provider",
                self._extract_error_message(resp),
                status_code=resp.status_code,
            )

        if stream:
            return ProviderHTTPResponse.success(
                text="",
                json_data=None,
                status_code=resp.status_code,
                metadata={"provider_id": provider.provider_id, "model": provider.model, "api_style": provider.api_style},
                text_iter_factory=lambda: self._iter_stream_text(resp, provider.api_style),
            )

        data = self._safe_json(resp)
        text = self._extract_text(data, provider.api_style)
        return ProviderHTTPResponse.success(
            text=text,
            json_data=data,
            status_code=resp.status_code,
            metadata={"provider_id": provider.provider_id, "model": provider.model, "api_style": provider.api_style},
        )

    def test_connection(self, provider: ProviderConfig, api_key: str) -> tuple[bool, str]:
        response = self.create_completion(
            provider,
            api_key,
            self._build_test_messages(provider),
            max_tokens=20,
            temperature=0.0,
            stream=False,
        )
        text = response.text.strip()
        if text:
            return True, self._normalize_test_result(text, provider.model)
        raw = response.json_data or {}
        returned_model = str(raw.get("model", "") or response.metadata.get("model", "")).strip()
        if returned_model:
            return True, self._normalize_test_result(returned_model, provider.model)
        return False, "Provider returned an empty response."

    def _normalize_test_result(self, text: str, fallback_model: str) -> str:
        cleaned = " ".join(str(text or "").split()).strip().strip("`\"'")
        if not cleaned:
            return fallback_model
        if len(cleaned) > 80:
            cleaned = cleaned.splitlines()[0].strip()
        if " " in cleaned and fallback_model and fallback_model in cleaned:
            return fallback_model
        return cleaned

    def _build_test_messages(self, provider: ProviderConfig) -> list[dict[str, Any]]:
        if provider.api_style == "responses":
            return [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "请只回答你当前使用的模型名称，不要添加解释。",
                        }
                    ],
                }
            ]
        return [
            {
                "role": "user",
                "content": "请只回答你当前使用的模型名称，不要添加解释。",
            }
        ]

    def _convert_messages_for_responses(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for message in messages:
            role = str(message.get("role", "user") or "user")
            content = message.get("content", "")
            converted.append(
                {
                    "role": role,
                    "content": self._convert_content_blocks(content),
                }
            )
        return converted

    def _convert_content_blocks(self, content: Any) -> list[dict[str, Any]]:
        if isinstance(content, str):
            text = content.strip()
            return [{"type": "input_text", "text": text}] if text else []

        converted: list[dict[str, Any]] = []
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    text = str(block).strip()
                    if text:
                        converted.append({"type": "input_text", "text": text})
                    continue
                block_type = str(block.get("type", "") or "")
                if block_type == "text":
                    text = str(block.get("text", "") or "").strip()
                    if text:
                        converted.append({"type": "input_text", "text": text})
                    continue
                if block_type == "image_url":
                    image_url = block.get("image_url")
                    if isinstance(image_url, dict):
                        url = str(image_url.get("url", "") or "").strip()
                    else:
                        url = str(image_url or "").strip()
                    if url:
                        converted.append({"type": "input_image", "image_url": url})
                    continue
                text = str(block.get("text", "") or "").strip()
                if text:
                    converted.append({"type": "input_text", "text": text})
        return converted

    def _extract_error_message(self, resp: requests.Response) -> str:
        try:
            data = resp.json()
        except Exception:
            return f"Cloud provider HTTP {resp.status_code}: {resp.text[:300]}"
        if isinstance(data, dict):
            error = data.get("error")
            if isinstance(error, dict) and error.get("message"):
                return str(error["message"])
            if data.get("message"):
                return str(data["message"])
        return f"Cloud provider HTTP {resp.status_code}"

    def _safe_json(self, resp: requests.Response) -> dict[str, Any]:
        try:
            return resp.json()
        except Exception:
            return {}

    def _extract_text(self, data: dict[str, Any], api_style: str) -> str:
        if api_style == "responses":
            output_text = data.get("output_text")
            if output_text:
                return str(output_text).strip()
            output = data.get("output") or []
            for item in output:
                if not isinstance(item, dict):
                    continue
                for content in item.get("content") or []:
                    if not isinstance(content, dict):
                        continue
                    text = content.get("text")
                    if text:
                        return str(text).strip()
            return ""
        choices = data.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        content = message.get("content", "")
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                    parts.append(str(block["text"]))
            return "\n".join(parts).strip()
        return str(content).strip()

    def _iter_stream_text(self, resp: requests.Response, api_style: str) -> Iterable[str]:
        for raw_line in resp.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if api_style == "responses":
                delta = data.get("delta")
                if isinstance(delta, str) and delta:
                    yield delta
                    continue
                output = data.get("output") or []
                for item in output:
                    if not isinstance(item, dict):
                        continue
                    for content in item.get("content") or []:
                        if not isinstance(content, dict):
                            continue
                        text = content.get("text")
                        if text:
                            yield str(text)
            else:
                choices = data.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                if content:
                    yield str(content)
