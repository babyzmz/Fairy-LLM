from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit
from uuid import UUID

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MAX_SCREENSHOT_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class RuntimeReviewExecutorHealth:
    executor: str
    version: str | None
    http_available: bool
    browser_available: bool
    diagnostics: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.executor.strip():
            raise ValueError("Runtime Review executor is required")
        if self.browser_available and not self.http_available:
            raise ValueError("browser Review requires HTTP Review")


@dataclass(frozen=True, slots=True)
class RuntimeReviewRequest:
    project_id: UUID
    conversation_id: UUID
    task_id: UUID
    version_id: UUID
    runtime_id: UUID
    preview_id: UUID
    preview_manifest_id: UUID
    workspace_generation: int
    scope_digest: str
    execution_target: str
    url: str

    def __post_init__(self) -> None:
        if self.workspace_generation < 1:
            raise ValueError("Runtime Review generation must be positive")
        if _DIGEST.fullmatch(self.scope_digest) is None:
            raise ValueError("Runtime Review Scope digest is invalid")
        if self.execution_target not in {"local", "cloud"}:
            raise ValueError("Runtime Review execution target is invalid")
        parsed = urlsplit(self.url)
        if (
            parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Runtime Review URL is invalid")
        if self.execution_target == "local" and (
            parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port is None
        ):
            raise ValueError("local Runtime Review requires loopback HTTP")
        if self.execution_target == "cloud" and (
            parsed.scheme != "https" or parsed.hostname is None
        ):
            raise ValueError("cloud Runtime Review requires HTTPS")


@dataclass(frozen=True, slots=True)
class RuntimeHealthCheck:
    status_code: int
    latency_ms: int
    content_type: str
    body_sha256: str
    body_bytes: int

    def __post_init__(self) -> None:
        if not 100 <= self.status_code <= 599:
            raise ValueError("Runtime health status code is invalid")
        if self.latency_ms < 0 or self.body_bytes < 0:
            raise ValueError("Runtime health metrics cannot be negative")
        if not self.content_type.strip() or _DIGEST.fullmatch(self.body_sha256) is None:
            raise ValueError("Runtime health response evidence is invalid")


@dataclass(frozen=True, slots=True)
class BrowserCapture:
    png: bytes
    width: int
    height: int
    device_scale_factor: float

    def __post_init__(self) -> None:
        content = bytes(self.png)
        object.__setattr__(self, "png", content)
        if not content.startswith(b"\x89PNG\r\n\x1a\n") or len(content) > _MAX_SCREENSHOT_BYTES:
            raise ValueError("browser Review requires a bounded PNG")
        if not 1 <= self.width <= 7_680 or not 1 <= self.height <= 4_320:
            raise ValueError("browser Review viewport is invalid")
        if not 0.5 <= self.device_scale_factor <= 4:
            raise ValueError("browser Review device scale factor is invalid")


@dataclass(frozen=True, slots=True)
class StoredRuntimeEvidence:
    storage_location: str
    content_hash: str
    byte_length: int

    @classmethod
    def create(
        cls,
        *,
        storage_location: str,
        content: bytes,
    ) -> StoredRuntimeEvidence:
        normalized = storage_location.strip()
        if not normalized:
            raise ValueError("Runtime evidence storage location is required")
        payload = bytes(content)
        return cls(
            storage_location=normalized,
            content_hash=hashlib.sha256(payload).hexdigest(),
            byte_length=len(payload),
        )


class RuntimeReviewer(Protocol):
    def health(self) -> RuntimeReviewExecutorHealth: ...

    def check(self, request: RuntimeReviewRequest) -> RuntimeHealthCheck: ...

    def capture(self, request: RuntimeReviewRequest) -> BrowserCapture: ...


class RuntimeEvidenceStore(Protocol):
    def put(self, *, content: bytes, media_type: str) -> StoredRuntimeEvidence: ...


__all__ = [
    "BrowserCapture",
    "RuntimeEvidenceStore",
    "RuntimeHealthCheck",
    "RuntimeReviewExecutorHealth",
    "RuntimeReviewRequest",
    "RuntimeReviewer",
    "StoredRuntimeEvidence",
]
