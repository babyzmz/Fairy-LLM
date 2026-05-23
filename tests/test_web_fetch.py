from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.tools.browser import web_fetch


class _FakeResponse:
    def __init__(
        self,
        url: str,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
        encoding: str | None = "utf-8",
    ) -> None:
        self.url = url
        self.status_code = status_code
        self.headers = headers or {}
        self.content = body
        self.encoding = encoding

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise web_fetch.requests.HTTPError(f"http {self.status_code}")


class WebFetchTests(unittest.TestCase):
    def setUp(self) -> None:
        web_fetch.clear_web_fetch_cache()

    def tearDown(self) -> None:
        web_fetch.clear_web_fetch_cache()

    def test_validate_fetch_url_rejects_credentialed_urls(self) -> None:
        valid, reason, normalized = web_fetch.validate_fetch_url("https://user:pass@example.com/docs")
        self.assertFalse(valid)
        self.assertEqual(reason, "credentialed_url_blocked")
        self.assertEqual(normalized, "https://user:pass@example.com/docs")

    def test_is_permitted_redirect_allows_www_variant_only(self) -> None:
        self.assertTrue(web_fetch.is_permitted_redirect("https://example.com/docs", "https://www.example.com/docs"))
        self.assertFalse(web_fetch.is_permitted_redirect("https://example.com/docs", "https://evil.example/docs"))
        self.assertFalse(web_fetch.is_permitted_redirect("https://example.com/docs", "http://example.com/docs"))

    @patch("app.tools.browser.web_fetch.requests.get")
    def test_fetch_http_resource_blocks_cross_host_redirect(self, mock_get) -> None:
        mock_get.return_value = _FakeResponse(
            "https://example.com/start",
            status_code=302,
            headers={"location": "https://evil.example/landing"},
        )

        result = web_fetch.fetch_http_resource("https://example.com/start")

        self.assertEqual(result.blocked_reason, "redirect_blocked")
        self.assertEqual(result.redirect_url, "https://evil.example/landing")
        self.assertEqual(result.redirect_status_code, 302)
        self.assertEqual(result.final_url, "https://example.com/start")

    @patch("app.tools.browser.web_fetch.requests.get")
    def test_fetch_http_resource_hits_memory_cache(self, mock_get) -> None:
        mock_get.return_value = _FakeResponse(
            "https://example.com/article",
            headers={"content-type": "text/html; charset=utf-8"},
            body=(
                b"<html><head><title>Example</title><meta name='description' content='Summary'></head>"
                b"<body><main><p>Hello world</p></main></body></html>"
            ),
        )

        first = web_fetch.fetch_http_resource("https://example.com/article")
        second = web_fetch.fetch_http_resource("https://example.com/article")

        self.assertFalse(first.cache_hit)
        self.assertTrue(second.cache_hit)
        self.assertEqual(mock_get.call_count, 1)
        self.assertIn("Hello world", first.text_content)
        self.assertEqual(first.final_url, "https://example.com/article")

    @patch("app.tools.browser.web_fetch.extract_persisted_file_text")
    @patch("app.tools.browser.web_fetch.requests.get")
    def test_fetch_http_resource_persists_binary_content(self, mock_get, mock_extract_text) -> None:
        raw_pdf = b"%PDF-1.7 fake"
        mock_get.return_value = _FakeResponse(
            "https://example.com/report.pdf",
            headers={"content-type": "application/pdf"},
            body=raw_pdf,
        )

        with TemporaryDirectory() as temp_dir:
            mock_extract_text.side_effect = lambda path: f"decoded:{path.name}"
            with patch.object(web_fetch, "_BINARY_DOWNLOAD_DIR", Path(temp_dir)):
                result = web_fetch.fetch_http_resource("https://example.com/report.pdf")
                persisted = Path(result.persisted_path)
                self.assertTrue(persisted.exists())
                self.assertEqual(persisted.suffix, ".pdf")
                self.assertEqual(persisted.read_bytes(), raw_pdf)
                self.assertEqual(result.text_content, f"decoded:{persisted.name}")
                self.assertEqual(result.persisted_size, len(raw_pdf))
                self.assertEqual(result.content_type, "application/pdf")


if __name__ == "__main__":
    unittest.main()
