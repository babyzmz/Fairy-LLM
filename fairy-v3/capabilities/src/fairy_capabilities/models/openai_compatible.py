from __future__ import annotations

import base64
import json
from collections.abc import Iterator, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

import httpx
from fairy_core.providers import (
    CancellationToken,
    ModelDelta,
    ModelRequest,
    ModelRole,
    ProviderAuthenticationError,
    ProviderContentRejectedError,
    ProviderContextLengthError,
    ProviderHealth,
    ProviderHealthStatus,
    ProviderNetworkError,
    ProviderProfile,
    ProviderProtocolError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    SecretValue,
)


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        profile: ProviderProfile,
        secret: SecretValue | None,
        client: httpx.Client | None = None,
    ) -> None:
        self.profile = profile
        self._secret = secret
        self._owns_client = client is None
        self._client = client or httpx.Client(
            trust_env=False,
            follow_redirects=False,
            timeout=profile.timeout_seconds,
        )

    @property
    def credential_configured(self) -> bool:
        return self.profile.credential_ref is None or self._secret is not None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def health(self) -> ProviderHealth:
        if not self.profile.enabled:
            return self._health(
                ProviderHealthStatus.UNAVAILABLE,
                "PROVIDER_DISABLED",
            )
        if not self.credential_configured:
            return self._health(
                ProviderHealthStatus.UNAVAILABLE,
                "CREDENTIAL_NOT_CONFIGURED",
            )
        try:
            response = self._client.get(
                f"{self.profile.base_url}/models",
                headers=self._headers(),
                timeout=self.profile.timeout_seconds,
            )
        except httpx.TimeoutException:
            return self._health(
                ProviderHealthStatus.UNAVAILABLE,
                "PROVIDER_TIMEOUT",
            )
        except httpx.HTTPError:
            return self._health(
                ProviderHealthStatus.UNAVAILABLE,
                "PROVIDER_TRANSPORT_ERROR",
            )
        if 200 <= response.status_code < 300:
            return self._health(ProviderHealthStatus.AVAILABLE, None)
        if response.status_code == 429:
            return self._health(
                ProviderHealthStatus.DEGRADED,
                "PROVIDER_RATE_LIMITED",
            )
        return self._health(
            ProviderHealthStatus.UNAVAILABLE,
            _status_error_code(response.status_code),
        )

    def stream(
        self,
        request: ModelRequest,
        cancellation: CancellationToken,
    ) -> Iterator[ModelDelta]:
        cancellation.raise_if_cancelled()
        if request.profile_id != self.profile.id:
            raise ValueError("model request profile does not match provider")
        if not self.credential_configured:
            raise ProviderUnavailableError("provider credential is not configured")
        sequence = 0
        done_emitted = False
        tool_ids: dict[int, str] = {}
        tool_names: dict[int, str] = {}
        try:
            with self._client.stream(
                "POST",
                f"{self.profile.base_url}/chat/completions",
                headers=self._headers(),
                json=self._request_payload(request),
                timeout=self.profile.timeout_seconds,
            ) as response:
                if not 200 <= response.status_code < 300:
                    raise _response_error(response)
                for line in response.iter_lines():
                    cancellation.raise_if_cancelled()
                    stripped = line.strip()
                    if not stripped or stripped.startswith(":"):
                        continue
                    if not stripped.startswith("data:"):
                        continue
                    data = stripped[5:].strip()
                    if data == "[DONE]":
                        if not done_emitted:
                            sequence += 1
                            yield ModelDelta.done(
                                profile_id=self.profile.id,
                                sequence=sequence,
                                finish_reason=None,
                            )
                        return
                    payload = _parse_frame(data)
                    if payload.get("error") is not None:
                        raise _stream_error(payload)
                    choices = payload.get("choices", ())
                    if not isinstance(choices, list):
                        raise ProviderProtocolError("provider frame choices must be a list")
                    finish_reason: str | None = None
                    for choice in choices:
                        if not isinstance(choice, dict):
                            raise ProviderProtocolError("provider frame choice must be an object")
                        delta = choice.get("delta") or {}
                        if not isinstance(delta, dict):
                            raise ProviderProtocolError("provider frame delta must be an object")
                        content = delta.get("content")
                        if content is not None:
                            if not isinstance(content, str):
                                raise ProviderProtocolError("provider text delta must be a string")
                            if content:
                                sequence += 1
                                yield ModelDelta.text(
                                    profile_id=self.profile.id,
                                    sequence=sequence,
                                    text=content,
                                )
                                cancellation.raise_if_cancelled()
                        for tool_delta in _tool_deltas(
                            delta,
                            tool_ids=tool_ids,
                            tool_names=tool_names,
                        ):
                            sequence += 1
                            yield ModelDelta.tool_call(
                                profile_id=self.profile.id,
                                sequence=sequence,
                                **tool_delta,
                            )
                            cancellation.raise_if_cancelled()
                        raw_finish = choice.get("finish_reason")
                        if raw_finish is not None:
                            if not isinstance(raw_finish, str):
                                raise ProviderProtocolError(
                                    "provider finish_reason must be a string"
                                )
                            finish_reason = raw_finish
                    usage = payload.get("usage")
                    if usage is not None:
                        if not isinstance(usage, dict):
                            raise ProviderProtocolError("provider usage must be an object")
                        sequence += 1
                        yield ModelDelta.usage_delta(
                            profile_id=self.profile.id,
                            sequence=sequence,
                            usage=_integer_usage(usage),
                            usage_cost=_usage_cost(usage.get("cost")),
                        )
                        cancellation.raise_if_cancelled()
                    if finish_reason is not None and not done_emitted:
                        sequence += 1
                        yield ModelDelta.done(
                            profile_id=self.profile.id,
                            sequence=sequence,
                            finish_reason=finish_reason,
                        )
                        cancellation.raise_if_cancelled()
                        done_emitted = True
                if not done_emitted:
                    raise ProviderProtocolError("provider stream ended before completion")
        except (ProviderProtocolError, ProviderUnavailableError):
            raise
        except httpx.TimeoutException as error:
            raise ProviderTimeoutError("provider request timed out") from error
        except httpx.HTTPError as error:
            raise ProviderNetworkError("provider transport failed") from error

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "text/event-stream, application/json",
            "Content-Type": "application/json",
            "User-Agent": "Fairy-V3/0.1",
        }
        if self._secret is not None:
            headers["Authorization"] = f"Bearer {self._secret.reveal()}"
        return headers

    def _request_payload(self, request: ModelRequest) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        for message in request.messages:
            content: str | list[dict[str, Any]] = message.content
            if message.images:
                content = [{"type": "text", "text": message.content}]
                content.extend(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": (
                                f"data:{image.media_type};base64,"
                                f"{base64.b64encode(image.data).decode('ascii')}"
                            ),
                            "detail": "auto",
                        },
                    }
                    for image in message.images
                )
            value = {"role": _openai_role(message.role), "content": content}
            if message.name is not None:
                value["name"] = message.name
            if message.tool_call_id is not None:
                value["tool_call_id"] = message.tool_call_id
            if message.tool_calls:
                value["tool_calls"] = [
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_call.name,
                            "arguments": tool_call.arguments,
                        },
                    }
                    for tool_call in message.tool_calls
                ]
            messages.append(value)
        payload: dict[str, Any] = {
            "model": self.profile.model_id,
            "messages": messages,
            "max_tokens": request.max_output_tokens,
            "stream": True,
        }
        if request.response_schema is not None:
            assert request.response_schema_name is not None
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.response_schema_name,
                    "strict": True,
                    "schema": dict(request.response_schema),
                },
            }
        if urlsplit(self.profile.base_url).hostname == "openrouter.ai":
            provider_preferences: dict[str, Any] = {
                "allow_fallbacks": True,
                "require_parameters": request.require_parameters,
                "data_collection": "deny" if request.deny_data_collection else "allow",
            }
            if request.zero_data_retention:
                provider_preferences["zdr"] = True
            payload["provider"] = provider_preferences
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": dict(tool.input_schema),
                    },
                }
                for tool in request.tools
            ]
        return payload

    def _health(
        self,
        status: ProviderHealthStatus,
        error_code: str | None,
    ) -> ProviderHealth:
        return ProviderHealth.create(
            profile_id=self.profile.id,
            status=status,
            error_code=error_code,
            diagnostics=(),
        )


def _parse_frame(value: str) -> dict[str, Any]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as error:
        raise ProviderProtocolError("provider returned malformed SSE data") from error
    if not isinstance(payload, dict):
        raise ProviderProtocolError("provider SSE data must be an object")
    return payload


def _tool_deltas(
    delta: Mapping[str, Any],
    *,
    tool_ids: dict[int, str],
    tool_names: dict[int, str],
) -> Iterator[dict[str, Any]]:
    raw_calls = delta.get("tool_calls")
    if raw_calls is None:
        return
    if not isinstance(raw_calls, list):
        raise ProviderProtocolError("provider tool_calls must be a list")
    for raw_call in raw_calls:
        if not isinstance(raw_call, dict):
            raise ProviderProtocolError("provider tool call must be an object")
        raw_index = raw_call.get("index", 0)
        if isinstance(raw_index, bool) or not isinstance(raw_index, int) or raw_index < 0:
            raise ProviderProtocolError("provider tool call index is invalid")
        raw_id = raw_call.get("id")
        if raw_id is not None:
            if not isinstance(raw_id, str) or not raw_id:
                raise ProviderProtocolError("provider tool call id is invalid")
            tool_ids[raw_index] = raw_id
        tool_call_id = tool_ids.get(raw_index)
        if tool_call_id is None:
            raise ProviderProtocolError("provider tool call fragment has no id")
        function = raw_call.get("function") or {}
        if not isinstance(function, dict):
            raise ProviderProtocolError("provider tool call function is invalid")
        raw_name = function.get("name")
        if raw_name is not None:
            if not isinstance(raw_name, str) or not raw_name:
                raise ProviderProtocolError("provider tool name is invalid")
            tool_names[raw_index] = raw_name
        arguments = function.get("arguments", "")
        if not isinstance(arguments, str):
            raise ProviderProtocolError("provider tool arguments must be text")
        yield {
            "tool_call_id": tool_call_id,
            "tool_name": tool_names.get(raw_index),
            "arguments_fragment": arguments,
        }


def _integer_usage(usage: Mapping[str, Any]) -> dict[str, int]:
    result: dict[str, int] = {}
    for key, value in usage.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            continue
        result[str(key)] = value
    return result


def _usage_cost(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return format(amount.normalize(), "f")


def _openai_role(role: ModelRole) -> str:
    return role.value


def _status_error_code(status_code: int) -> str:
    if status_code in {401, 403}:
        return "PROVIDER_AUTH_REJECTED"
    if status_code == 429:
        return "PROVIDER_RATE_LIMITED"
    if status_code >= 500:
        return "PROVIDER_UPSTREAM_ERROR"
    return "PROVIDER_REQUEST_REJECTED"


def _response_error(response: httpx.Response) -> Exception:
    code = _safe_error_code(response)
    if response.status_code in {401, 403}:
        return ProviderAuthenticationError("provider authentication failed")
    if response.status_code == 429:
        return ProviderRateLimitError("provider rate limit exceeded")
    if response.status_code in {408, 504}:
        return ProviderTimeoutError("provider request timed out")
    if code in {"context_length_exceeded", "max_tokens_exceeded"}:
        return ProviderContextLengthError("provider context length exceeded")
    if code in {"content_filter", "content_policy_violation"}:
        return ProviderContentRejectedError("provider rejected the requested content")
    if response.status_code >= 500:
        return ProviderNetworkError("provider upstream is unavailable")
    return ProviderUnavailableError(
        f"provider request failed ({_status_error_code(response.status_code)})"
    )


def _stream_error(payload: Mapping[str, Any]) -> Exception:
    raw = payload.get("error")
    code = raw.get("code") if isinstance(raw, dict) else None
    normalized = str(code).strip().lower() if code is not None else ""
    if normalized in {"context_length_exceeded", "max_tokens_exceeded"}:
        return ProviderContextLengthError("provider context length exceeded")
    if normalized in {"content_filter", "content_policy_violation"}:
        return ProviderContentRejectedError("provider rejected the requested content")
    if normalized in {"rate_limit_exceeded", "rate_limited"}:
        return ProviderRateLimitError("provider rate limit exceeded")
    return ProviderNetworkError("provider stream reported an error")


def _safe_error_code(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError):
        return ""
    raw = payload.get("error") if isinstance(payload, dict) else None
    code = raw.get("code") if isinstance(raw, dict) else None
    return str(code).strip().lower() if code is not None else ""
