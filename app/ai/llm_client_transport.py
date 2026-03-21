from __future__ import annotations

import json
from typing import Any, Callable, Dict, Iterable, List

import requests


Message = Dict[str, Any]


class ChatTransport:
    def __init__(
        self,
        config: Any,
        build_headers: Callable[[], Dict[str, str]],
        normalize_response: Callable[[requests.Response], requests.Response],
    ) -> None:
        self.config = config
        self.build_headers = build_headers
        self.normalize_response = normalize_response

    def post_chat_completion(
        self,
        messages: List[Message],
        max_tokens: int,
        temperature: float,
    ) -> requests.Response:
        url = self.config.api_base.rstrip("/") + self.config.api_path
        payload: Dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        resp = requests.post(
            url,
            json=payload,
            headers=self.build_headers(),
            timeout=self.config.request_timeout_sec,
        )
        return self.normalize_response(resp)

    def post_chat_completion_stream(
        self,
        messages: List[Message],
        max_tokens: int,
        temperature: float,
    ) -> requests.Response:
        url = self.config.api_base.rstrip("/") + self.config.api_path
        payload: Dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        resp = requests.post(
            url,
            json=payload,
            headers=self.build_headers(),
            timeout=self.config.request_timeout_sec,
            stream=True,
        )
        return self.normalize_response(resp)

    def iter_stream_text(self, resp: requests.Response) -> Iterable[str]:
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
            choices = data.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            content = delta.get("content")
            if content:
                yield str(content)
