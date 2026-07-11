from __future__ import annotations

import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import pytest

from fairy_core.runtime.http_review import BrowserProcessResult, HttpRuntimeReviewer
from fairy_core.runtime.models import RuntimeExecutorError
from fairy_core.runtime.review import RuntimeReviewRequest


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"<h1>ready</h1>")

    def log_message(self, _format: str, *args: object) -> None:
        del args


class RecordingBrowserRunner:
    def __init__(self) -> None:
        self.argv: tuple[str, ...] | None = None
        self.environment: dict[str, str] | None = None
        self.shell: bool | None = None
        self.creation_flags: int | None = None

    def run(
        self,
        argv: tuple[str, ...],
        *,
        timeout_seconds: float,
        environment: dict[str, str],
        shell: bool,
        creation_flags: int,
    ) -> BrowserProcessResult:
        assert timeout_seconds == 20
        screenshot_argument = next(
            argument for argument in argv if argument.startswith("--screenshot=")
        )
        screenshot = Path(screenshot_argument.removeprefix("--screenshot="))
        screenshot.write_bytes(
            b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + struct.pack(">II", 1280, 720)
        )
        self.argv = argv
        self.environment = environment
        self.shell = shell
        self.creation_flags = creation_flags
        return BrowserProcessResult(returncode=0, stdout=b"", stderr=b"")


def test_http_runtime_review_is_bounded_and_does_not_follow_redirects() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        reviewer = HttpRuntimeReviewer()
        result = reviewer.check(_request(f"http://127.0.0.1:{server.server_port}/"))

        assert result.status_code == 200
        assert result.body_bytes == len(b"<h1>ready</h1>")
        assert result.body_sha256

        with pytest.raises(RuntimeExecutorError) as captured:
            reviewer.check(_request(f"http://127.0.0.1:{server.server_port}/redirect"))
        assert captured.value.error_code == "REVIEW_FAILED"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_cloud_runtime_review_requires_configured_preview_suffix() -> None:
    request = _request(
        "https://attacker.invalid/",
        execution_target="cloud",
    )

    with pytest.raises(RuntimeExecutorError) as captured:
        HttpRuntimeReviewer(cloud_host_suffix="preview.fairy.test").check(request)

    assert captured.value.error_code == "SCOPE_MISMATCH"


def test_browser_review_uses_fixed_isolated_command_and_scrubbed_environment(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "msedge.exe"
    executable.touch()
    scratch = tmp_path / "browser"
    runner = RecordingBrowserRunner()
    reviewer = HttpRuntimeReviewer(
        browser_executable=executable,
        browser_scratch_root=scratch,
        browser_runner=runner,
        host_environment={
            "SYSTEMROOT": r"C:\Windows",
            "OPENROUTER_API_KEY": "must-not-leak",
        },
    )
    request = _request("http://127.0.0.1:43127/")

    capture = reviewer.capture(request)

    assert reviewer.health().browser_available is True
    assert capture.width == 1280
    assert capture.height == 720
    assert runner.argv is not None
    assert runner.argv[0] == str(executable.resolve())
    assert runner.argv[-1] == request.url
    assert "--headless=new" in runner.argv
    assert "--proxy-server=http://127.0.0.1:9" in runner.argv
    assert "--proxy-bypass-list=127.0.0.1" in runner.argv
    assert "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1" in runner.argv
    assert any(argument.startswith("--user-data-dir=") for argument in runner.argv)
    assert runner.environment == {"SYSTEMROOT": r"C:\Windows"}
    assert runner.shell is False
    assert isinstance(runner.creation_flags, int)


def _request(url: str, *, execution_target: str = "local") -> RuntimeReviewRequest:
    return RuntimeReviewRequest(
        project_id=uuid4(),
        conversation_id=uuid4(),
        task_id=uuid4(),
        version_id=uuid4(),
        runtime_id=uuid4(),
        preview_id=uuid4(),
        preview_manifest_id=uuid4(),
        workspace_generation=1,
        scope_digest="a" * 64,
        execution_target=execution_target,
        url=url,
    )
