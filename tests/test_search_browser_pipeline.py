from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.capabilities.browser_capability import BrowserCapability
from app.config import apply_cli_overrides, llm_config
from app.fairy_core import FairySkillLLMHelper
from app.skills.bundles.web_research.tool_loop import ToolLoopWebResearchRunner
from app.tools.browser.http_page_loader import CrawledPage
from app.tools.search import html_search
from app.tools.web import research_pipeline


class _FakeSearchResponse:
    def __init__(self, url: str, body: str) -> None:
        self.url = url
        self.status_code = 200
        self.headers = {"content-type": "application/xml" if "format=rss" in url else "text/html"}
        self.text = body

    def raise_for_status(self) -> None:
        return None


class _UnexpectedLLM:
    def execute_task(self, *args, **kwargs):
        raise AssertionError("LLM fallback should not be used for deterministic specs extraction")


class SearchBrowserPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self._search_backend = llm_config.search_backend
        self._browser_enabled = llm_config.browser_automation_enabled

    def tearDown(self) -> None:
        llm_config.search_backend = self._search_backend
        llm_config.browser_automation_enabled = self._browser_enabled

    def test_apply_cli_overrides_accepts_canonical_search_backends(self) -> None:
        llm_config.search_backend = "auto"

        apply_cli_overrides(["--search-backend", "ddgs"])
        self.assertEqual(llm_config.search_backend, "ddgs")

        apply_cli_overrides(["--search-backend", "browser"])
        self.assertEqual(llm_config.search_backend, "browser")

        apply_cli_overrides(["--search-backend", "searxng"])
        self.assertEqual(llm_config.search_backend, "searxng")

    def test_normalize_search_result_url_unwraps_redirectors(self) -> None:
        cases = [
            (
                "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Farticle%3Futm_source%3Dddg",
                "https://example.com/article",
            ),
            (
                "https://www.bing.com/ck/a?url=https%3A%2F%2Fexample.com%2Fguide%3Fref%3Dbing",
                "https://example.com/guide",
            ),
            (
                "https://www.google.com/url?q=https%3A%2F%2Fexample.com%2Fdocs%3Futm_medium%3Dsearch",
                "https://example.com/docs",
            ),
        ]
        for raw_url, expected in cases:
            with self.subTest(raw_url=raw_url):
                self.assertEqual(html_search.normalize_search_result_url(raw_url), expected)

    @patch("app.tools.search.html_search.requests.get")
    @patch("app.tools.search.html_search.browser_automation.search")
    @patch("app.tools.search.html_search.browser_automation.available")
    def test_search_web_detailed_browser_backend_prefers_browser(
        self,
        mock_available,
        mock_browser_search,
        mock_get,
    ) -> None:
        llm_config.search_backend = "browser"
        llm_config.browser_automation_enabled = True
        mock_available.return_value = True
        mock_browser_search.return_value = [
            SimpleNamespace(
                title="Example result",
                url="https://example.com/result",
                snippet="Snippet",
                provider="browser",
            )
        ]
        mock_get.side_effect = AssertionError("unexpected html provider call")

        result = html_search.search_web_detailed("example query", max_results=3)

        self.assertEqual(result["provider_name"], "browser")
        self.assertEqual(result["results"][0]["url"], "https://example.com/result")
        self.assertEqual(result["provider_chain"], ["browser"])
        mock_browser_search.assert_called_once()
        mock_get.assert_not_called()

    @patch("app.tools.search.html_search.requests.get")
    @patch("app.tools.search.html_search.browser_automation.search")
    @patch("app.tools.search.html_search.browser_automation.available")
    @patch("app.tools.search.html_search._search_via_ddgs")
    @patch("app.tools.search.html_search._search_via_searxng")
    def test_search_web_detailed_auto_falls_back_to_browser(
        self,
        mock_searxng,
        mock_ddgs,
        mock_available,
        mock_browser_search,
        mock_get,
    ) -> None:
        llm_config.search_backend = "auto"
        llm_config.browser_automation_enabled = True
        mock_searxng.return_value = []
        mock_ddgs.return_value = []
        mock_available.return_value = True
        mock_browser_search.return_value = [
            SimpleNamespace(
                title="Browser fallback",
                url="https://browser.example/fallback",
                snippet="Browser snippet",
                provider="browser",
            )
        ]
        mock_get.side_effect = lambda url, **_: _FakeSearchResponse(
            url,
            "<rss><channel></channel></rss>" if "format=rss" in url else "<html><body>No results</body></html>",
        )

        result = html_search.search_web_detailed("fallback query", max_results=2)

        self.assertEqual(result["provider_name"], "browser")
        self.assertEqual(result["results"][0]["url"], "https://browser.example/fallback")
        self.assertEqual(result["provider_chain"][-1], "browser")
        mock_browser_search.assert_called_once()
        self.assertEqual(mock_get.call_count, 4)

    def test_extract_page_text_prefers_crawler_text_content(self) -> None:
        capability = object.__new__(BrowserCapability)
        extracted = capability._tool_extract_page_text(
            {
                "url": "https://example.com/report.pdf",
                "final_url": "https://example.com/report.pdf",
                "title": "Quarterly report",
                "html": "<html><body>fallback html</body></html>",
                "content_type": "application/pdf",
                "text_content": "Extracted PDF body",
                "meta_description": "",
                "visible_text": "",
                "headings": [],
                "links": [],
                "key_values": [],
                "table_rows": [],
                "screenshot_path": "",
                "handle_id": "",
                "blocked_reason": "",
                "persisted_path": "D:/tmp/report.pdf",
                "persisted_size": 128,
                "redirect_url": "",
                "redirect_status_code": None,
                "cache_hit": True,
            }
        )

        self.assertEqual(extracted["body_text"], "Extracted PDF body")
        self.assertEqual(extracted["content_type"], "application/pdf")
        self.assertEqual(extracted["persisted_path"], "D:/tmp/report.pdf")
        self.assertTrue(extracted["cache_hit"])

    @patch("app.tools.web.research_pipeline.crawl_webpage")
    @patch("app.tools.web.research_pipeline.search_web_queries")
    def test_run_web_research_uses_crawler_text_then_snippet_fallback(
        self,
        mock_search_web_queries,
        mock_crawl_webpage,
    ) -> None:
        mock_search_web_queries.return_value = [
            {
                "title": "Primary source",
                "url": "https://example.com/primary",
                "snippet": "Primary snippet",
                "query": "query",
                "provider": "ddgs",
                "query_order": 0,
                "result_rank": 0,
            },
            {
                "title": "Fallback source",
                "url": "https://example.com/fallback",
                "snippet": "Fallback snippet",
                "query": "query",
                "provider": "ddgs",
                "query_order": 0,
                "result_rank": 1,
            },
        ]
        mock_crawl_webpage.side_effect = [
            CrawledPage(
                url="https://example.com/primary",
                final_url="https://example.com/primary",
                html="",
                text_content="Primary extracted text",
            ),
            CrawledPage(
                url="https://example.com/fallback",
                final_url="https://example.com/fallback",
                html="",
                text_content="",
            ),
        ]

        research = research_pipeline.run_web_research("query", max_pages=2, max_chars=500)

        self.assertEqual(len(research.pages), 2)
        self.assertEqual(research.pages[0].content, "Primary extracted text")
        self.assertEqual(research.pages[1].content, "Fallback snippet")

    def test_specs_summary_extracts_structured_fields_without_llm(self) -> None:
        helper = FairySkillLLMHelper(_UnexpectedLLM())
        result = helper.summarize_web_result(
            "查查 iPad Pro 的参数",
            [
                {
                    "title": "iPad Pro - Technical Specifications - Apple",
                    "url": "https://www.apple.com/ipad-pro/specs/",
                    "body_text": "Apple M4 chip\n11-inch Ultra Retina XDR display\n256GB storage\n12MP Wide camera\nUSB-C connector with Thunderbolt",
                    "headings": ["iPad Pro", "Technical Specifications"],
                    "key_values": [
                        "Chip | Apple M4 chip",
                        "Display | 11-inch Ultra Retina XDR display",
                        "Storage | 256GB",
                        "Camera | 12MP Wide camera",
                        "Connector | USB-C connector with Thunderbolt",
                    ],
                    "table_rows": [],
                }
            ],
            {},
        )

        self.assertTrue(result["fields"])
        self.assertEqual(result["fields"][0]["label"], "芯片")
        self.assertIn("M4", result["fields"][0]["value"])
        self.assertIn("参数页", result["response_text"])

    def test_tool_loop_generate_answer_uses_specs_summary_fields(self) -> None:
        runner = object.__new__(ToolLoopWebResearchRunner)
        runner.services = SimpleNamespace(llm_helper=FairySkillLLMHelper(_UnexpectedLLM()))
        runner.memory_context = ""
        runner.llm = _UnexpectedLLM()

        result = runner._generate_answer_text(
            {
                "task_type": "specs",
                "user_query": "查查 iPad Pro 的参数",
                "current_page_url": "https://www.apple.com/ipad-pro/specs/",
                "current_page_type": "docs",
                "current_page_snapshot": {
                    "title": "iPad Pro - Technical Specifications - Apple",
                    "visible_text": "",
                    "headings": ["iPad Pro", "Technical Specifications"],
                    "key_values": [
                        "Chip | Apple M4 chip",
                        "Display | 11-inch Ultra Retina XDR display",
                        "Storage | 256GB",
                    ],
                    "table_rows": [
                        "Camera | 12MP Wide camera",
                        "Connector | USB-C connector with Thunderbolt",
                    ],
                },
                "current_page_answer_context": {
                    "task_relevant_summary": "",
                    "key_evidence": [],
                },
            },
            seed_text="",
        )

        self.assertTrue(result["fields"])
        self.assertIn("芯片", result["response_text"])
        self.assertIn("屏幕", result["response_text"])


if __name__ == "__main__":
    unittest.main()
