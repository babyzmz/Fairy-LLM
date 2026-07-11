from __future__ import annotations

import hashlib
import os
import struct
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from fairy_core.runtime.models import RuntimeExecutorError
from fairy_core.runtime.review import (
    BrowserCapture,
    RuntimeHealthCheck,
    RuntimeReviewExecutorHealth,
    RuntimeReviewRequest,
)

_MAX_BODY_BYTES = 1024 * 1024
_MAX_SCREENSHOT_BYTES = 16 * 1024 * 1024
_SAFE_ENVIRONMENT_KEYS = ("SYSTEMROOT", "WINDIR", "TEMP", "TMP")


@dataclass(frozen=True, slots=True)
class BrowserProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class BrowserProcessRunner(Protocol):
    def run(
        self,
        argv: tuple[str, ...],
        *,
        timeout_seconds: float,
        environment: dict[str, str],
        shell: bool,
        creation_flags: int,
    ) -> BrowserProcessResult: ...


class SafeBrowserProcessRunner:
    def run(
        self,
        argv: tuple[str, ...],
        *,
        timeout_seconds: float,
        environment: dict[str, str],
        shell: bool,
        creation_flags: int,
    ) -> BrowserProcessResult:
        completed = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
            env=environment,
            shell=shell,
            creationflags=creation_flags,
        )
        return BrowserProcessResult(completed.returncode, completed.stdout, completed.stderr)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        return None


class HttpRuntimeReviewer:
    def __init__(
        self,
        *,
        cloud_host_suffix: str | None = None,
        browser_executable: Path | None = None,
        browser_scratch_root: Path | None = None,
        browser_runner: BrowserProcessRunner | None = None,
        host_environment: Mapping[str, str] | None = None,
    ) -> None:
        self._cloud_host_suffix = (
            cloud_host_suffix.lower().strip().strip(".") if cloud_host_suffix is not None else None
        )
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirect(),
        )
        self._browser_executable = (
            Path(browser_executable).resolve(strict=False)
            if browser_executable is not None
            else None
        )
        self._browser_scratch_root = (
            Path(browser_scratch_root).resolve(strict=False)
            if browser_scratch_root is not None
            else None
        )
        if self._browser_scratch_root is not None:
            self._browser_scratch_root.mkdir(parents=True, exist_ok=True)
        environment = dict(os.environ if host_environment is None else host_environment)
        self._browser_environment = {
            key: environment[key] for key in _SAFE_ENVIRONMENT_KEYS if key in environment
        }
        self._browser_runner = browser_runner or SafeBrowserProcessRunner()
        self._creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    def health(self) -> RuntimeReviewExecutorHealth:
        return RuntimeReviewExecutorHealth(
            executor="core_http_review",
            version="1.0.0",
            http_available=True,
            browser_available=(
                self._browser_executable is not None
                and self._browser_executable.is_file()
                and self._browser_scratch_root is not None
            ),
            diagnostics=("bounded direct HTTP probe configured",),
        )

    def check(self, request: RuntimeReviewRequest) -> RuntimeHealthCheck:
        self._validate_target(request)
        started = time.monotonic()
        http_request = urllib.request.Request(
            request.url,
            method="GET",
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "Fairy-Runtime-Review/1.0",
            },
        )
        try:
            with self._opener.open(http_request, timeout=5) as response:
                body = response.read(_MAX_BODY_BYTES + 1)
                status = int(response.status)
                content_type = response.headers.get_content_type()
        except urllib.error.HTTPError as error:
            body = error.read(_MAX_BODY_BYTES + 1)
            status = int(error.code)
            content_type = error.headers.get_content_type()
        except (OSError, urllib.error.URLError) as error:
            raise RuntimeExecutorError(
                "Runtime health probe could not reach the Preview",
                error_code="WORKER_INTERRUPTED",
            ) from error
        if len(body) > _MAX_BODY_BYTES:
            raise RuntimeExecutorError(
                "Runtime health response exceeded the evidence limit",
                error_code="WORKER_INTERRUPTED",
            )
        if not 200 <= status < 300:
            raise RuntimeExecutorError(
                f"Runtime health probe returned HTTP {status}",
                error_code="REVIEW_FAILED",
            )
        return RuntimeHealthCheck(
            status_code=status,
            latency_ms=max(0, round((time.monotonic() - started) * 1000)),
            content_type=content_type,
            body_sha256=hashlib.sha256(body).hexdigest(),
            body_bytes=len(body),
        )

    def capture(self, request: RuntimeReviewRequest) -> BrowserCapture:
        self._validate_target(request)
        if request.execution_target != "local":
            raise RuntimeExecutorError(
                "Cloud browser Review requires a dedicated browser worker",
                error_code="CAPABILITY_NOT_AVAILABLE",
            )
        if (
            self._browser_executable is None
            or not self._browser_executable.is_file()
            or self._browser_scratch_root is None
        ):
            raise RuntimeExecutorError(
                "Browser Review executor is not configured",
                error_code="CAPABILITY_NOT_AVAILABLE",
            )
        with tempfile.TemporaryDirectory(
            prefix="capture-",
            dir=self._browser_scratch_root,
        ) as temporary:
            root = Path(temporary)
            screenshot = root / "preview.png"
            profile = root / "profile"
            argv = (
                str(self._browser_executable),
                "--headless=new",
                "--disable-gpu",
                "--disable-extensions",
                "--disable-background-networking",
                "--disable-component-update",
                "--disable-default-apps",
                "--disable-sync",
                "--disable-dev-shm-usage",
                "--no-first-run",
                "--no-pings",
                "--hide-scrollbars",
                "--run-all-compositor-stages-before-draw",
                "--virtual-time-budget=3000",
                "--proxy-server=http://127.0.0.1:9",
                "--proxy-bypass-list=127.0.0.1",
                "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
                "--window-size=1280,720",
                f"--user-data-dir={profile}",
                f"--screenshot={screenshot}",
                request.url,
            )
            try:
                result = self._browser_runner.run(
                    argv,
                    timeout_seconds=20,
                    environment=dict(self._browser_environment),
                    shell=False,
                    creation_flags=self._creation_flags,
                )
            except (OSError, subprocess.SubprocessError) as error:
                raise RuntimeExecutorError(
                    "Browser Review process could not run",
                    error_code="WORKER_INTERRUPTED",
                ) from error
            if result.returncode != 0:
                raise RuntimeExecutorError(
                    "Browser Review process failed",
                    error_code="REVIEW_FAILED",
                )
            try:
                if screenshot.stat().st_size > _MAX_SCREENSHOT_BYTES:
                    raise ValueError("screenshot exceeds limit")
                png = screenshot.read_bytes()
                width, height = png_dimensions(png)
            except (OSError, ValueError) as error:
                raise RuntimeExecutorError(
                    "Browser Review returned invalid screenshot evidence",
                    error_code="REVIEW_FAILED",
                ) from error
        return BrowserCapture(
            png=png,
            width=width,
            height=height,
            device_scale_factor=1.0,
        )

    def _validate_target(self, request: RuntimeReviewRequest) -> None:
        parsed = urlsplit(request.url)
        if request.execution_target == "local":
            if parsed.hostname != "127.0.0.1":
                raise RuntimeExecutorError(
                    "Runtime health target escaped loopback",
                    error_code="SCOPE_MISMATCH",
                )
            return
        if (
            self._cloud_host_suffix is None
            or parsed.hostname is None
            or not parsed.hostname.endswith(f".{self._cloud_host_suffix}")
        ):
            raise RuntimeExecutorError(
                "Runtime health target escaped the Preview domain",
                error_code="SCOPE_MISMATCH",
            )


__all__ = ["HttpRuntimeReviewer", "png_dimensions"]


def png_dimensions(content: bytes) -> tuple[int, int]:
    if (
        len(content) < 24
        or not content.startswith(b"\x89PNG\r\n\x1a\n")
        or content[12:16] != b"IHDR"
    ):
        raise ValueError("PNG header is invalid")
    width, height = struct.unpack(">II", content[16:24])
    if width < 1 or height < 1:
        raise ValueError("PNG dimensions are invalid")
    return width, height
