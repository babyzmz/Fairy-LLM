"""Tests for app.agents.realtime_lookup — canonical agent public API.

Verifies:
- RealtimeLookupAgent instantiation and execution via typed contract
- RealtimeLookupRequest / RealtimeLookupResult / RealtimeLookupError protocol
- Success path (mock search), failure path (no tool), exception safety
- Parallel execution
- Result.to_dict() legacy compat shape
"""

from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from app.agents.realtime_lookup.agent import RealtimeLookupAgent
from app.agents.realtime_lookup.contract import (
    RealtimeLookupRequest, RealtimeLookupResult, RealtimeLookupError,
)
from app.agents.realtime_lookup.models import RealtimeLookupRequest as ModelRequest


def _mock_search(query: str, max_results: int = 5) -> list[dict]:
    q = query.lower()
    if "bitcoin" in q or "btc" in q:
        return [{"title": "BTC", "url": "https://coinmarketcap.com",
                 "snippet": "Bitcoin price today is $67,450 USD with +2.3% change."}]
    if "\u4e1c\u4eac" in q or "tokyo" in q:
        return [{"title": "Tokyo time", "url": "https://time.is/Tokyo",
                 "snippet": "Current time in Tokyo: 14:35 JST Friday."}]
    if "melbourne" in q and "weather" in q:
        return [{"title": "Melbourne weather", "url": "https://bom.gov.au",
                 "snippet": "Melbourne today: 21\u00b0C, partly cloudy."}]
    return []


class TestRealtimeLookupAgent:
    def setup_method(self):
        self.agent = RealtimeLookupAgent(search_tool=_mock_search)

    def test_instantiation(self):
        assert self.agent is not None

    def test_request_model_fields(self):
        req = RealtimeLookupRequest(
            query="\u6bd4\u7279\u5e01\u73b0\u5728\u4ef7\u683c\u591a\u5c11",
            allowed_tools=["search_web"], strict_mode=True,
        )
        assert req.query
        assert req.strict_mode is True

    def test_execute_returns_result_type(self):
        req = RealtimeLookupRequest(query="\u6bd4\u7279\u5e01\u73b0\u5728\u4ef7\u683c\u591a\u5c11")
        result = self.agent.execute(req)
        assert isinstance(result, RealtimeLookupResult)

    def test_result_has_required_fields(self):
        req = RealtimeLookupRequest(query="\u6bd4\u7279\u5e01\u4ef7\u683c")
        r = self.agent.execute(req)
        assert hasattr(r, "success")
        assert hasattr(r, "speech_text")
        assert hasattr(r, "card_payload")
        assert hasattr(r, "numeric_value")
        assert hasattr(r, "confidence")
        assert hasattr(r, "stage_used")
        assert hasattr(r, "source_urls")
        assert hasattr(r, "tool_lock")

    def test_no_search_tool_returns_error_result(self):
        agent = RealtimeLookupAgent(search_tool=None)
        req = RealtimeLookupRequest(query="\u6bd4\u7279\u5e01\u4ef7\u683c")
        r = agent.execute(req)
        assert isinstance(r, RealtimeLookupResult)
        assert not r.success
        assert "search_tool" in r.error_message

    def test_time_query_uses_stage1(self):
        req = RealtimeLookupRequest(query="\u73b0\u5728\u4e1c\u4eac\u51e0\u70b9")
        r = self.agent.execute(req)
        assert isinstance(r, RealtimeLookupResult)
        if r.success:
            assert r.stage_used == "stage1"
            assert r.tool_lock is True

    def test_weather_query(self):
        req = RealtimeLookupRequest(query="\u58a8\u5c14\u672c\u4eca\u5929\u5929\u6c14")
        r = self.agent.execute(req)
        assert isinstance(r, RealtimeLookupResult)

    def test_to_dict_compat_shape(self):
        req = RealtimeLookupRequest(query="\u6bd4\u7279\u5e01\u4ef7\u683c")
        r = self.agent.execute(req)
        d = r.to_dict()
        for key in ("success", "answer", "card", "source", "source_urls",
                    "confidence", "tool_lock", "tool_lock_valid",
                    "needs_clarification", "reason"):
            assert key in d, f"missing key: {key}"

    def test_error_model_to_result(self):
        err = RealtimeLookupError(reason="test_error", subtype="crypto")
        r = err.to_result()
        assert isinstance(r, RealtimeLookupResult)
        assert not r.success
        assert r.error_message == "test_error"

    def test_exception_safety(self):
        def broken(q, **kw):
            raise RuntimeError("exploded")
        agent = RealtimeLookupAgent(search_tool=broken)
        r = agent.execute(RealtimeLookupRequest(query="\u6bd4\u7279\u5e01\u4ef7\u683c"))
        assert isinstance(r, RealtimeLookupResult)
        assert not r.success

    def test_parallel_execution(self):
        results = self.agent.run_parallel([
            "\u73b0\u5728\u4e1c\u4eac\u51e0\u70b9",
            "\u58a8\u5c14\u672c\u4eca\u5929\u5929\u6c14",
        ])
        assert len(results) == 2
        assert all(isinstance(r, RealtimeLookupResult) for r in results)
