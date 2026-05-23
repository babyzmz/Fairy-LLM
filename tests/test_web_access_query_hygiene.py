from __future__ import annotations

import os
import sys
from urllib.parse import quote_plus

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.agent.perception.followup_resolver import FollowUpResolver
from app.api.dependencies import FairyRuntimeService
from app.context import ContextManager
from app.context.session_context import SessionContext
from app.core.perception import EntityExtractor
from app.core.query_resolution.query_resolver import QueryResolver
from app.core.query_resolution.slot_filler import SlotFiller
from app.response.models import NormalizedAssistantResponse
from app.runtime.fairy_runtime_v2 import FairyRuntimeV2
from app.runtime.orchestration import ExecutionStep, StepExecutionResult
from app.web_access.access_resolver import WebAccessResolver
from app.web_access.decision_models import BrowserAvailabilityStatus, WebAccessDecision
from app.web_access.execution_ladder import ExecutionLadder
from app.web_access.retrieval_plan_builder import RetrievalPlanBuilder
from app.web_access.browser_executor import BrowserExecutor
from app.web_access.source_registry import resolve_source_descriptor


SHOW_CHENGDU_MAP = "\u663e\u793a\u6210\u90fd\u5730\u56fe"
SHOW_LA_MAP = "\u663e\u793a\u6d1b\u6749\u77f6\u5730\u56fe"
OPEN_TOKYO_MAP = "\u6253\u5f00\u4e1c\u4eac\u5730\u56fe"
WHERE_IS_SEOUL = "\u9996\u5c14\u5728\u54ea\u91cc"
LOOK_IT_HOME_NEWS = "\u770b\u770b IT \u4e4b\u5bb6\u4eca\u5929\u7684\u79d1\u6280\u65b0\u95fb"
LOOK_AIRPODS = "\u770b\u770bAirPods max2\u53d1\u5e03\u4e86\u5417"
LOOK_VISUAL = "\u770b\u770b\u8fd9\u4e2a\u7f51\u9875\u9876\u90e8\u516c\u544a\u5199\u4e86\u4ec0\u4e48"
OPEN_OPENAI = "\u6253\u5f00 OpenAI \u5b98\u7f51\u770b\u770b\u6700\u65b0\u53d1\u5e03"
LOOK_TECH_NEWS = "\u770b\u770b\u4eca\u5929\u7684\u79d1\u6280\u65b0\u95fb"
MONITOR_RECOMMEND = "\u6211\u6700\u8fd1\u60f3\u4e70\u663e\u793a\u5668 \u5e2e\u6211\u770b\u770b\u63a8\u8350"
CONTINUE_FIND = "\u7ee7\u7eed\u627e"
LOS_ANGELES = "\u6d1b\u6749\u77f6"
CHENGDU = "\u6210\u90fd"
SEOUL = "\u9996\u5c14"
JAVA_ISLAND = "\u722a\u54c7\u5c9b"
TECH = "\u79d1\u6280"


def _service() -> FairyRuntimeService:
    return object.__new__(FairyRuntimeService)


def _resolver() -> QueryResolver:
    return QueryResolver()


def _followup_resolver() -> FollowUpResolver:
    return FollowUpResolver()


def _planner() -> RetrievalPlanBuilder:
    return RetrievalPlanBuilder()


def _access_resolver() -> WebAccessResolver:
    return WebAccessResolver()


def _runtime() -> FairyRuntimeV2:
    runtime = object.__new__(FairyRuntimeV2)
    runtime.context_manager = ContextManager()
    return runtime


class _FakeLocationSkill:
    class _Spec:
        name = "web_research"

    SPEC = _Spec()

    def __init__(self) -> None:
        self.context_called = False
        self.lookup_called = False

    def _classify_intent(self, raw_query: str, runtime_context: dict | None = None):
        raise AssertionError("classification should not run for explicit map phrases")

    def _location_result_from_context(self, intent, runtime_context):
        self.context_called = True
        return {
            "summary": f"{LOS_ANGELES}\u4f4d\u4e8e\u7f8e\u56fd\u52a0\u5dde\u3002",
            "recommendation": "",
            "response_text": f"\u8bf7\u6c42\u5df2\u63a5\u6536\u3002{LOS_ANGELES}\u4f4d\u4e8e\u7f8e\u56fd\u52a0\u5dde\u3002",
            "sources": [],
            "title": LOS_ANGELES,
            "place_name": LOS_ANGELES,
            "address": f"\u7f8e\u56fd\u52a0\u5dde{LOS_ANGELES}",
            "lat": 34.05,
            "lon": -118.24,
            "map_url": "https://example.com/la-map",
            "image_url": "https://example.com/la-map.png",
            "map_preview_url": "https://example.com/la-map.png",
        }

    def _try_location_lookup(self, intent):
        self.lookup_called = True
        location = str(intent.constraints.get("location_query") or intent.target_entity or "").strip()
        return {
            "summary": f"{location}\u4f4d\u4e8e\u6d4b\u8bd5\u533a\u57df\u3002",
            "recommendation": "",
            "response_text": f"\u8bf7\u6c42\u5df2\u63a5\u6536\u3002{location}\u4f4d\u4e8e\u6d4b\u8bd5\u533a\u57df\u3002",
            "sources": [],
            "title": location,
            "place_name": location,
            "address": f"{location}\u6d4b\u8bd5\u5730\u5740",
            "lat": 30.0,
            "lon": 104.0,
            "map_url": f"https://example.com/{location}",
            "image_url": f"https://example.com/{location}.png",
            "map_preview_url": f"https://example.com/{location}.png",
        }


class _FakeExecution:
    def __init__(
        self,
        *,
        browser_result: dict | None = None,
        search_results: list[dict] | None = None,
        source_name: str = "",
        source_constraints: dict | None = None,
        failure_reason: str = "",
    ) -> None:
        self.browser_result = browser_result or {}
        self.search_results = search_results or []
        self.decision = type("Decision", (), {"source_name": source_name, "intent_type": "source_constrained_lookup", "access_mode": "http_fetch"})()
        self.retrieval_plan = type("Plan", (), {"source_constraints": source_constraints or {}})()
        self.failure_reason = failure_reason
        self.browser_availability = None
        self.visual_results = []


class _SearchSpy:
    def __init__(self, responses: dict[str, list[dict]]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def __call__(self, query: str, max_results: int = 5, preferred_domains: list[str] | None = None):
        _ = max_results, preferred_domains
        self.calls.append(query)
        return list(self.responses.get(query, []))


class _FakeBrowseBrowser:
    def __init__(self, pages: dict[str, dict]) -> None:
        self.pages = pages
        self.opened: list[str] = []

    def availability_status(self) -> BrowserAvailabilityStatus:
        return BrowserAvailabilityStatus(available=True, level="full")

    def available(self) -> bool:
        return True

    def rendered_read(self, url: str) -> dict:
        self.opened.append(url)
        page = self.pages.get(url)
        if page is None:
            return {
                "ok": False,
                "error": "open_failed",
                "page": {"url": url, "final_url": url},
                "availability": self.availability_status().to_dict(),
            }
        payload = {"url": url, "final_url": url, "title": "", "visible_text": "", "headings": [], "links": [], "nav_items": []}
        payload.update(page)
        payload.setdefault("url", url)
        payload.setdefault("final_url", url)
        return {"ok": True, "error": "", "page": payload, "availability": self.availability_status().to_dict()}

    def classify_page_type(self, page_payload: dict, *, source_descriptor=None, task_type: str = "", entity: str = "") -> str:
        _ = source_descriptor, task_type, entity
        return str(page_payload.get("page_type") or "generic")

    def extract_links(self, page_payload: dict) -> list[dict]:
        return list(page_payload.get("links") or [])


def test_new_entity_breaks_topic_carryover():
    svc = _service()
    result = svc._resolve_effective_web_query(
        raw_query=LOOK_AIRPODS,
        resolved_query=f"{TECH} AirPods max2\u53d1\u5e03\u4e86\u5417",
        resolved_capability="generic_search",
        slots={},
        context={"last_topic": TECH, "last_url": "https://example.com/news"},
        route_hints={},
    )
    assert result["effective_query"] == LOOK_AIRPODS
    assert result["query_authority"] == "source_constrained_override"
    assert result["query_mutation_reason"] == "explicit_source_overrides_history"
    assert result["topic_carryover_applied"] is False
    assert result["source_constraint_applied"] is True


def test_source_constraint_overrides_history():
    svc = _service()
    result = svc._resolve_effective_web_query(
        raw_query=LOOK_IT_HOME_NEWS,
        resolved_query=f"{TECH} news today",
        resolved_capability="news_lookup",
        slots={"source": "IT\u4e4b\u5bb6", "topic": TECH},
        context={"last_topic": TECH, "last_url": "https://example.com/news"},
        route_hints={},
    )
    assert result["effective_query"] == LOOK_IT_HOME_NEWS
    assert result["query_authority"] == "source_constrained_override"
    assert result["query_mutation_reason"] == "explicit_source_overrides_history"
    assert result["source_constraint_applied"] is True
    assert "last_topic" not in result["planning_context"]


def test_visual_followup_keeps_page_context_but_not_topic():
    svc = _service()
    result = svc._resolve_effective_web_query(
        raw_query=LOOK_VISUAL,
        resolved_query=f"{TECH} {LOOK_VISUAL}",
        resolved_capability="generic_search",
        slots={},
        context={
            "last_topic": TECH,
            "last_url": "https://example.com/page",
            "last_page_handle": "page-1",
            "last_visual_region": "results_panel",
        },
        route_hints={},
    )
    assert result["effective_query"] == LOOK_VISUAL
    assert result["query_authority"] == "page_context_followup"
    assert result["query_mutation_reason"] == "visual_task_blocks_topic_inheritance"
    assert result["page_context_available"] is True
    assert result["planning_context"]["last_url"] == "https://example.com/page"
    assert "last_topic" not in result["planning_context"]


def test_browser_task_breaks_topic_carryover():
    svc = _service()
    result = svc._resolve_effective_web_query(
        raw_query=OPEN_OPENAI,
        resolved_query=f"{TECH} {OPEN_OPENAI}",
        resolved_capability="generic_search",
        slots={},
        context={"last_topic": TECH, "last_url": "https://example.com/news"},
        route_hints={},
    )
    assert result["effective_query"] == OPEN_OPENAI
    assert result["query_authority"] == "source_constrained_override"
    assert result["query_mutation_reason"] == "explicit_source_overrides_history"
    assert "last_topic" not in result["planning_context"]


def test_cached_web_query_inputs_are_reused():
    svc = _service()
    cached = {
        "raw_query": OPEN_OPENAI,
        "resolved_query": f"{TECH} {OPEN_OPENAI}",
        "effective_query": OPEN_OPENAI,
        "query_authority": "source_constrained_override",
        "query_mutation_reason": "explicit_source_overrides_history",
    }
    route_hints = {"_web_query_inputs": dict(cached)}
    result = svc._get_or_create_web_query_inputs(
        message=OPEN_OPENAI,
        session_id="default",
        resolved_capability="generic_search",
        route_hints=route_hints,
    )
    assert result["effective_query"] == cached["effective_query"]
    assert result["query_authority"] == cached["query_authority"]


def test_generic_news_prefers_tech_target_url():
    planner = _planner()
    decision = WebAccessDecision(
        needs_web=True,
        access_mode="http_fetch",
        intent_type="general_web_research",
    )
    plan = planner.build(query=LOOK_TECH_NEWS, decision=decision, slots={}, context={})
    assert plan.source_constraints["query_strategy"] == "generic_news"
    assert plan.target_urls
    assert "ithome.com/tags/%E7%A7%91%E6%8A%80/" in plan.target_urls[0]


def test_source_registry_resolves_apple_products():
    descriptor = resolve_source_descriptor(LOOK_AIRPODS)
    assert descriptor is not None
    assert descriptor.key == "apple"


def test_source_registry_includes_ipad_mini_product_path():
    planner = _planner()
    decision = WebAccessDecision(
        needs_web=True,
        access_mode="http_fetch",
        intent_type="source_constrained_lookup",
        source_name="Apple",
        source_domain="apple.com",
        preferred_domains=["apple.com"],
    )
    plan = planner.build(query="帮我查查 iPad mini7 参数", decision=decision, slots={}, context={})
    assert plan.source_constraints["task_type"] == "specs"
    assert any("/ipad-mini/" in url for url in plan.target_urls)


def test_source_registry_builds_dynamic_descriptor_for_unknown_domain():
    descriptor = resolve_source_descriptor("check https://example.com/docs for overview")
    assert descriptor is not None
    assert descriptor.key == "dynamic:example.com"
    assert descriptor.home_url == "https://example.com/"


def test_access_resolver_treats_airpods_release_as_source_constrained():
    resolver = _access_resolver()
    decision = resolver.resolve(
        raw_query=LOOK_AIRPODS,
        resolved_query=LOOK_AIRPODS,
        resolved_capability="generic_search",
        slots={},
        context={},
    )
    assert decision.intent_type == "source_constrained_lookup"
    assert decision.source_domain == "apple.com"
    assert decision.target_url.startswith("https://www.apple.com/newsroom/")


def test_access_resolver_treats_unknown_explicit_url_as_source_constrained():
    resolver = _access_resolver()
    decision = resolver.resolve(
        raw_query="看看 https://example.com/docs 上写了什么",
        resolved_query="看看 https://example.com/docs 上写了什么",
        resolved_capability="generic_search",
        slots={},
        context={},
    )
    assert decision.intent_type == "source_constrained_lookup"
    assert decision.source_domain == "example.com"
    assert decision.target_url == "https://example.com/docs"


def test_release_query_builds_release_aware_variants():
    planner = _planner()
    decision = WebAccessDecision(
        needs_web=True,
        access_mode="http_fetch",
        intent_type="general_web_research",
    )
    plan = planner.build(query=LOOK_AIRPODS, decision=decision, slots={}, context={})
    assert plan.source_constraints["query_strategy"] == "generic_web_release"
    joined = " | ".join(plan.primary_queries + plan.fallback_queries)
    assert "发布时间" in joined
    assert "发布" in joined
    assert "release date" in joined


def test_source_constrained_release_query_builds_apple_official_variants():
    planner = _planner()
    decision = WebAccessDecision(
        needs_web=True,
        access_mode="http_fetch",
        intent_type="source_constrained_lookup",
        source_name="Apple",
        source_domain="apple.com",
        preferred_domains=["apple.com", "www.apple.com"],
        target_url="https://www.apple.com/newsroom/",
    )
    plan = planner.build(query=LOOK_AIRPODS, decision=decision, slots={}, context={})
    assert plan.source_constraints["query_strategy"] == "source_constrained_release"
    joined = " | ".join(plan.primary_queries + plan.fallback_queries).lower()
    assert "site:apple.com" in joined
    assert "release date" in joined


def test_source_constrained_specs_query_builds_apple_specs_variants():
    planner = _planner()
    decision = WebAccessDecision(
        needs_web=True,
        access_mode="http_fetch",
        intent_type="source_constrained_lookup",
        source_name="Apple",
        source_domain="apple.com",
        preferred_domains=["apple.com", "www.apple.com"],
        target_url="https://www.apple.com/",
    )
    plan = planner.build(query="帮我查查ipad pro参数", decision=decision, slots={}, context={})
    assert plan.source_constraints["query_strategy"] == "source_constrained_specs"
    joined = " | ".join(plan.primary_queries + plan.fallback_queries).lower()
    assert "site:apple.com" in joined
    assert "specs" in joined


def test_dynamic_source_constrained_query_builds_general_info_browse_strategy():
    planner = _planner()
    decision = WebAccessDecision(
        needs_web=True,
        access_mode="http_fetch",
        intent_type="source_constrained_lookup",
        source_name="example.com",
        source_domain="example.com",
        preferred_domains=["example.com"],
        target_url="https://example.com/docs",
    )
    plan = planner.build(query="看看 https://example.com/docs 上写了什么", decision=decision, slots={}, context={})
    constraints = plan.source_constraints
    assert constraints["browse_strategy"] == "source_constrained_browse"
    assert constraints["task_type"] == "general_info"
    assert constraints["entry_urls"]
    assert constraints["navigation_targets"]


def test_continue_reuses_previous_generic_news_plan():
    planner = _planner()
    decision = WebAccessDecision(
        needs_web=True,
        access_mode="http_fetch",
        intent_type="general_web_research",
    )
    plan = planner.build(
        query=CONTINUE_FIND,
        decision=decision,
        slots={},
        context={
            "last_query_strategy": "generic_news",
            "last_result_kind": "web_failure",
            "last_request_id": "req-1",
            "last_retrieval_plan": {
                "primary_queries": ["今天 科技新闻", "科技 最新消息"],
                "fallback_queries": ["科技 新闻", "科技 资讯"],
                "target_urls": ["https://www.ithome.com/tag/科技/"],
                "source_constraints": {"query_strategy": "generic_news", "topic": TECH},
            },
            "last_topic": TECH,
        },
    )
    assert plan.source_constraints["continuation_applied"] is True
    assert plan.source_constraints["continuation_strategy"] == "continue_previous_web_plan"
    assert plan.source_constraints["query_strategy"] == "generic_news_continuation"
    assert CONTINUE_FIND not in " | ".join(plan.primary_queries + plan.fallback_queries)


def test_expand_source_drops_source_constraint_for_previous_news_failure():
    planner = _planner()
    decision = WebAccessDecision(
        needs_web=True,
        access_mode="http_fetch",
        intent_type="general_web_research",
    )
    plan = planner.build(
        query="换个来源继续找",
        decision=decision,
        slots={},
        context={
            "last_query_strategy": "source_constrained",
            "last_result_kind": "web_failure",
            "last_request_id": "req-2",
            "last_retrieval_plan": {
                "primary_queries": ["site:ithome.com 科技 新闻 今天"],
                "fallback_queries": ["IT之家 科技新闻"],
                "target_urls": ["https://www.ithome.com/list/"],
                "source_constraints": {
                    "query_strategy": "source_constrained",
                    "topic": TECH,
                    "source_name": "IT之家",
                    "source_domain": "ithome.com",
                },
            },
            "last_topic": TECH,
        },
    )
    assert plan.source_constraints["continuation_applied"] is True
    assert plan.source_constraints["continuation_strategy"] == "expand_source"
    assert plan.source_constraints["query_strategy"] == "generic_news_continuation"
    assert not any("site:ithome.com" in item for item in plan.primary_queries)
    assert plan.target_urls == []


def test_continuation_preserves_original_user_query_for_expand_source():
    planner = _planner()
    decision = WebAccessDecision(
        needs_web=True,
        access_mode="http_fetch",
        intent_type="general_web_research",
    )
    plan = planner.build(
        query="换个来源继续找",
        decision=decision,
        slots={},
        context={
            "last_query_strategy": "generic_news_continuation",
            "last_result_kind": "web_failure",
            "last_request_id": "req-3",
            "last_query": "某官网今天有什么新消息",
            "last_retrieval_plan": {
                "primary_queries": ["某官网今天有什么新消息 latest news"],
                "fallback_queries": ["某官网今天有什么新消息 最新情况"],
                "target_urls": [],
                "source_constraints": {
                    "query_strategy": "generic_news_continuation",
                    "topic": "某官网今天有什么新消息",
                    "user_query": "某官网今天有什么新消息",
                    "original_user_query": "某官网今天有什么新消息",
                    "task_type": "news",
                    "force_web_browse": True,
                    "browse_strategy": "forced_web_browse",
                },
            },
            "last_topic": "某官网今天有什么新消息",
        },
    )
    constraints = plan.source_constraints
    assert constraints["continuation_applied"] is True
    assert constraints["user_query"] == "某官网今天有什么新消息"
    assert constraints["original_user_query"] == "某官网今天有什么新消息"
    joined_queries = " | ".join(plan.primary_queries + plan.fallback_queries)
    assert "换个来源继续找" not in joined_queries
    assert "继续" not in joined_queries


def test_microsoft_newsroom_headings_are_extracted_as_news_items():
    svc = _service()
    execution = _FakeExecution(
        browser_result={
            "final_url": "https://news.microsoft.com/source/",
            "title": "Microsoft Source",
            "headings": [
                "Global",
                "Source",
                "Japan’s ARUM turns craftsmanship into scalable AI for precision manufacturing",
                "Microsoft launches new secure AI workflow for enterprise teams",
            ],
        },
        source_name="Microsoft",
    )
    bundle = svc._extract_news_bundle_from_web_access(
        execution,
        query_strategy="source_constrained",
        raw_query="看看微软官网今天有什么新消息",
    )
    assert bundle["extraction_profile"] == "newsroom_headings"
    headlines = [item["headline"] for item in bundle["items"]]
    assert "Global" not in headlines
    assert any("ARUM" in headline for headline in headlines)


def test_generic_news_filters_promotional_titles():
    svc = _service()
    execution = _FakeExecution(
        browser_result={
            "final_url": "https://www.ithome.com/tag/科技/",
            "visible_text": "\n".join(
                [
                    "[数码之家]",
                    "庄臣雷达蚊香液 5 瓶 1 器 15.6 元探底 2026-03-28 20:22:42",
                    "[智能时代]",
                    "全国最大人形机器人训练基地在京揭牌 2026-03-28 19:46:20",
                ]
            ),
        },
        source_name="IT之家",
    )
    bundle = svc._extract_news_bundle_from_web_access(
        execution,
        query_strategy="generic_news",
        raw_query=LOOK_TECH_NEWS,
    )
    headlines = [item["headline"] for item in bundle["items"]]
    assert any("人形机器人训练基地" in headline for headline in headlines)
    assert not any("蚊香液" in headline for headline in headlines)


def test_source_constrained_news_also_filters_promotional_titles():
    svc = _service()
    execution = _FakeExecution(
        browser_result={
            "final_url": "https://www.ithome.com/list/",
            "visible_text": "\n".join(
                [
                    "西南首个商业卫星遥感测运控站在四川启用 2026-03-28 21:27:15",
                    "110g 大克重：玉沙棉纤毛巾 12.5 元 3 条久违发车（日常 24 元） 2026-03-28 21:16:02",
                ]
            ),
        },
        source_name="IT之家",
    )
    bundle = svc._extract_news_bundle_from_web_access(
        execution,
        query_strategy="source_constrained",
        raw_query=LOOK_IT_HOME_NEWS,
    )
    headlines = [item["headline"] for item in bundle["items"]]
    assert any("商业卫星遥感测运控站" in headline for headline in headlines)
    assert not any("毛巾" in headline for headline in headlines)


def test_specs_query_builds_specs_aware_variants():
    planner = _planner()
    decision = WebAccessDecision(
        needs_web=True,
        access_mode="http_fetch",
        intent_type="general_web_research",
    )
    plan = planner.build(query="帮我查查ipad pro参数", decision=decision, slots={}, context={})
    assert plan.source_constraints["query_strategy"] == "generic_web_specs"
    joined = " | ".join(plan.primary_queries + plan.fallback_queries).lower()
    assert "参数" in joined
    assert "规格" in joined
    assert "specs" in joined


def test_explicit_map_phrase_bypasses_context_reuse():
    svc = _service()
    svc._location_skill = _FakeLocationSkill()
    payload = svc._execute_location_intent_without_browser(
        message=SHOW_CHENGDU_MAP,
        session_id="default",
        request_id="req-map",
        request_origin="test",
        route_hints={
            "raw_user_text": SHOW_CHENGDU_MAP,
            "resolved_query": "show map for Web access failed",
            "resolved_slots": {},
            "perception_intent": "display_information",
        },
        runtime_context={
            "previous_structured": {
                "type": "location",
                "location": {
                    "place_name": LOS_ANGELES,
                    "address": f"\u7f8e\u56fd\u52a0\u5dde{LOS_ANGELES}",
                    "lat": 34.05,
                    "lon": -118.24,
                },
            }
        },
    )
    assert payload is not None
    assert CHENGDU in str(payload.get("assistant_text") or "")
    assert payload.get("_runtime_query_debug", {}).get("query_mutation_reason") == "map_phrase_bypasses_strict_slot_parse"
    assert svc._location_skill.context_called is False
    assert svc._location_skill.lookup_called is True


def test_explicit_map_phrase_retains_raw_query_for_los_angeles():
    svc = _service()
    result = svc._resolve_effective_web_query(
        raw_query=SHOW_LA_MAP,
        resolved_query="show map for Web access failed",
        resolved_capability="location_lookup",
        slots={},
        context={"last_topic": TECH},
        route_hints={},
    )
    assert result["effective_query"] == SHOW_LA_MAP
    assert result["query_authority"] == "raw_query"


def test_source_constrained_specs_builds_browse_strategy():
    planner = _planner()
    decision = WebAccessDecision(
        needs_web=True,
        access_mode="http_fetch",
        intent_type="source_constrained_lookup",
        source_name="Apple",
        source_domain="apple.com",
        preferred_domains=["apple.com", "www.apple.com"],
        target_url="https://www.apple.com/",
    )
    plan = planner.build(query="帮我查查 iPad Pro 参数", decision=decision, slots={}, context={})
    constraints = plan.source_constraints
    assert constraints["browse_strategy"] == "source_constrained_browse"
    assert constraints["task_type"] == "specs"
    assert constraints["entity"].lower() == "ipad pro"
    assert any("/ipad-pro/specs/" in item for item in constraints["entry_urls"])


def test_source_constrained_release_builds_browse_strategy():
    planner = _planner()
    decision = WebAccessDecision(
        needs_web=True,
        access_mode="http_fetch",
        intent_type="source_constrained_lookup",
        source_name="Apple",
        source_domain="apple.com",
        preferred_domains=["apple.com", "www.apple.com"],
        target_url="https://www.apple.com/newsroom/",
    )
    plan = planner.build(query=LOOK_AIRPODS, decision=decision, slots={}, context={})
    constraints = plan.source_constraints
    assert constraints["browse_strategy"] == "source_constrained_browse"
    assert constraints["task_type"] == "release"
    assert "airpods max" in constraints["entity"].lower()
    assert constraints["entry_urls"]


def test_source_constrained_news_builds_browse_strategy():
    planner = _planner()
    decision = WebAccessDecision(
        needs_web=True,
        access_mode="http_fetch",
        intent_type="source_constrained_lookup",
        source_name="Microsoft",
        source_domain="microsoft.com",
        preferred_domains=["microsoft.com", "news.microsoft.com"],
        target_url="https://news.microsoft.com/source/",
    )
    plan = planner.build(query="看看微软官网今天有什么新消息", decision=decision, slots={}, context={})
    constraints = plan.source_constraints
    assert constraints["browse_strategy"] == "source_constrained_browse"
    assert constraints["task_type"] == "news"
    assert constraints["entry_urls"][0].startswith("https://news.microsoft.com/source/")


def test_continue_reuses_previous_source_browse_plan():
    planner = _planner()
    decision = WebAccessDecision(
        needs_web=True,
        access_mode="http_fetch",
        intent_type="source_constrained_lookup",
        source_name="Apple",
        source_domain="apple.com",
        preferred_domains=["apple.com", "www.apple.com"],
    )
    plan = planner.build(
        query=CONTINUE_FIND,
        decision=decision,
        slots={},
        context={
            "last_query_strategy": "source_constrained_specs",
            "last_result_kind": "web_failure",
            "last_request_id": "req-browse-1",
            "last_browse_mode_used": "source_constrained_browse",
            "last_task_type": "specs",
            "last_navigation_hops": 1,
            "last_selected_links": [
                {"text": "iPad Pro", "url": "https://www.apple.com/ipad-pro/"},
                {"text": "Tech Specs", "url": "https://www.apple.com/ipad-pro/specs/"},
            ],
            "last_final_page_type": "product",
            "last_final_page_url": "https://www.apple.com/ipad-pro/",
            "last_retrieval_plan": {
                "primary_queries": ["site:apple.com iPad Pro 参数", "site:apple.com iPad Pro specs"],
                "fallback_queries": ["Apple iPad Pro 参数"],
                "target_urls": ["https://www.apple.com/ipad-pro/"],
                "preferred_domains": ["apple.com", "www.apple.com"],
                "stop_conditions": ["source_browse_target_reached"],
                "source_constraints": {
                    "query_strategy": "source_constrained_specs",
                    "source_name": "Apple",
                    "source_domain": "apple.com",
                    "browse_strategy": "source_constrained_browse",
                    "task_type": "specs",
                    "entity": "iPad Pro",
                    "max_hops": 2,
                    "max_candidate_links": 2,
                    "navigation_targets": ["Tech Specs", "Specifications"],
                },
            },
        },
    )
    constraints = plan.source_constraints
    assert constraints["continuation_applied"] is True
    assert constraints["browse_strategy"] == "source_constrained_browse"
    assert constraints["task_type"] == "specs"
    assert plan.target_urls == ["https://www.apple.com/ipad-pro/specs/"]


def test_browse_specs_answer_mentions_specs_page():
    svc = _service()
    execution = _FakeExecution(
        browser_result={
            "browse_mode_used": True,
            "task_type": "specs",
            "title": "iPad Pro - Tech Specs - Apple",
            "final_page_url": "https://www.apple.com/ipad-pro/specs/",
            "key_values": ["chip=M4", "display=Ultra Retina XDR", "storage=256GB"],
        },
        source_name="Apple",
        source_constraints={"task_type": "specs"},
    )
    answer = svc._generic_web_answer_from_execution_clean(execution)
    assert "规格页面" in answer
    assert "chip=M4" in answer


def test_activity_payload_contains_browse_steps_and_sources():
    svc = _service()
    execution = _FakeExecution(
        browser_result={
            "browse_mode_used": True,
            "task_type": "specs",
            "title": "iPad Pro - Tech Specs - Apple",
            "final_page_url": "https://www.apple.com/ipad-pro/specs/",
            "final_page_type": "specs",
            "source_discovery_completed": True,
            "page_open_attempted": True,
            "model_decision_emitted": True,
            "selected_links": [
                {"text": "iPad Pro", "url": "https://www.apple.com/ipad-pro/"},
                {"text": "Tech Specs", "url": "https://www.apple.com/ipad-pro/specs/"},
            ],
        },
        source_name="Apple",
        source_constraints={"task_type": "specs", "query_strategy": "source_constrained_specs"},
    )
    activity = svc._build_web_activity_payload(
        raw_query="帮我查查 iPad Pro 参数",
        execution=execution,
        debug_meta={
            "task_type": "specs",
            "source_name": "Apple",
            "query_strategy": "source_constrained_specs",
            "source_discovery_completed": True,
            "page_open_attempted": True,
            "model_decision_emitted": True,
            "source_candidates": [{"url": "https://www.apple.com/ipad-pro/specs/"}],
        },
        news_bundle={"items": [], "rendered_item_count": 0},
    )
    assert activity["entries"]
    assert any(entry["stage"] == "source_discovery_completed" for entry in activity["entries"])
    assert any(entry["stage"] == "page_open_attempted" for entry in activity["entries"])
    assert any(entry["stage"] == "model_decision_emitted" for entry in activity["entries"])
    assert any(entry["stage"] == "opening_page" for entry in activity["entries"])
    assert any(source["url"] == "https://www.apple.com/ipad-pro/specs/" for source in activity["sources"])


def test_query_resolver_prefers_location_lookup_for_explicit_map_phrase():
    resolver = _resolver()
    extractor = EntityExtractor()
    text = SHOW_CHENGDU_MAP
    context = SessionContext(session_id="default", last_capability="display_information", active_location_target=LOS_ANGELES)
    result = resolver.resolve(
        raw_text=text,
        normalized_text=text,
        intent_hint="display_information",
        entities=extractor.extract(text),
        session_context=context,
        previous_structured=None,
        followup_target="",
        followup_focus_value="",
    )
    assert result.selected_capability == "location_lookup"
    assert result.capability == "location_lookup"
    assert result.validated_slots.get("location") == CHENGDU
    assert not any(item.get("source") == "session_last_location" for item in result.carryover_trace)


def test_query_resolver_prefers_location_lookup_for_where_is_phrase():
    resolver = _resolver()
    extractor = EntityExtractor()
    text = WHERE_IS_SEOUL
    context = SessionContext(session_id="default", last_capability="display_information", active_location_target=LOS_ANGELES)
    result = resolver.resolve(
        raw_text=text,
        normalized_text=text,
        intent_hint="display_information",
        entities=extractor.extract(text),
        session_context=context,
        previous_structured=None,
        followup_target="",
        followup_focus_value="",
    )
    assert result.selected_capability == "location_lookup"
    assert result.validated_slots.get("location") == SEOUL


def test_query_resolver_does_not_carry_screen_topic_into_web_query():
    resolver = _resolver()
    extractor = EntityExtractor()
    text = "看看apple官网今天有什么新消息"
    context = SessionContext(
        session_id="default",
        last_capability="screen-understanding",
        active_topic="分析我的当前屏幕",
    )
    result = resolver.resolve(
        raw_text=text,
        normalized_text=text,
        intent_hint="generic_search",
        entities=extractor.extract(text),
        session_context=context,
        previous_structured={"bundle_name": "screen-understanding", "screen_followup_remaining": 2},
        followup_target="",
        followup_focus_value="",
    )
    assert result.selected_capability == "generic_search"
    assert result.validated_slots.get("query") == "apple官网今天有什么新消息"
    assert "topic" not in result.validated_slots


def test_followup_resolver_does_not_reuse_location_for_generic_recommendation_query():
    resolver = _followup_resolver()
    followup = resolver.resolve(
        MONITOR_RECOMMEND,
        previous_structured={
            "type": "location",
            "title": LOS_ANGELES,
            "address": f"\u7f8e\u56fd\u52a0\u5dde{LOS_ANGELES}",
        },
    )
    assert followup.target == ""
    assert followup.focus_value == ""


def test_followup_resolver_does_not_reuse_location_for_news_query():
    resolver = _followup_resolver()
    followup = resolver.resolve(
        LOOK_TECH_NEWS,
        previous_structured={
            "type": "location",
            "title": JAVA_ISLAND,
            "address": "\u5370\u5ea6\u5c3c\u897f\u4e9a",
        },
    )
    assert followup.target == ""
    assert followup.focus_value == ""


def test_web_slot_sanitizer_drops_followup_topic_and_location_for_generic_search():
    svc = _service()
    sanitized = svc._sanitize_web_resolution_slots(
        raw_query=CONTINUE_FIND,
        resolved_capability="generic_search",
        slots={
            "query": CONTINUE_FIND,
            "topic": MONITOR_RECOMMEND,
            "location": JAVA_ISLAND,
        },
        slot_sources={
            "query": "normalized_query",
            "topic": "followup_context",
            "location": "rule_override",
        },
        query_debug={
            "query_authority": "raw_query",
            "topic_carryover_applied": False,
            "source_constraint_applied": False,
        },
    )
    assert sanitized == {"query": CONTINUE_FIND}


def test_slot_filler_ignores_noisy_context_defaults():
    filler = SlotFiller()
    context = SessionContext(
        session_id="default",
        active_topic="No running session file found: D:\\desk\\runtime\\fairy_desktop_dev.json",
        active_weather_location="Web access failed",
        active_location_target="Type: error_card; Message: unavailable",
    )
    assert (
        filler._resolve_source_value(
            "followup_context",
            slot_name="topic",
            normalized_text="帮我查查 iPad Pro 参数",
            session_context=context,
            previous_structured={},
            followup_target="",
            followup_focus_value="",
        )
        == ""
    )
    assert (
        filler._resolve_source_value(
            "session_last_weather_location",
            slot_name="location",
            normalized_text="现在要干什么",
            session_context=context,
            previous_structured={},
            followup_target="",
            followup_focus_value="",
        )
        == ""
    )


def test_slot_filler_ignores_screen_context_for_topic_defaults():
    filler = SlotFiller()
    context = SessionContext(
        session_id="default",
        active_topic="分析我的当前屏幕",
    )
    assert (
        filler._resolve_source_value(
            "followup_context",
            slot_name="topic",
            normalized_text="看看 apple 官网今天有什么新消息",
            session_context=context,
            previous_structured={},
            followup_target="",
            followup_focus_value="",
        )
        == ""
    )


def test_web_slot_sanitizer_drops_screen_topic_context():
    svc = _service()
    sanitized = svc._sanitize_web_resolution_slots(
        raw_query="看看 apple 官网今天有什么新消息",
        resolved_capability="generic_search",
        slots={"query": "apple 官网今天有什么新消息", "topic": "分析我的当前屏幕"},
        slot_sources={"query": "normalized_query", "topic": "followup_context"},
        query_debug={
            "query_authority": "raw_query",
            "topic_carryover_applied": True,
            "source_constraint_applied": True,
        },
    )
    assert sanitized == {"query": "apple 官网今天有什么新消息"}


def test_bundle_execute_routes_web_queries_to_web_access():
    svc = _service()
    seen: dict[str, str] = {}

    def _should_route_text_intent_to_web_access(**kwargs):
        seen["intent"] = kwargs["intent"]
        return True

    def _execute_web_access_request(**kwargs):
        seen["message"] = kwargs["message"]
        return {"skill_name": "web-research", "structured": {"bundle_name": "web-research"}}

    svc._should_route_text_intent_to_web_access = _should_route_text_intent_to_web_access  # type: ignore[attr-defined]
    svc._execute_web_access_request = _execute_web_access_request  # type: ignore[attr-defined]
    payload = svc._bundle_execute(
        message="帮我查查 iPad mini7 参数",
        session_id="default",
        request_id="req-1",
        attachments=[],
        request_origin="test",
        route_hints={"perception_intent": "generic_search"},
    )
    assert seen["intent"] == "generic_search"
    assert seen["message"] == "帮我查查 iPad mini7 参数"
    assert payload["skill_name"] == "web-research"


def test_query_resolver_rejects_noisy_followup_focus_value():
    resolver = _resolver()
    assert (
        resolver._sanitize_followup_focus_value(
            normalized_text="帮我查查 iPad Pro 参数",
            followup_target="location",
            followup_focus_value="No running session file found: D:\\desk\\runtime\\fairy_desktop_dev.json",
        )
        == ""
    )


def test_runtime_ignores_failed_step_entities_when_updating_context():
    runtime = _runtime()
    step = ExecutionStep(capability="time_lookup", slots={"location": "Web access failed"}, step_index=0, normalized_query="现在几点")
    result = StepExecutionResult(
        step_id=step.step_id,
        capability=step.capability,
        success=False,
        output_summary="Type: error_card; Message: 实时数据暂不可用",
        produced_entities=["Web access failed"],
        error_type="runtime_stream_error",
    )
    runtime._apply_step_result_to_session_context("default", step, result)
    context = runtime.context_manager.get_or_create("default")
    assert context.active_location_target == ""
    assert context.active_weather_location == ""


def test_runtime_extract_step_entities_filters_error_titles():
    runtime = _runtime()
    step = ExecutionStep(capability="generic_search", slots={"query": "iPad Pro 参数"}, step_index=0, normalized_query="iPad Pro 参数")
    normalized = NormalizedAssistantResponse(intent="generic_search", modality="text_only", text_reply="")
    entities = runtime._extract_step_entities(
        step=step,
        normalized=normalized,
        payload={
            "structured": {
                "title": "Web access failed",
                "query": "iPad Pro 参数",
                "address": "Type: error_card; Message: unavailable",
            }
        },
    )
    assert "Web access failed" not in entities
    assert "Type: error_card; Message: unavailable" not in entities
    assert "iPad Pro 参数" in entities


def test_browser_executor_keeps_apple_homepage_as_home_not_specs():
    descriptor = resolve_source_descriptor("帮我查查 iPad mini7 参数")
    page_type = BrowserExecutor._classify_page_type(
        {
            "url": "https://www.apple.com/",
            "title": "Apple",
            "visible_text": "Store Shop Shop the Latest Mac iPad iPhone Apple Watch Apple Vision Pro AirPods Accessories Quick Links Find a Store Order Status App Displays",
            "headings": ["Store", "Shop", "Quick Links"],
            "nav_items": ["Mac", "iPad", "iPhone", "AirPods", "Support"],
            "links": [{"text": "iPad", "url": "https://www.apple.com/ipad/"}],
        },
        source_descriptor=descriptor,
        task_type="specs",
        entity="iPad mini7",
    )
    assert page_type == "homepage"


def test_specs_stop_signal_does_not_accept_homepage_noise():
    stop, reason = ExecutionLadder._browse_stop_signal(
        page_payload={
            "url": "https://www.apple.com/",
            "title": "Apple",
            "visible_text": "Store Shop Shop the Latest Mac iPad iPhone Apple Watch Apple Vision Pro AirPods Accessories Quick Links Find a Store Order Status App Displays",
            "headings": ["Store", "Shop", "Quick Links"],
            "nav_items": ["Mac", "iPad", "iPhone", "AirPods", "Support"],
            "links": [{"text": "iPad", "url": "https://www.apple.com/ipad/"}],
            "key_values": [],
            "table_rows": [],
        },
        task_type="specs",
        entity="iPad mini7",
        page_type="homepage",
    )
    assert stop is False
    assert reason == ""


def test_screen_task_queries_do_not_route_to_web_access():
    svc = _service()
    assert svc._looks_like_screen_task_query("分析我的当前屏幕") is True
    assert svc._looks_like_screen_task_query("我当前这步要怎么继续") is True


def test_general_info_query_forces_web_browse():
    planner = _planner()
    decision = WebAccessDecision(needs_web=True, access_mode="http_fetch", intent_type="general_web_research")
    plan = planner.build(query="Stripe 官网是干嘛的", decision=decision, slots={}, context={})
    assert plan.source_constraints["force_web_browse"] is True
    assert plan.source_constraints["task_type"] == "general_info"
    assert plan.source_constraints["browse_strategy"] == "forced_web_browse"


def test_compare_query_forces_web_browse():
    planner = _planner()
    decision = WebAccessDecision(needs_web=True, access_mode="http_fetch", intent_type="general_web_research")
    plan = planner.build(query="M3 vs M2 区别", decision=decision, slots={}, context={})
    assert plan.source_constraints["force_web_browse"] is True
    assert plan.source_constraints["task_type"] == "compare"
    assert plan.source_constraints["query_strategy"] == "generic_web_compare"


def test_compare_query_resolves_to_generic_search():
    resolver = _resolver()
    extractor = EntityExtractor()
    text = "M3 vs M2 区别"
    result = resolver.resolve(
        raw_text=text,
        normalized_text=text,
        intent_hint="explanation",
        entities=extractor.extract(text),
        session_context=SessionContext(session_id="default"),
        previous_structured={"bundle_name": "chat"},
        followup_target="",
        followup_focus_value="",
    )
    assert result.capability == "generic_search"
    assert result.validated_slots.get("query") == text


def test_forced_browse_executes_source_discovery_open_and_decision():
    planner = _planner()
    decision = WebAccessDecision(needs_web=True, access_mode="http_fetch", intent_type="general_web_research")
    plan = planner.build(query="Stripe 官网是干嘛的", decision=decision, slots={}, context={})
    search = _SearchSpy(
        {
            "Stripe 官网是干嘛的": [
                {"url": "https://stripe.com/about", "title": "Stripe", "snippet": "About Stripe"},
            ]
        }
    )
    browser = _FakeBrowseBrowser(
        {
            "https://stripe.com/about": {
                "title": "Stripe",
                "page_type": "generic",
                "visible_text": "Stripe provides payment and financial infrastructure for the internet. Businesses use Stripe to accept payments and manage revenue.",
                "headings": ["Stripe", "Financial infrastructure for the internet"],
                "links": [],
            }
        }
    )
    ladder = ExecutionLadder(
        search_tool=search,
        browser_executor=browser,
        browse_policy_callback=lambda **_: {
            "task_type": "general_info",
            "page_type": "generic",
            "action": "stop",
            "candidate_link_ids": [],
            "selected_link_id": None,
            "stop_reason": "enough_info",
            "confidence": 0.9,
        },
    )
    execution = ladder.execute(decision=decision, plan=plan, session_web_context={})
    assert execution.success is True
    assert browser.opened == ["https://stripe.com/about"]
    assert search.calls == ["Stripe 官网是干嘛的"]
    assert execution.browser_result["source_discovery_completed"] is True
    assert execution.browser_result["page_open_attempted"] is True
    assert execution.browser_result["model_decision_emitted"] is True


def test_invalid_model_output_falls_back_to_link_selection():
    planner = _planner()
    decision = WebAccessDecision(needs_web=True, access_mode="http_fetch", intent_type="general_web_research")
    plan = planner.build(query="Tesla Model 3 配置", decision=decision, slots={}, context={})
    search = _SearchSpy(
        {
            "Tesla Model 3 配置": [
                {"url": "https://www.tesla.com/model3", "title": "Tesla Model 3", "snippet": "Model 3"},
            ]
        }
    )
    browser = _FakeBrowseBrowser(
        {
            "https://www.tesla.com/model3": {
                "title": "Tesla Model 3",
                "page_type": "product",
                "visible_text": "Model 3. Learn more about the vehicle.",
                "headings": ["Model 3"],
                "links": [{"text": "Specs", "url": "https://www.tesla.com/model3/specs"}],
            },
            "https://www.tesla.com/model3/specs": {
                "title": "Model 3 Specs",
                "page_type": "docs",
                "visible_text": "Range 629 km. Battery 75 kWh. Dimensions 4694 mm.",
                "headings": ["Model 3 Specs", "Range", "Battery", "Dimensions"],
                "links": [],
                "key_values": ["Range=629 km", "Battery=75 kWh", "Dimensions=4694 mm"],
            },
        }
    )
    ladder = ExecutionLadder(search_tool=search, browser_executor=browser, browse_policy_callback=lambda **_: {})
    execution = ladder.execute(decision=decision, plan=plan, session_web_context={})
    assert execution.success is True
    assert browser.opened == ["https://www.tesla.com/model3", "https://www.tesla.com/model3/specs"]
    assert execution.browser_result["final_page_type"] == "docs"
    assert execution.browser_result["model_decision_emitted"] is True


def test_forced_browse_uses_revised_query_for_search_again():
    planner = _planner()
    decision = WebAccessDecision(needs_web=True, access_mode="http_fetch", intent_type="general_web_research")
    plan = planner.build(query="某官网今天有什么新消息", decision=decision, slots={}, context={})
    search = _SearchSpy(
        {
            "某官网今天有什么新消息": [
                {"url": "https://example.com/", "title": "Example", "snippet": "Home"},
            ],
            "某官网今天有什么新消息 latest news": [
                {"url": "https://example.com/news", "title": "Example News", "snippet": "News"},
            ],
        }
    )
    browser = _FakeBrowseBrowser(
        {
            "https://example.com/": {
                "title": "Example",
                "page_type": "homepage",
                "visible_text": "Welcome to Example.",
                "headings": ["Example"],
                "links": [],
            },
            "https://example.com/news": {
                "title": "Example News",
                "page_type": "news",
                "visible_text": "Headline one 2026. Headline two 2026. Headline three 2026.",
                "headings": ["Headline one", "Headline two", "Headline three", "Headline four"],
                "links": [{"text": "Headline one", "url": "https://example.com/news/1"}],
            },
        }
    )
    ladder = ExecutionLadder(
        search_tool=search,
        browser_executor=browser,
        browse_policy_callback=lambda **kwargs: {
            "task_type": "news",
            "page_type": str(kwargs.get("page_type_guess") or "generic"),
            "action": "search_again" if "example.com/" in str(kwargs.get("page_snapshot", {}).get("url") or "") else "stop",
            "candidate_link_ids": [],
            "selected_link_id": None,
            "stop_reason": "enough_info",
            "confidence": 0.9,
        },
    )
    execution = ladder.execute(decision=decision, plan=plan, session_web_context={})
    assert execution.success is True
    assert search.calls == ["某官网今天有什么新消息", "某官网今天有什么新消息 latest news"]
    assert execution.browser_result["search_again_used"] is True
    assert execution.browser_result["revised_query"] == "某官网今天有什么新消息 latest news"


def test_forced_browse_fails_with_browse_source_discovery_failed():
    planner = _planner()
    decision = WebAccessDecision(needs_web=True, access_mode="http_fetch", intent_type="general_web_research")
    plan = planner.build(query="Tesla Model 3 配置", decision=decision, slots={}, context={})
    search = _SearchSpy({})
    browser = _FakeBrowseBrowser({})
    ladder = ExecutionLadder(search_tool=search, browser_executor=browser, browse_policy_callback=lambda **_: {})
    execution = ladder.execute(decision=decision, plan=plan, session_web_context={})
    assert execution.success is False
    assert execution.failure_reason == "browse_entry_open_failed"
    assert execution.browser_result["search_queries_attempted"]
    assert execution.browser_result["raw_search_results_count"] == 0
    assert execution.browser_result["filtered_search_results_count"] == 0
    assert execution.browser_result["source_discovery_completed"] is True
    assert execution.browser_result["page_open_attempted"] is True
    assert "chosen_entry_url" in execution.browser_result


def test_forced_browse_uses_search_results_page_fallback_when_search_empty():
    planner = _planner()
    decision = WebAccessDecision(needs_web=True, access_mode="http_fetch", intent_type="general_web_research")
    query = "Stripe 官网是干嘛的"
    plan = planner.build(query=query, decision=decision, slots={}, context={})
    search = _SearchSpy({})
    search_url = f"https://www.bing.com/search?q={quote_plus(query)}"
    browser = _FakeBrowseBrowser(
        {
            search_url: {
                "title": "Stripe Search",
                "page_type": "generic",
                "visible_text": "Search results for Stripe.",
                "headings": ["Search results"],
                "links": [{"text": "Stripe", "url": "https://stripe.com/"}],
            },
            "https://stripe.com/": {
                "title": "Stripe",
                "page_type": "generic",
                "visible_text": "Stripe provides payment and financial infrastructure for the internet.",
                "headings": ["Stripe", "Financial infrastructure for the internet"],
                "links": [],
            },
        }
    )
    ladder = ExecutionLadder(
        search_tool=search,
        browser_executor=browser,
        browse_policy_callback=lambda **kwargs: {
            "task_type": "general_info",
            "page_type": str(kwargs.get("page_type_guess") or "generic"),
            "action": "open_link" if "bing.com/search" in str(kwargs.get("page_snapshot", {}).get("url") or "") else "stop",
            "candidate_link_ids": [0] if "bing.com/search" in str(kwargs.get("page_snapshot", {}).get("url") or "") else [],
            "selected_link_id": 0 if "bing.com/search" in str(kwargs.get("page_snapshot", {}).get("url") or "") else None,
            "stop_reason": "enough_info",
            "confidence": 0.9,
        },
    )
    execution = ladder.execute(decision=decision, plan=plan, session_web_context={})
    assert execution.success is True
    assert execution.browser_result["source_discovery_completed"] is True
    assert execution.browser_result["page_open_attempted"] is True
    assert execution.browser_result["model_decision_emitted"] is True
    assert execution.browser_result["chosen_entry_url"] == search_url
    assert browser.opened[0] == search_url
    assert browser.opened[-1] == "https://stripe.com/"


def test_forced_browse_uses_serp_backup_when_primary_candidates_cannot_open():
    planner = _planner()
    decision = WebAccessDecision(needs_web=True, access_mode="http_fetch", intent_type="general_web_research")
    query = "Tesla Model 3 \u914d\u7f6e"
    plan = planner.build(query=query, decision=decision, slots={}, context={})
    first_query = (plan.primary_queries[:1] or [query])[0]
    search = _SearchSpy(
        {
            first_query: [
                {"url": "https://www.tesla.com/model3", "title": "Tesla Model 3", "snippet": "Model 3"},
                {"url": "https://www.tesla.com/compare", "title": "Compare | Tesla", "snippet": "Compare Tesla vehicles"},
            ]
        }
    )
    search_url = f"https://www.bing.com/search?q={quote_plus(first_query)}"
    browser = _FakeBrowseBrowser(
        {
            search_url: {
                "title": "Tesla Search",
                "page_type": "generic",
                "visible_text": "Search results for Tesla Model 3 specs.",
                "headings": ["Search results"],
                "links": [{"text": "Model 3 Specs", "url": "https://example.com/model3-specs"}],
                "serp_detected": True,
                "serp_links_extracted_count": 1,
            },
            "https://example.com/model3-specs": {
                "title": "Model 3 Specs",
                "page_type": "docs",
                "visible_text": "Range 629 km. Battery 75 kWh. Dimensions 4694 mm.",
                "headings": ["Model 3 Specs", "Range", "Battery", "Dimensions"],
                "links": [],
                "key_values": ["Range=629 km", "Battery=75 kWh", "Dimensions=4694 mm"],
            },
        }
    )
    ladder = ExecutionLadder(
        search_tool=search,
        browser_executor=browser,
        browse_policy_callback=lambda **kwargs: {
            "task_type": "specs",
            "page_type": str(kwargs.get("page_type_guess") or "generic"),
            "action": "open_link" if "bing.com/search" in str(kwargs.get("page_snapshot", {}).get("url") or "") else "stop",
            "candidate_link_ids": [0] if "bing.com/search" in str(kwargs.get("page_snapshot", {}).get("url") or "") else [],
            "selected_link_id": 0 if "bing.com/search" in str(kwargs.get("page_snapshot", {}).get("url") or "") else None,
            "stop_reason": "enough_info",
            "confidence": 0.9,
        },
    )
    execution = ladder.execute(decision=decision, plan=plan, session_web_context={})
    assert execution.success is True
    assert execution.browser_result["page_open_attempted"] is True
    assert execution.browser_result["model_decision_emitted"] is True
    assert search_url in browser.opened
    assert browser.opened[-1] == "https://example.com/model3-specs"


def test_standalone_specs_query_blocks_followup_topic_default():
    resolver = _resolver()
    extractor = EntityExtractor()
    text = "Tesla Model 3 \u914d\u7f6e"
    context = SessionContext(session_id="default", last_capability="generic_search", active_topic="Stripe \u5b98\u7f51\u662f\u5e72\u561b\u7684")
    result = resolver.resolve(
        raw_text=text,
        normalized_text=text,
        intent_hint="generic_search",
        entities=extractor.extract(text),
        session_context=context,
        previous_structured={"bundle_name": "web-research"},
        followup_target="",
        followup_focus_value="",
    )
    assert result.validated_slots.get("query") == text
    assert "topic" not in result.validated_slots
    assert not any(
        item.get("slot") == "topic" and item.get("source") == "followup_context"
        for item in result.carryover_trace
    )


def test_standalone_web_query_sanitizer_drops_followup_topic():
    svc = _service()
    sanitized = svc._sanitize_web_resolution_slots(
        raw_query="Tesla Model 3 \u914d\u7f6e",
        resolved_capability="generic_search",
        slots={"query": "Tesla Model 3 \u914d\u7f6e", "topic": "Stripe \u5b98\u7f51\u662f\u5e72\u561b\u7684"},
        slot_sources={"query": "normalized_query", "topic": "followup_context"},
        query_debug={
            "query_authority": "raw_query",
            "topic_carryover_applied": True,
            "source_constraint_applied": False,
        },
    )
    assert sanitized == {"query": "Tesla Model 3 \u914d\u7f6e"}


def test_forced_browse_records_source_discovery_diagnostics():
    planner = _planner()
    decision = WebAccessDecision(needs_web=True, access_mode="http_fetch", intent_type="general_web_research")
    plan = planner.build(query="Stripe \u5b98\u7f51\u662f\u5e72\u561b\u7684", decision=decision, slots={}, context={})
    search = _SearchSpy(
        {
            "Stripe \u5b98\u7f51\u662f\u5e72\u561b\u7684": [
                {"url": "https://stripe.com/about", "title": "Stripe", "snippet": "About Stripe"},
            ]
        }
    )
    browser = _FakeBrowseBrowser(
        {
            "https://stripe.com/about": {
                "title": "Stripe",
                "page_type": "generic",
                "visible_text": "Stripe provides payment and financial infrastructure for the internet.",
                "headings": ["Stripe", "Financial infrastructure for the internet"],
                "links": [],
            }
        }
    )
    ladder = ExecutionLadder(
        search_tool=search,
        browser_executor=browser,
        browse_policy_callback=lambda **_: {
            "task_type": "general_info",
            "page_type": "generic",
            "action": "stop",
            "candidate_link_ids": [],
            "selected_link_id": None,
            "stop_reason": "enough_info",
            "confidence": 0.9,
        },
    )
    execution = ladder.execute(decision=decision, plan=plan, session_web_context={})
    assert execution.success is True
    assert execution.browser_result["search_queries_attempted"] == ["Stripe \u5b98\u7f51\u662f\u5e72\u561b\u7684"]
    assert execution.browser_result["raw_search_results_count"] == 1
    assert execution.browser_result["filtered_search_results_count"] == 1
    assert execution.browser_result["chosen_entry_url"] == "https://stripe.com/about"
    assert execution.browser_result["chosen_source_candidates"][0]["url"] == "https://stripe.com/about"


def test_sse_event_preserves_utf8_text_delta_roundtrip():
    from app.api.routes.chat import _format_sse_event

    payload = {"event": "text_delta", "text": "\u4e2d\u6587\u53c2\u6570\u5bf9\u6bd4"}
    encoded = _format_sse_event("text_delta", payload).encode("utf-8")
    decoded = encoded.decode("utf-8")
    assert "\u4e2d\u6587\u53c2\u6570\u5bf9\u6bd4" in decoded


def _execute_serp_ranking_flow(
    *,
    query: str,
    task_type: str,
    serp_links: list[dict[str, str]],
    final_pages: dict[str, dict],
):
    planner = _planner()
    decision = WebAccessDecision(needs_web=True, access_mode="http_fetch", intent_type="general_web_research")
    plan = planner.build(query=query, decision=decision, slots={}, context={})
    plan.source_constraints["task_type"] = task_type
    plan.source_constraints["force_web_browse"] = True
    plan.source_constraints["browse_strategy"] = "forced_web_browse"
    search = _SearchSpy({})
    browser_pages: dict[str, dict] = {}
    search_urls = []
    for search_query in [*list(plan.primary_queries or []), *list(plan.fallback_queries or [])]:
        url = f"https://www.bing.com/search?q={quote_plus(search_query)}"
        if url in browser_pages:
            continue
        search_urls.append(url)
        browser_pages[url] = {
            "title": "Search results",
            "page_type": "generic",
            "visible_text": "search results page",
            "headings": ["Search results"],
            "links": serp_links,
            "serp_detected": True,
            "serp_links_extracted_count": len(serp_links),
        }
    search_url = search_urls[0]
    browser_pages.update(final_pages)
    browser = _FakeBrowseBrowser(browser_pages)

    def _policy(**kwargs):
        current_url = str(kwargs.get("page_snapshot", {}).get("url") or "")
        if "bing.com/search" in current_url:
            return {
                "task_type": str(kwargs.get("task_type") or "general_info"),
                "page_type": str(kwargs.get("page_type_guess") or "generic"),
                "action": "search_again",
                "candidate_link_ids": [],
                "selected_link_id": None,
                "stop_reason": "low_relevance",
                "confidence": 0.9,
            }
        return {
            "task_type": str(kwargs.get("task_type") or "general_info"),
            "page_type": str(kwargs.get("page_type_guess") or "generic"),
            "action": "stop",
            "candidate_link_ids": [],
            "selected_link_id": None,
            "stop_reason": "enough_info",
            "confidence": 0.9,
        }

    ladder = ExecutionLadder(search_tool=search, browser_executor=browser, browse_policy_callback=_policy)
    execution = ladder.execute(decision=decision, plan=plan, session_web_context={})
    return search_url, browser, execution


def test_serp_ranker_prefers_general_info_about_page():
    query = "Stripe 官网是干嘛的"
    search_url, browser, execution = _execute_serp_ranking_flow(
        query=query,
        task_type="general_info",
        serp_links=[
            {"text": "Stripe Newsroom", "url": "https://stripe.com/newsroom"},
            {"text": "About Stripe", "url": "https://stripe.com/about"},
            {"text": "Stripe Docs", "url": "https://docs.stripe.com/"},
        ],
        final_pages={
            "https://stripe.com/about": {
                "title": "About Stripe",
                "page_type": "generic",
                "visible_text": "Stripe provides financial infrastructure for the internet and helps businesses accept payments, run billing, and manage online commerce.",
                "headings": ["About Stripe"],
                "links": [],
            }
        },
    )
    assert execution.success is True
    assert browser.opened[0] == search_url
    assert browser.opened[-1] == "https://stripe.com/about"
    assert execution.browser_result["serp_detected"] is True
    assert execution.browser_result["top_ranked_candidate"]["url"] == "https://stripe.com/about"
    assert execution.browser_result["final_selected_candidate"]["url"] == "https://stripe.com/about"
    assert execution.browser_result["serp_selection_reason"] in {"top_ranked_candidate", "top_ranked_override"}


def test_serp_ranker_prefers_specs_page_over_homepage():
    query = "Tesla Model 3 配置"
    search_url, browser, execution = _execute_serp_ranking_flow(
        query=query,
        task_type="specs",
        serp_links=[
            {"text": "Tesla", "url": "https://www.tesla.com/"},
            {"text": "Model 3", "url": "https://www.tesla.com/model3"},
            {"text": "Model 3 Specs", "url": "https://www.tesla.com/model3/specs"},
        ],
        final_pages={
            "https://www.tesla.com/model3/specs": {
                "title": "Model 3 Specs",
                "page_type": "docs",
                "visible_text": "Battery Range Dimensions Weight Wheel Tire Cargo.",
                "headings": ["Model 3 Specs"],
                "table_rows": ["Battery", "Range", "Dimensions"],
                "links": [],
            }
        },
    )
    assert execution.success is True
    assert browser.opened[0] == search_url
    assert browser.opened[-1] == "https://www.tesla.com/model3/specs"
    assert execution.browser_result["top_ranked_candidate"]["url"] == "https://www.tesla.com/model3/specs"
    assert execution.browser_result["final_selected_candidate"]["url"] == "https://www.tesla.com/model3/specs"


def test_serp_ranker_prefers_newsroom_page_for_news_task():
    query = "某官网今天有什么新消息"
    search_url, browser, execution = _execute_serp_ranking_flow(
        query=query,
        task_type="news",
        serp_links=[
            {"text": "Example", "url": "https://example.com/"},
            {"text": "Example Newsroom", "url": "https://example.com/newsroom"},
            {"text": "Example Blog", "url": "https://example.com/blog"},
        ],
        final_pages={
            "https://example.com/newsroom": {
                "title": "Example Newsroom",
                "page_type": "news",
                "visible_text": "Latest announcements. Story one. Story two. Story three.",
                "headings": ["Latest announcements", "Story one", "Story two", "Story three"],
                "links": [],
            }
        },
    )
    assert execution.success is True
    assert browser.opened[0] == search_url
    assert browser.opened[-1] == "https://example.com/newsroom"
    assert execution.browser_result["top_ranked_candidate"]["url"] == "https://example.com/newsroom"
    assert execution.browser_result["final_selected_candidate"]["url"] == "https://example.com/newsroom"


def test_serp_ranker_prefers_compare_page_for_compare_task():
    query = "M3 vs M2 区别"
    search_url, browser, execution = _execute_serp_ranking_flow(
        query=query,
        task_type="compare",
        serp_links=[
            {"text": "Apple", "url": "https://www.apple.com/"},
            {"text": "Mac compare", "url": "https://www.apple.com/mac/compare/"},
            {"text": "MacBook Air", "url": "https://www.apple.com/macbook-air/"},
        ],
        final_pages={
            "https://www.apple.com/mac/compare/": {
                "title": "Mac compare",
                "page_type": "docs",
                "visible_text": "Compare Mac models and chips including M3 and M2.",
                "headings": ["Compare Mac models"],
                "table_rows": ["M3", "M2", "CPU", "GPU"],
                "links": [],
            }
        },
    )
    assert execution.success is True
    assert browser.opened[0] == search_url
    assert browser.opened[-1] == "https://www.apple.com/mac/compare/"
    assert execution.browser_result["top_ranked_candidate"]["url"] == "https://www.apple.com/mac/compare/"
    assert execution.browser_result["final_selected_candidate"]["url"] == "https://www.apple.com/mac/compare/"
