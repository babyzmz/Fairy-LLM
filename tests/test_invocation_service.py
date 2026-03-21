"""Tests for app.core �?capability registry, invocation service, skill router.

Verifies:
- CapabilityRegistry maps realtime_lookup �?RealtimeLookupAgent
- Pure-LLM skills correctly identified
- InvocationService dispatches to agent and returns structured result
- SkillAgentRouter routing table
- Compat shim (app.realtime.*) still importable
- No direct runtime imports remain in app/skills/bundles/realtime_lookup/
"""

from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


def _mock_search(query: str, max_results: int = 5) -> list[dict]:
    if "bitcoin" in query.lower() or "btc" in query.lower():
        return [{"title": "BTC", "url": "https://coinmarketcap.com",
                 "snippet": "Bitcoin $67,450 USD +2.3%"}]
    return []


class TestCapabilityRegistry:
    def setup_method(self):
        from app.core.capability_registry import CapabilityRegistry
        self.reg = CapabilityRegistry.default()

    def test_realtime_lookup_registered(self):
        cap = self.reg.get("realtime_lookup")
        assert cap is not None
        assert cap.agent_class == "RealtimeLookupAgent"
        assert cap.agent_module == "app.agents.realtime_lookup.agent"

    def test_hyphen_alias(self):
        assert self.reg.get("realtime-lookup") is not None

    def test_has_agent_backend(self):
        assert self.reg.has_agent_backend("realtime_lookup")

    def test_pure_llm_skills(self):
        for s in ["news_intelligence", "document_editing",
                  "screen_understanding", "terminal_agent"]:
            assert not self.reg.has_agent_backend(s), f"{s} should be pure-LLM"

    def test_load_agent_class(self):
        AgentCls = self.reg.load_agent_class("realtime_lookup")
        assert AgentCls is not None
        assert AgentCls.__name__ == "RealtimeLookupAgent"

    def test_load_agent_class_pure_llm_returns_none(self):
        assert self.reg.load_agent_class("news_intelligence") is None  # still pure-LLM

    def test_unknown_returns_none(self):
        assert self.reg.get("nonexistent_xyz") is None

    def test_list_all(self):
        names = [c.skill_name for c in self.reg.list_all()]
        assert "realtime_lookup" in names
        assert "web_research" in names


class TestSkillAgentRouter:
    def setup_method(self):
        from app.core.skill_router.router import SkillAgentRouter
        self.router = SkillAgentRouter()

    def test_realtime_lookup_has_agent(self):
        assert self.router.has_agent_backend("realtime_lookup")
        assert self.router.has_agent_backend("realtime-lookup")

    def test_pure_llm(self):
        assert not self.router.is_pure_llm("web-research")
        assert self.router.is_pure_llm("news-intelligence")

    def test_agent_module_path(self):
        mod = self.router.get_agent_module("realtime_lookup")
        assert mod == "app.agents.realtime_lookup.agent"

    def test_web_research_agent_module_path(self):
        mod = self.router.get_agent_module("web-research")
        assert mod == "app.agents.web_research.agent"

    def test_list_agent_backed(self):
        skills = self.router.list_agent_backed_skills()
        assert any("realtime" in s for s in skills)
        assert any("web" in s for s in skills)


class TestInvocationService:
    def setup_method(self):
        from app.core.invocation_service import InvocationService
        self.svc = InvocationService(search_tool=_mock_search)

    def test_invoke_realtime_lookup(self):
        result = self.svc.invoke(
            "realtime_lookup",
            query="\u6bd4\u7279\u5e01\u73b0\u5728\u4ef7\u683c\u591a\u5c11",
        )
        assert isinstance(result, dict)
        for key in ("success", "answer", "card", "confidence", "tool_lock"):
            assert key in result

    def test_invoke_unknown_skill(self):
        r = self.svc.invoke("nonexistent", query="test")
        assert not r["success"]
        assert r["reason"] == "skill_not_registered"

    def test_invoke_pure_llm_skill(self):
        r = self.svc.invoke("news_intelligence", query="test")
        assert not r["success"]
        assert r["reason"] == "pure_llm_skill"

    def test_agent_cached(self):
        self.svc.invoke("realtime_lookup", query="\u6bd4\u7279\u5e01")
        self.svc.invoke("realtime_lookup", query="\u6bd4\u7279\u5e01")
        assert "realtime_lookup" in self.svc._agent_cache


class TestArchitecturePurity:
    def test_canonical_query_analyzer_importable(self):
        from app.agents.realtime_lookup.query_analyzer import QueryAnalyzer
        assert QueryAnalyzer is not None

    def test_canonical_lookup_engine_importable(self):
        from app.agents.realtime_lookup.lookup_engine import LookupEngine
        assert LookupEngine is not None

    def test_canonical_timezone_resolver_importable(self):
        from app.agents.realtime_lookup.timezone_resolver import TimezoneResolver
        assert TimezoneResolver is not None

    def test_no_runtime_files_in_skills_bundle(self):
        """Skill bundle must be declarative-only �?no Python runtime files."""
        import pathlib
        bundle = pathlib.Path("app/skills/bundles/realtime_lookup")
        py_files = [f.name for f in bundle.glob("*.py")]
        assert py_files == [], f"Runtime .py files found in skill bundle: {py_files}"

    def test_no_app_realtime_imports_in_codebase(self):
        """app.realtime namespace is deleted �?no code should import from it."""
        import pathlib
        violations = []
        skip = {"__pycache__", ".git"}
        for f in pathlib.Path(".").rglob("*.py"):
            if any(p in f.parts for p in skip):
                continue
            if f.name.startswith("test_") or f.name.startswith("_"):
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except Exception:
                continue
            if "from app.realtime" in text or "import app.realtime" in text:
                violations.append(str(f))
        assert violations == [], f"app.realtime imports found: {violations}"


if __name__ == "__main__":
    pytest.main(["-v", __file__])

