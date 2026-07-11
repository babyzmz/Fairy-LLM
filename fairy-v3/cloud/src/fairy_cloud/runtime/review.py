from __future__ import annotations

import json
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from fairy_core.runtime.http_review import png_dimensions
from fairy_core.runtime.models import RuntimeExecutorError
from fairy_core.runtime.review import (
    BrowserCapture,
    RuntimeHealthCheck,
    RuntimeReviewExecutorHealth,
    RuntimeReviewRequest,
)
from pydantic import BaseModel, ConfigDict, Field

_GATEWAY_HEADER = "X-Fairy-Runtime-Gateway-Key"
_MAX_JSON_BYTES = 1024 * 1024
_MAX_SCREENSHOT_BYTES = 16 * 1024 * 1024


class CloudRuntimeReviewWire(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    project_id: UUID
    conversation_id: UUID
    task_id: UUID
    version_id: UUID
    runtime_id: UUID
    preview_id: UUID
    preview_manifest_id: UUID
    workspace_generation: int = Field(ge=1)
    scope_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def from_request(cls, request: RuntimeReviewRequest) -> CloudRuntimeReviewWire:
        if request.execution_target != "cloud":
            raise RuntimeExecutorError(
                "Cloud Runtime Review request does not match cloud Scope",
                error_code="SCOPE_MISMATCH",
            )
        return cls(
            project_id=request.project_id,
            conversation_id=request.conversation_id,
            task_id=request.task_id,
            version_id=request.version_id,
            runtime_id=request.runtime_id,
            preview_id=request.preview_id,
            preview_manifest_id=request.preview_manifest_id,
            workspace_generation=request.workspace_generation,
            scope_digest=request.scope_digest,
        )

    def local_request(self, url: str) -> RuntimeReviewRequest:
        return RuntimeReviewRequest(
            project_id=self.project_id,
            conversation_id=self.conversation_id,
            task_id=self.task_id,
            version_id=self.version_id,
            runtime_id=self.runtime_id,
            preview_id=self.preview_id,
            preview_manifest_id=self.preview_manifest_id,
            workspace_generation=self.workspace_generation,
            scope_digest=self.scope_digest,
            execution_target="local",
            url=url,
        )


class CloudRuntimeReviewer:
    def __init__(
        self,
        *,
        gateway_base_url: str,
        gateway_key: str,
        client: httpx.Client | None = None,
    ) -> None:
        parsed = urlsplit(gateway_base_url.rstrip("/"))
        if (
            parsed.scheme != "http"
            or parsed.hostname != "runtime"
            or parsed.port != 8082
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Cloud Runtime Review gateway must use the private service origin")
        if len(gateway_key.encode("utf-8")) < 32:
            raise ValueError("Cloud Runtime Review gateway key must contain at least 32 bytes")
        self._base_url = gateway_base_url.rstrip("/")
        self._gateway_key = gateway_key
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(30, connect=2),
            follow_redirects=False,
        )

    def health(self) -> RuntimeReviewExecutorHealth:
        try:
            status, _headers, content = self._send(
                "GET",
                "/internal-review/health",
                content=None,
                limit=_MAX_JSON_BYTES,
            )
            values = _json_object(content)
            if status != 200 or set(values) != {
                "schema_version",
                "http_available",
                "browser_available",
                "diagnostics",
            }:
                raise ValueError("Runtime Review health schema is invalid")
            http_available = values["http_available"] is True
            browser_available = values["browser_available"] is True
            diagnostics = values["diagnostics"]
            if not isinstance(diagnostics, list) or any(
                not isinstance(value, str) for value in diagnostics
            ):
                raise ValueError("Runtime Review diagnostics are invalid")
        except (httpx.HTTPError, RuntimeExecutorError, ValueError):
            return RuntimeReviewExecutorHealth(
                executor="cloud_runtime_review",
                version=None,
                http_available=False,
                browser_available=False,
                diagnostics=("Cloud Runtime Review worker is unavailable",),
            )
        return RuntimeReviewExecutorHealth(
            executor="cloud_runtime_review",
            version="1.0.0",
            http_available=http_available,
            browser_available=browser_available,
            diagnostics=tuple(diagnostics),
        )

    def check(self, request: RuntimeReviewRequest) -> RuntimeHealthCheck:
        wire = CloudRuntimeReviewWire.from_request(request)
        status, _headers, content = self._send(
            "POST",
            f"/internal-review/{wire.runtime_id}/health",
            content=wire.model_dump_json().encode("utf-8"),
            limit=_MAX_JSON_BYTES,
        )
        if status != 200:
            raise RuntimeExecutorError(
                "Cloud Runtime health Review failed",
                error_code="WORKER_INTERRUPTED",
            )
        values = _json_object(content)
        if set(values) != {
            "status_code",
            "latency_ms",
            "content_type",
            "body_sha256",
            "body_bytes",
        }:
            raise RuntimeExecutorError(
                "Cloud Runtime health Review response is invalid",
                error_code="SCOPE_MISMATCH",
            )
        try:
            return RuntimeHealthCheck(**values)
        except (TypeError, ValueError) as error:
            raise RuntimeExecutorError(
                "Cloud Runtime health Review response is invalid",
                error_code="SCOPE_MISMATCH",
            ) from error

    def capture(self, request: RuntimeReviewRequest) -> BrowserCapture:
        wire = CloudRuntimeReviewWire.from_request(request)
        status, headers, content = self._send(
            "POST",
            f"/internal-review/{wire.runtime_id}/capture",
            content=wire.model_dump_json().encode("utf-8"),
            limit=_MAX_SCREENSHOT_BYTES,
        )
        if status != 200 or headers.get("content-type", "").split(";", 1)[0] != "image/png":
            raise RuntimeExecutorError(
                "Cloud browser Review failed",
                error_code="WORKER_INTERRUPTED",
            )
        try:
            width = int(headers["x-fairy-image-width"])
            height = int(headers["x-fairy-image-height"])
            if png_dimensions(content) != (width, height):
                raise ValueError("PNG dimensions do not match headers")
            return BrowserCapture(
                png=content,
                width=width,
                height=height,
                device_scale_factor=1.0,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeExecutorError(
                "Cloud browser Review response is invalid",
                error_code="SCOPE_MISMATCH",
            ) from error

    def _send(
        self,
        method: str,
        path: str,
        *,
        content: bytes | None,
        limit: int,
    ) -> tuple[int, httpx.Headers, bytes]:
        headers = {_GATEWAY_HEADER: self._gateway_key}
        if content is not None:
            headers["Content-Type"] = "application/json"
        request = self._client.build_request(
            method,
            f"{self._base_url}{path}",
            headers=headers,
            content=content,
        )
        try:
            response = self._client.send(request, stream=True)
            payload = bytearray()
            try:
                for chunk in response.iter_bytes():
                    if len(payload) + len(chunk) > limit:
                        raise RuntimeExecutorError(
                            "Cloud Runtime Review response exceeded its limit",
                            error_code="WORKER_INTERRUPTED",
                        )
                    payload.extend(chunk)
                return response.status_code, response.headers, bytes(payload)
            finally:
                response.close()
        except httpx.HTTPError as error:
            raise RuntimeExecutorError(
                "Cloud Runtime Review worker could not be reached",
                error_code="WORKER_INTERRUPTED",
            ) from error


def _json_object(content: bytes) -> dict[str, object]:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Cloud Runtime Review response is invalid JSON") from error
    if not isinstance(value, dict):
        raise ValueError("Cloud Runtime Review response must be an object")
    return value


__all__ = ["CloudRuntimeReviewWire", "CloudRuntimeReviewer"]
