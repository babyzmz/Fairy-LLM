from __future__ import annotations

import hashlib
import secrets
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote, urlsplit
from uuid import UUID

MAX_SESSION_SECONDS = 300


@dataclass(frozen=True, slots=True)
class FileReadSession:
    session_id: UUID
    workspace_id: UUID
    version_id: UUID
    path: str
    content_hash: str
    byte_length: int
    media_type: str
    url: str
    expires_at: datetime


@dataclass(slots=True)
class _SessionBinding:
    token_hash: bytes
    source: BinaryIO
    content_hash: str
    byte_length: int
    media_type: str
    expires_at: datetime
    read_lock: threading.Lock


class LoopbackFileReadServer:
    """Development fallback for the Rust read-session server."""

    def __init__(self) -> None:
        self._bindings: dict[str, _SessionBinding] = {}
        self._lock = threading.RLock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                owner._serve(self, include_body=True)

            def do_HEAD(self) -> None:
                owner._serve(self, include_body=False)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="fairy-file-read-server",
            daemon=True,
        )
        self._thread.start()

    def open(
        self,
        *,
        session_id: UUID,
        workspace_id: UUID,
        version_id: UUID,
        path: str,
        source: Path,
        content_hash: str,
        byte_length: int,
        media_type: str,
        expires_seconds: int = 120,
    ) -> FileReadSession:
        if not 1 <= expires_seconds <= MAX_SESSION_SECONDS:
            raise ValueError("read session expiry is outside the allowed range")
        source = source.resolve(strict=True)
        if not source.is_file() or source.stat().st_size != byte_length:
            raise ValueError("read session source does not match the indexed file")
        source_handle = source.open("rb")
        digest = hashlib.sha256()
        while chunk := source_handle.read(1024 * 1024):
            digest.update(chunk)
        if digest.hexdigest() != content_hash:
            source_handle.close()
            raise ValueError("read session source digest does not match the indexed file")
        source_handle.seek(0)
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + timedelta(seconds=expires_seconds)
        binding = _SessionBinding(
            token_hash=hashlib.sha256(token.encode("ascii")).digest(),
            source=source_handle,
            content_hash=content_hash,
            byte_length=byte_length,
            media_type=media_type,
            expires_at=expires_at,
            read_lock=threading.Lock(),
        )
        key = str(session_id)
        with self._lock:
            for expired_key, expired in tuple(self._bindings.items()):
                if datetime.now(UTC) >= expired.expires_at:
                    expired.source.close()
                    self._bindings.pop(expired_key, None)
            previous = self._bindings.pop(key, None)
            if previous is not None:
                previous.source.close()
            self._bindings[key] = binding
        host, port = self._server.server_address
        url = f"http://{host}:{port}/read/{key}/{quote(token, safe='')}"
        return FileReadSession(
            session_id=session_id,
            workspace_id=workspace_id,
            version_id=version_id,
            path=path,
            content_hash=content_hash,
            byte_length=byte_length,
            media_type=media_type,
            url=url,
            expires_at=expires_at,
        )

    def revoke(self, session_id: UUID) -> None:
        with self._lock:
            binding = self._bindings.pop(str(session_id), None)
        if binding is not None:
            binding.source.close()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)
        with self._lock:
            for binding in self._bindings.values():
                binding.source.close()
            self._bindings.clear()

    def _serve(self, request: BaseHTTPRequestHandler, *, include_body: bool) -> None:
        parts = urlsplit(request.path)
        segments = parts.path.split("/")
        if len(segments) != 4 or segments[1] != "read" or parts.query or parts.fragment:
            request.send_error(HTTPStatus.NOT_FOUND)
            return
        session_id, token = segments[2], segments[3]
        with self._lock:
            binding = self._bindings.get(session_id)
        token_hash = hashlib.sha256(token.encode("ascii", errors="ignore")).digest()
        if (
            binding is None
            or not secrets.compare_digest(binding.token_hash, token_hash)
            or datetime.now(UTC) >= binding.expires_at
        ):
            if binding is not None and datetime.now(UTC) >= binding.expires_at:
                with self._lock:
                    expired = self._bindings.pop(session_id, None)
                if expired is not None:
                    expired.source.close()
            request.send_error(HTTPStatus.GONE)
            return
        try:
            start, end = _parse_range(request.headers.get("Range"), binding.byte_length)
        except ValueError:
            request.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            request.send_header("Content-Range", f"bytes */{binding.byte_length}")
            request.end_headers()
            return
        status = HTTPStatus.PARTIAL_CONTENT if request.headers.get("Range") else HTTPStatus.OK
        length = end - start + 1
        request.send_response(status)
        request.send_header("Accept-Ranges", "bytes")
        request.send_header("Content-Type", binding.media_type)
        request.send_header("Content-Length", str(length))
        request.send_header("Cache-Control", "private, no-store")
        request.send_header("X-Content-Type-Options", "nosniff")
        request.send_header("Referrer-Policy", "no-referrer")
        if status is HTTPStatus.PARTIAL_CONTENT:
            request.send_header("Content-Range", f"bytes {start}-{end}/{binding.byte_length}")
        request.end_headers()
        if include_body:
            with binding.read_lock:
                binding.source.seek(start)
                remaining = length
                while remaining:
                    chunk = binding.source.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    request.wfile.write(chunk)
                    remaining -= len(chunk)


def _parse_range(value: str | None, byte_length: int) -> tuple[int, int]:
    if byte_length <= 0:
        return 0, -1
    if value is None:
        return 0, byte_length - 1
    if not value.startswith("bytes=") or "," in value:
        raise ValueError("only one byte range is supported")
    bounds = value[6:].split("-", 1)
    if len(bounds) != 2 or not bounds[0].isdigit():
        raise ValueError("byte range start is invalid")
    start = int(bounds[0])
    end = int(bounds[1]) if bounds[1].isdigit() else byte_length - 1
    if start > end or end >= byte_length:
        raise ValueError("byte range is outside the file")
    return start, end


__all__ = ["MAX_SESSION_SECONDS", "FileReadSession", "LoopbackFileReadServer"]
