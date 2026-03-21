from __future__ import annotations

import logging
import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import quote_plus, urlparse

import requests

from app.capabilities.browser_capability import BrowserCapability
from app.models.skill_result import SkillResult
from app.models.tool_result import ToolResult
from app.skills.skill_spec import SkillSpec
from skills.search_web import normalize_search_result_url


logger = logging.getLogger(__name__)

_QUERY_PREFIXES = (
    "帮我上网",
    "帮我联网",
    "帮我去",
    "帮我",
    "请帮我",
    "请",
    "麻烦",
    "上网",
    "联网",
    "网找一下",
    "找一下",
    "查一下",
    "查查",
    "搜一下",
    "搜索一下",
)
_LEADING_FILLERS = ("去", "到", "在", "给我", "替我")
_ACTION_FILLERS = (
    "查查",
    "查一下",
    "查下",
    "查",
    "搜一下",
    "搜索一下",
    "搜索",
    "搜搜",
    "看看",
    "看下",
    "看一下",
    "帮我",
    "请帮我",
    "请",
    "麻烦",
    "去",
)
_DISTANCE_SUFFIXES = (
    "是多少公里",
    "多少公里",
    "距离是多少",
    "距离",
    "多远",
    "公里",
    "千米",
    "km",
)
_DISTANCE_CONNECTORS = ("到", "至", "和", "与", " to ")
_DISTANCE_INTENT_MARKERS = ("距离", "多远", "多少公里", "公里", "千米", "km")
_TRAVEL_MARKERS = (
    "机票",
    "航班",
    "票价",
    "价格",
    "酒店",
    "路线",
    "怎么去",
    "怎样去",
    "航空公司",
)
_PRICE_MARKERS = ("多少钱", "价格", "售价", "报价", "折扣", "优惠")
_METRIC_MARKERS = ("粉丝", "粉丝数", "followers", "subscriber", "subscribe")
_INTERACTIVE_MARKERS = ("登录", "注册", "提交", "填写", "输入", "点击", "翻页", "滚动")
_WEATHER_MARKERS = ("天气", "气温", "温度", "预报", "下雨", "降雨", "weather", "forecast")
_NEWS_MARKERS = ("新闻", "科技新闻", "最新", "实时", "今日", "今天", "快讯", "动态", "news")
_SITE_MARKERS = {
    "bilibili": ("哔哩哔哩", "哔站", "b站", "bilibili"),
    "apple": ("apple", "苹果"),
}
_SITE_DOMAINS = {
    "bilibili": ["bilibili.com", "space.bilibili.com", "search.bilibili.com"],
    "apple": ["apple.com", "www.apple.com"],
}
_SEARCH_ENGINE_HOSTS = ("bing.com", "duckduckgo.com", "google.com")
SITE_ADAPTERS = {
    "bilibili": "_handle_bilibili_metric_lookup",
}


@dataclass(slots=True)
class WebResearchIntent:
    intent_type: str
    normalized_query: str
    target_site: str = ""
    target_entity: str = ""
    target_metric: str = ""
    constraints: dict[str, Any] = field(default_factory=dict)
    allow_distance_fallback: bool = False


@dataclass(slots=True)
class NavigationCandidate:
    url: str
    source: str
    title: str = ""
    snippet: str = ""


@dataclass(slots=True)
class NavigationPlan:
    intent: WebResearchIntent
    target_domains: list[str] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=list)
    candidate_urls: list[NavigationCandidate] = field(default_factory=list)
    missing_constraints: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    max_candidates: int = 4


class WebResearchSkill:
    SPEC = SkillSpec(
        name="web_research_skill",
        description="Plan navigation, browse target pages, and extract structured web information.",
        trigger_hints=("上网", "联网", "搜索", "网页", "网站", "官网", "价格", "粉丝", "链接"),
        allowed_tools=(
            "search_web",
            "open_url",
            "extract_page_text",
            "extract_structured_fields",
            "snapshot_page",
            "compare_structured_results",
        ),
        execution_steps=(
            "classify_intent",
            "plan_navigation",
            "discover_candidates",
            "browse_targets",
            "extract_structured_fields",
            "summarize",
        ),
        output_schema=("summary", "compared_items", "differences", "recommendation", "sources"),
    )

    def __init__(self, browser: BrowserCapability, llm_helper: Any) -> None:
        self.browser = browser
        self.llm_helper = llm_helper

    def execute(self, user_request: str, allowed_tools: list[str], *, memory_context: str = "") -> SkillResult:
        intent = self._classify_intent(user_request)
        plan = self._plan_navigation(intent)
        tool_results: list[ToolResult] = [
            ToolResult("intent_classification", ok=True, data=asdict(intent)),
            ToolResult("navigation_plan", ok=True, data=self._plan_to_dict(plan)),
        ]
        pages: list[dict[str, Any]] = []
        sources: list[dict[str, str]] = []
        search_diagnostics: list[dict[str, Any]] = []
        blocked_pages: list[dict[str, str]] = []
        adapter_summary: dict[str, Any] | None = None

        if intent.intent_type == "distance_lookup" and intent.allow_distance_fallback:
            distance_fallback = self._try_distance_fallback(intent)
            if distance_fallback is not None:
                tool_results.append(ToolResult("distance_lookup", ok=True, data=distance_fallback))
                return SkillResult(
                    skill_name=self.SPEC.name,
                    success=True,
                    summary=distance_fallback["summary"],
                    structured={
                        "intent": asdict(intent),
                        "plan": self._plan_to_dict(plan),
                        **distance_fallback,
                    },
                    recommendation=distance_fallback["recommendation"],
                    sources=distance_fallback["sources"],
                    tool_results=tool_results,
                    response_text=distance_fallback["response_text"],
                )

        if intent.intent_type == "weather_lookup":
            weather_result = self._try_weather_lookup(intent)
            if weather_result is not None:
                tool_results.append(ToolResult("weather_lookup", ok=True, data=weather_result))
                return SkillResult(
                    skill_name=self.SPEC.name,
                    success=True,
                    summary=weather_result["summary"],
                    structured={
                        "intent": asdict(intent),
                        "plan": self._plan_to_dict(plan),
                        **weather_result,
                    },
                    recommendation=weather_result["recommendation"],
                    sources=weather_result["sources"],
                    tool_results=tool_results,
                    response_text=weather_result["response_text"],
                )
            if not str(intent.constraints.get("location", "") or "").strip():
                message = {
                    "summary": "我已识别为天气查询，但当前没能拿到你的近似定位。",
                    "recommendation": "请直接告诉我要查哪个城市，例如“墨尔本今天天气怎么样”。",
                    "response_text": "请求已接收。我已识别为天气查询，但当前没能拿到你的近似定位。请直接告诉我要查哪个城市，我再继续给你天气结果。",
                }
                return SkillResult(
                    skill_name=self.SPEC.name,
                    success=False,
                    summary=message["summary"],
                    structured={"intent": asdict(intent), "plan": self._plan_to_dict(plan)},
                    recommendation=message["recommendation"],
                    sources=[],
                    tool_results=tool_results,
                    response_text=message["response_text"],
                )

        if plan.missing_constraints:
            message = self._build_missing_constraint_response(intent, plan)
            return SkillResult(
                skill_name=self.SPEC.name,
                success=False,
                summary=message["summary"],
                structured={"intent": asdict(intent), "plan": self._plan_to_dict(plan)},
                recommendation=message["recommendation"],
                sources=[],
                tool_results=tool_results,
                response_text=message["response_text"],
            )

        candidates = self._collect_candidates(plan, allowed_tools, tool_results, search_diagnostics)
        logger.info("search_result_count count=%s", len(candidates))
        if not candidates:
            message = self._build_failure_response(intent, blocked_pages)
            return SkillResult(
                skill_name=self.SPEC.name,
                success=False,
                summary=message["summary"],
                structured={
                    "intent": asdict(intent),
                    "plan": self._plan_to_dict(plan),
                    "blocked_pages": blocked_pages,
                    "search_diagnostics": search_diagnostics,
                },
                recommendation=message["recommendation"],
                sources=[],
                tool_results=tool_results,
                response_text=message["response_text"],
            )

        for candidate in candidates[: plan.max_candidates]:
            page_item = self._open_candidate(candidate, intent, allowed_tools, tool_results, blocked_pages)
            if page_item is None:
                continue
            pages.append(page_item)
            sources.append({"title": str(page_item.get("title", "")), "url": str(page_item.get("url", ""))})
            adapter_summary = self._run_site_adapter(intent, page_item)
            if adapter_summary is not None:
                break

        logger.info("opened_url_count count=%s", len(pages))
        if intent.intent_type == "site_metric_lookup" and adapter_summary is None:
            message = self._build_failure_response(intent, blocked_pages)
            return SkillResult(
                skill_name=self.SPEC.name,
                success=False,
                summary=message["summary"],
                structured={
                    "intent": asdict(intent),
                    "plan": self._plan_to_dict(plan),
                    "blocked_pages": blocked_pages,
                    "search_diagnostics": search_diagnostics,
                    "pages": pages,
                },
                recommendation=message["recommendation"],
                sources=sources,
                tool_results=tool_results,
                response_text=message["response_text"],
            )

        if not pages:
            message = self._build_failure_response(intent, blocked_pages)
            return SkillResult(
                skill_name=self.SPEC.name,
                success=False,
                summary=message["summary"],
                structured={
                    "intent": asdict(intent),
                    "plan": self._plan_to_dict(plan),
                    "blocked_pages": blocked_pages,
                    "search_diagnostics": search_diagnostics,
                },
                recommendation=message["recommendation"],
                sources=[],
                tool_results=tool_results,
                response_text=message["response_text"],
            )

        comparison: dict[str, Any] = {}
        if len(pages) > 1 and intent.intent_type not in {"site_metric_lookup", "document_read"}:
            comparison = self.browser.compare(pages, allowed_tools=allowed_tools, focus=user_request)
            if comparison:
                tool_results.append(ToolResult("compare_structured_results", ok=True, data=comparison))
                logger.info("comparison_item_count count=%s", len(comparison.get("items", [])))

        synthesis = adapter_summary or self.llm_helper.summarize_web_result(user_request, pages, comparison, memory_context=memory_context)
        return SkillResult(
            skill_name=self.SPEC.name,
            success=True,
            summary=synthesis.get("summary", ""),
            structured={
                "intent": asdict(intent),
                "plan": self._plan_to_dict(plan),
                "summary": synthesis.get("summary", ""),
                "compared_items": comparison.get("items", []),
                "differences": comparison.get("differences", []),
                "recommendation": synthesis.get("recommendation", comparison.get("recommendation", "")),
                "sources": sources,
                "blocked_pages": blocked_pages,
                "pages": pages,
            },
            recommendation=synthesis.get("recommendation", comparison.get("recommendation", "")),
            sources=sources,
            tool_results=tool_results,
            response_text=synthesis.get("response_text", ""),
        )

    def _classify_intent(self, user_request: str) -> WebResearchIntent:
        normalized_query = self._normalize_query(user_request)
        urls = re.findall(r"https?://\S+", user_request)
        site_key = self._site_key(normalized_query)
        route_pair = self._extract_route_pair(normalized_query)
        date_constraints = self._extract_date_constraints(normalized_query)
        lowered = normalized_query.lower()

        if urls:
            return WebResearchIntent(
                intent_type="document_read",
                normalized_query=normalized_query,
                constraints={"urls": urls[:4]},
                allow_distance_fallback=False,
            )
        if self._is_distance_request(normalized_query):
            return WebResearchIntent(
                intent_type="distance_lookup",
                normalized_query=normalized_query,
                target_entity=" -> ".join(route_pair) if route_pair else normalized_query,
                constraints={"route": route_pair} if route_pair else {},
                allow_distance_fallback=True,
            )
        if self._is_weather_request(normalized_query):
            location = self._extract_weather_location(normalized_query)
            return WebResearchIntent(
                intent_type="weather_lookup",
                normalized_query=normalized_query,
                target_entity=location,
                target_metric="weather",
                constraints={
                    "location": location,
                    "day_offset": self._extract_weather_day_offset(normalized_query),
                },
                allow_distance_fallback=False,
            )
        if site_key and any(marker in lowered for marker in _METRIC_MARKERS):
            return WebResearchIntent(
                intent_type="site_metric_lookup",
                normalized_query=normalized_query,
                target_site=site_key,
                target_entity=self._extract_site_metric_entity(normalized_query, site_key),
                target_metric="follower_count",
                allow_distance_fallback=False,
            )
        if any(marker in normalized_query for marker in _TRAVEL_MARKERS):
            return WebResearchIntent(
                intent_type="travel_price_lookup",
                normalized_query=normalized_query,
                target_entity=" -> ".join(route_pair) if route_pair else normalized_query,
                target_metric="price",
                constraints={"route": route_pair, "dates": date_constraints},
                allow_distance_fallback=False,
            )
        if any(marker in normalized_query for marker in _PRICE_MARKERS):
            return WebResearchIntent(
                intent_type="product_price_lookup",
                normalized_query=normalized_query,
                target_site=site_key or "",
                target_entity=self._strip_site_terms(normalized_query) if site_key else normalized_query,
                target_metric="price",
                allow_distance_fallback=False,
            )
        if any(marker in normalized_query for marker in _INTERACTIVE_MARKERS):
            return WebResearchIntent(
                intent_type="interactive_site_task",
                normalized_query=normalized_query,
                target_site=site_key or "",
                target_entity=self._strip_site_terms(normalized_query) if site_key else normalized_query,
                allow_distance_fallback=False,
            )
        if any(marker in lowered for marker in _NEWS_MARKERS):
            return WebResearchIntent(
                intent_type="browser_navigation",
                normalized_query=normalized_query,
                target_site=site_key or "",
                target_entity=self._strip_site_terms(normalized_query) if site_key else normalized_query,
                constraints={"news_lookup": True},
                allow_distance_fallback=False,
            )
        if site_key or any(marker in normalized_query for marker in ("打开网页", "进入官网", "浏览一下", "链接")):
            return WebResearchIntent(
                intent_type="browser_navigation",
                normalized_query=normalized_query,
                target_site=site_key or "",
                target_entity=self._strip_site_terms(normalized_query) if site_key else normalized_query,
                allow_distance_fallback=False,
            )
        return WebResearchIntent(
            intent_type="general_fact_lookup",
            normalized_query=normalized_query,
            target_site=site_key or "",
            target_entity=self._strip_site_terms(normalized_query) if site_key else normalized_query,
            allow_distance_fallback=False,
        )

    def _plan_navigation(self, intent: WebResearchIntent) -> NavigationPlan:
        plan = NavigationPlan(intent=intent)
        if intent.intent_type == "document_read":
            for url in intent.constraints.get("urls", []):
                plan.candidate_urls.append(NavigationCandidate(url=url, source="direct-url", title=url))
            plan.notes.append("User provided a direct URL.")
            return plan

        if intent.intent_type == "distance_lookup":
            plan.notes.append("Distance lookup uses coordinate fallback and does not browse websites.")
            return plan

        if intent.intent_type == "weather_lookup":
            location = str(intent.constraints.get("location", "") or "").strip()
            if not location:
                plan.notes.append("Weather lookup will try current approximate location before asking for a city.")
                return plan
            plan.notes.append("Weather lookup uses geocoding plus Open-Meteo forecast API and does not rely on news pages.")
            return plan

        if intent.intent_type == "site_metric_lookup" and intent.target_site == "bilibili":
            keyword = intent.target_entity or intent.normalized_query
            plan.target_domains = list(_SITE_DOMAINS.get("bilibili", []))
            plan.candidate_urls.extend(
                [
                    NavigationCandidate(
                        url=f"https://search.bilibili.com/upuser?keyword={quote_plus(keyword)}",
                        source="site-direct",
                        title=f"Bilibili UP 搜索: {keyword}",
                    ),
                    NavigationCandidate(
                        url=f"https://search.bilibili.com/all?keyword={quote_plus(keyword)}",
                        source="site-direct",
                        title=f"Bilibili 综合搜索: {keyword}",
                    ),
                ]
            )
            plan.search_queries.extend(
                [
                    f'site:bilibili.com "{keyword}" 粉丝',
                    f'site:space.bilibili.com "{keyword}" 粉丝数',
                ]
            )
            plan.notes.append("Prefer direct Bilibili search pages over search engine result pages.")
            return plan

        if intent.intent_type == "travel_price_lookup":
            route = intent.constraints.get("route") or ()
            dates = intent.constraints.get("dates") or []
            plan.target_domains = ["trip.com", "ctrip.com", "skyscanner.com", "kayak.com"]
            if not route:
                plan.missing_constraints.append("出发地和目的地")
                return plan
            if not dates:
                plan.missing_constraints.append("出行日期")
                plan.notes.append("Travel price lookup needs a date before browsing flight sites.")
                return plan
            start, end = route
            plan.search_queries.extend(
                [
                    f"{start} {end} 机票 {dates[0]}",
                    f"site:trip.com {start} {end} 机票 {dates[0]}",
                    f"site:skyscanner.com {start} {end} flight {dates[0]}",
                ]
            )
            return plan

        if intent.intent_type == "product_price_lookup":
            entity = intent.target_entity or intent.normalized_query
            plan.search_queries.extend([entity, f"{entity} 价格", f"{entity} 官方参数"])
            return plan

        if intent.intent_type == "browser_navigation" and intent.target_site == "bilibili":
            keyword = intent.target_entity or intent.normalized_query
            plan.target_domains = list(_SITE_DOMAINS.get("bilibili", []))
            plan.candidate_urls.append(
                NavigationCandidate(
                    url=f"https://search.bilibili.com/all?keyword={quote_plus(keyword)}",
                    source="site-direct",
                    title=f"Bilibili 综合搜索: {keyword}",
                )
            )
            plan.search_queries.append(f"site:bilibili.com {keyword}")
            return plan

        if intent.intent_type == "browser_navigation" and intent.constraints.get("news_lookup"):
            plan.notes.append("Prefer direct destination pages for news requests instead of search engine result pages.")
            if intent.target_site == "apple":
                plan.target_domains = list(_SITE_DOMAINS.get("apple", []))
                plan.candidate_urls.extend(
                    [
                        NavigationCandidate(
                            url="https://www.apple.com/newsroom/",
                            source="site-direct",
                            title="Apple Newsroom",
                        ),
                        NavigationCandidate(
                            url="https://www.apple.com/newsroom/archive/",
                            source="site-direct",
                            title="Apple Newsroom Archive",
                        ),
                    ]
                )
                plan.max_candidates = 2
                return plan

        plan.search_queries.append(intent.normalized_query)
        if intent.target_site:
            plan.target_domains = list(_SITE_DOMAINS.get(intent.target_site, []))
        return plan

    def _collect_candidates(
        self,
        plan: NavigationPlan,
        allowed_tools: list[str],
        tool_results: list[ToolResult],
        search_diagnostics: list[dict[str, Any]],
    ) -> list[NavigationCandidate]:
        candidates: list[NavigationCandidate] = list(plan.candidate_urls)
        seen_urls = {candidate.url for candidate in candidates}
        for query in plan.search_queries[:3]:
            search_payload = self.browser.search(query, allowed_tools=allowed_tools, preferred_domains=plan.target_domains)
            results = list(search_payload.get("results", []))[:6]
            diagnostics = list(search_payload.get("diagnostics", []))
            search_diagnostics.extend(diagnostics)
            tool_results.append(
                ToolResult(
                    "search_web",
                    ok=bool(results),
                    data={"query": query, "results": results, "diagnostics": diagnostics},
                )
            )
            for item in results:
                clean_url = normalize_search_result_url(item)
                if not clean_url or clean_url in seen_urls:
                    continue
                seen_urls.add(clean_url)
                candidates.append(
                    NavigationCandidate(
                        url=clean_url,
                        source=str(item.get("provider", "search")),
                        title=str(item.get("title", "")),
                        snippet=str(item.get("snippet", "")),
                    )
                )
        return candidates

    def _open_candidate(
        self,
        candidate: NavigationCandidate,
        intent: WebResearchIntent,
        allowed_tools: list[str],
        tool_results: list[ToolResult],
        blocked_pages: list[dict[str, str]],
    ) -> dict[str, Any] | None:
        try:
            opened = self.browser.open(candidate.url, allowed_tools=allowed_tools)
            tool_results.append(ToolResult("open_url", ok=True, data={"url": candidate.url, "title": opened.get("title", "")}))
            extracted = self.browser.extract(opened, allowed_tools=allowed_tools)
            tool_results.append(ToolResult("extract_page_text", ok=True, data={"url": candidate.url, "title": extracted.get("title", "")}))
            structured = self.browser.extract_structured_fields(
                opened,
                allowed_tools=allowed_tools,
                schema=["entity_name", "follower_count", "price", "rating", "date", "dates", "domain"],
            )
            tool_results.append(
                ToolResult(
                    "extract_structured_fields",
                    ok=True,
                    data={"url": candidate.url, "fields": structured.get("fields", {})},
                )
            )
            snapshot = self.browser.snapshot(opened, allowed_tools=allowed_tools)
            tool_results.append(ToolResult("snapshot_page", ok=True, data={"url": candidate.url, "title": snapshot.get("title", "")}))
        except Exception as exc:  # noqa: BLE001
            tool_results.append(ToolResult("open_url", ok=False, data={"url": candidate.url}, error=str(exc)))
            blocked_pages.append({"url": candidate.url, "reason": str(exc)})
            return None

        final_url = str(opened.get("final_url") or opened.get("url") or candidate.url)
        blocked_reason = str(structured.get("blocked_reason") or extracted.get("blocked_reason") or opened.get("blocked_reason") or "")
        if blocked_reason or self._looks_like_search_engine_page(final_url):
            blocked_pages.append({"url": final_url, "reason": blocked_reason or "search_engine_page"})
            return None

        page_item = {
            "title": str(extracted.get("title", opened.get("title", candidate.title))),
            "url": final_url,
            "body_text": str(extracted.get("body_text", "")),
            "meta_description": str(extracted.get("meta_description", "")),
            "links": list(extracted.get("links", [])),
            "headings": list(extracted.get("headings", [])),
            "structured_signals": self._merge_structured_signals(extracted, structured),
            "snapshot": snapshot,
            "source": candidate.source,
        }
        if not self._is_relevant_page(intent, page_item):
            return None
        return page_item

    def _merge_structured_signals(self, extracted: dict[str, Any], structured: dict[str, Any]) -> dict[str, Any]:
        signals = dict(extracted.get("structured_signals", {}))
        fields = structured.get("fields", {}) if isinstance(structured.get("fields"), dict) else {}
        signals.update(fields)
        return signals

    def _run_site_adapter(self, intent: WebResearchIntent, page_item: dict[str, Any]) -> dict[str, Any] | None:
        adapter_name = SITE_ADAPTERS.get(intent.target_site)
        if not adapter_name:
            return None
        adapter = getattr(self, adapter_name, None)
        if adapter is None:
            return None
        return adapter(intent, page_item)

    def _handle_bilibili_metric_lookup(self, intent: WebResearchIntent, page_item: dict[str, Any]) -> dict[str, Any] | None:
        if intent.target_metric != "follower_count":
            return None
        structured = page_item.get("structured_signals", {}) if isinstance(page_item.get("structured_signals"), dict) else {}
        follower_count = str(structured.get("follower_count", "") or "").strip()
        name = self._extract_bilibili_entity_name(page_item, intent.target_entity)
        if not follower_count:
            follower_count = self._extract_followers_from_body(page_item.get("body_text", ""), name)
        if not follower_count or not name:
            return None
        if intent.target_entity and not self._name_matches(intent.target_entity, name):
            return None
        return {
            "summary": f"已定位到“{name}”。当前识别到的粉丝数约为 {follower_count}。",
            "recommendation": "建议打开对应主页再次确认，因为平台展示数字会随时间变化。",
            "response_text": (
                "请求已接收。"
                f"已定位到“{name}”。当前识别到的粉丝数约为 {follower_count}。"
                "该结果来自当前页面可见信息。"
            ),
        }

    def _extract_bilibili_entity_name(self, page_item: dict[str, Any], target_entity: str) -> str:
        structured = page_item.get("structured_signals", {}) if isinstance(page_item.get("structured_signals"), dict) else {}
        title = str(page_item.get("title", "") or "").strip()
        if title and "搜索" not in title and "search" not in title.lower():
            title = re.sub(r"[-_—|].*$", "", title).strip()
            if title:
                return title
        body_text = str(page_item.get("body_text", "") or "")
        lines = [line.strip() for line in body_text.splitlines() if line.strip()]
        target_lower = target_entity.lower().replace(" ", "")
        for idx, line in enumerate(lines[:-1]):
            next_line = lines[idx + 1]
            if "粉丝" not in next_line:
                continue
            normalized_name = line.lower().replace(" ", "")
            if not target_lower or target_lower in normalized_name:
                return line
        return str(structured.get("entity_name", "") or "").strip()

    def _extract_followers_from_body(self, body_text: str, name: str) -> str:
        lines = [line.strip() for line in str(body_text).splitlines() if line.strip()]
        normalized_name = name.lower().replace(" ", "")
        for idx, line in enumerate(lines):
            if normalized_name and normalized_name not in line.lower().replace(" ", ""):
                continue
            next_lines = lines[idx : idx + 3]
            for candidate in next_lines:
                match = re.search(r"(\d+(?:\.\d+)?(?:万|亿)?)\s*粉丝", candidate)
                if match:
                    return match.group(1)
        match = re.search(r"(\d+(?:\.\d+)?(?:万|亿)?)\s*粉丝", body_text)
        return match.group(1) if match else ""

    def _is_relevant_page(self, intent: WebResearchIntent, page_item: dict[str, Any]) -> bool:
        url = str(page_item.get("url", "") or "")
        if self._looks_like_search_engine_page(url):
            return False
        domain = urlparse(url).netloc.lower()
        if intent.target_site == "bilibili" and "bilibili.com" not in domain:
            return False
        if intent.target_site == "apple":
            if "apple.com" not in domain:
                return False
            if intent.constraints.get("news_lookup"):
                title = str(page_item.get("title", "") or "").lower()
                if "newsroom" not in url.lower() and "newsroom" not in title:
                    return False
        if intent.target_entity and intent.intent_type == "site_metric_lookup":
            title = str(page_item.get("title", "") or "")
            body_text = str(page_item.get("body_text", "") or "")
            haystack = (title + "\n" + body_text[:1800]).lower().replace(" ", "")
            needle = intent.target_entity.lower().replace(" ", "")
            if needle and needle not in haystack:
                return False
        return True

    def _looks_like_search_engine_page(self, url: str) -> bool:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        path = parsed.path.lower()
        if any(token in host for token in _SEARCH_ENGINE_HOSTS):
            return True
        return "searx" in host or path.startswith("/search") and any(token in host for token in ("google.", "bing.com", "duckduckgo.com"))

    def _build_missing_constraint_response(self, intent: WebResearchIntent, plan: NavigationPlan) -> dict[str, str]:
        if intent.intent_type == "weather_lookup":
            missing = "、".join(plan.missing_constraints) or "城市"
            return {
                "summary": "我已识别为天气查询，但地点还不明确。",
                "recommendation": f"请补充 {missing}，例如“墨尔本今天天气怎么样”。",
                "response_text": f"请求已接收。我已识别为天气查询，但目前缺少 {missing}。请告诉我要查哪个城市，我再直接给你天气结果。",
            }
        if intent.intent_type == "travel_price_lookup":
            missing = "、".join(plan.missing_constraints)
            return {
                "summary": "我已识别为机票价格查询，但条件还不完整。",
                "recommendation": f"请补充 {missing}，例如“4月20日单程”或“往返日期”。",
                "response_text": f"请求已接收。我已识别为机票查询，但目前缺少 {missing}。请补充出行条件后，我再继续打开航班页面核对票价。",
            }
        return {
            "summary": "当前信息不足，暂时无法继续浏览。",
            "recommendation": "请补充更明确的目标或直接提供链接。",
            "response_text": "请求已接收。当前信息不足，暂时无法继续浏览。请补充更明确的目标或直接提供链接。",
        }

    def _build_failure_response(self, intent: WebResearchIntent, blocked_pages: list[dict[str, str]]) -> dict[str, str]:
        if intent.intent_type == "weather_lookup":
            return {
                "summary": "我已识别为天气查询，但暂时没能从天气数据源拿到可靠结果。",
                "recommendation": "建议确认城市名称，或稍后再试一次。",
                "response_text": "请求已接收。我已识别为天气查询，但暂时没能从天气数据源拿到可靠结果。建议确认城市名称，或稍后再试一次。",
            }
        if intent.intent_type == "site_metric_lookup" and intent.target_site == "bilibili":
            return {
                "summary": "我已识别为 Bilibili 指标查询，但没能可靠确认目标账号页面。",
                "recommendation": "建议提供更准确的账号名或直接给我主页链接。",
                "response_text": "请求已接收。我已识别为 Bilibili 粉丝数查询，但这次没能可靠确认目标账号页面。建议提供更准确的账号名，或直接给我主页链接。",
            }
        if intent.intent_type == "browser_navigation" and intent.constraints.get("news_lookup"):
            return {
                "summary": "我已识别为新闻查询，但暂时没能从目标新闻页提取到足够可靠的内容。",
                "recommendation": "建议提供更具体的品牌或直接给我目标新闻页链接。",
                "response_text": "请求已接收。我已识别为新闻查询，但暂时没能从目标新闻页提取到足够可靠的内容。建议提供更具体的品牌，或直接给我目标新闻页链接。",
            }
        if intent.intent_type == "travel_price_lookup":
            return {
                "summary": "我已识别为机票查询，但暂时没能从航班页面确认价格。",
                "recommendation": "建议补充出行日期、单程或往返，以及是否限定平台。",
                "response_text": "请求已接收。我已识别为机票查询，但暂时没能从航班页面确认价格。建议补充出行日期、单程或往返条件后再继续。",
            }
        if blocked_pages:
            return {
                "summary": "我已经打开候选网页，但目标页面被验证页或拦截页阻断了。",
                "recommendation": "建议提供更直接的页面链接，或稍后重试。",
                "response_text": "请求已接收。我已经打开候选网页，但目标页面被验证页或拦截页阻断了。建议提供更直接的页面链接，或稍后重试。",
            }
        return {
            "summary": "我已开始浏览网页，但暂时没有拿到足够可靠的页面内容。",
            "recommendation": "建议换更明确的关键词，或直接提供目标网站链接。",
            "response_text": "请求已接收。我已开始浏览网页，但暂时没有拿到足够可靠的页面内容。建议换更明确的关键词，或直接提供目标网站链接。",
        }

    def _plan_to_dict(self, plan: NavigationPlan) -> dict[str, Any]:
        return {
            "intent": asdict(plan.intent),
            "target_domains": list(plan.target_domains),
            "search_queries": list(plan.search_queries),
            "candidate_urls": [asdict(item) for item in plan.candidate_urls],
            "missing_constraints": list(plan.missing_constraints),
            "notes": list(plan.notes),
            "max_candidates": plan.max_candidates,
        }

    def _normalize_query(self, text: str) -> str:
        cleaned = text.strip()
        cleaned = cleaned.replace("？", " ").replace("?", " ").replace("，", " ").replace(",", " ")
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        changed = True
        while changed and cleaned:
            changed = False
            for prefix in _QUERY_PREFIXES:
                if cleaned.startswith(prefix):
                    cleaned = cleaned[len(prefix) :].lstrip()
                    changed = True
        for filler in _LEADING_FILLERS:
            if cleaned.startswith(filler):
                cleaned = cleaned[len(filler) :].lstrip()
        return cleaned or text.strip()

    def _site_key(self, text: str) -> str | None:
        lowered = text.lower()
        for key, markers in _SITE_MARKERS.items():
            if any(marker.lower() in lowered for marker in markers):
                return key
        return None

    def _is_weather_request(self, text: str) -> bool:
        lowered = text.lower()
        return any(marker in lowered for marker in _WEATHER_MARKERS)

    def _extract_weather_location(self, text: str) -> str:
        cleaned = text.strip()
        cleaned = re.sub(r"(今天天气|明天天气|后天天气)", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"(今天|今日|明天|后天|现在|当前|最近|这几天)", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"(天气怎么样|天气咋样|天气如何|什么天气|天气|气温|温度|预报|会不会下雨|下不下雨)", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\b(?:weather|forecast)\b", " ", cleaned, flags=re.IGNORECASE)
        changed = True
        while changed and cleaned:
            changed = False
            for prefix in _QUERY_PREFIXES + _ACTION_FILLERS + _LEADING_FILLERS:
                if cleaned.startswith(prefix):
                    cleaned = cleaned[len(prefix) :].lstrip()
                    changed = True
        cleaned = re.sub(r"^(的|要|想|问|一下)\s*", "", cleaned)
        cleaned = re.sub(r"(呢|呀|啊|吗|嘛|吧|如何|怎样|咋样|怎么样)$", "", cleaned).strip()
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ，,。.!！？?")
        return cleaned

    def _extract_weather_day_offset(self, text: str) -> int:
        if "后天" in text:
            return 2
        if "明天" in text:
            return 1
        return 0

    def _strip_site_terms(self, text: str) -> str:
        cleaned = text
        site_key = self._site_key(text)
        if site_key is not None:
            for marker in _SITE_MARKERS[site_key]:
                cleaned = re.sub(re.escape(marker), " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        changed = True
        while changed and cleaned:
            changed = False
            for prefix in _ACTION_FILLERS:
                if cleaned.startswith(prefix):
                    cleaned = cleaned[len(prefix) :].lstrip()
                    changed = True
        cleaned = re.sub(r"(现在|目前|当前)有多少", "", cleaned).strip()
        cleaned = re.sub(r"(粉丝数|粉丝|followers|subscriber)\s*有多少", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"有多少\s*(粉丝数|粉丝|followers|subscriber)", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"有多少粉丝了", "", cleaned).strip()
        cleaned = re.sub(r"有多少粉丝", "", cleaned).strip()
        cleaned = re.sub(r"有多少", "", cleaned).strip()
        cleaned = re.sub(r"多少", "", cleaned).strip()
        cleaned = re.sub(r"粉丝数", "", cleaned).strip()
        cleaned = re.sub(r"粉丝", "", cleaned).strip()
        cleaned = re.sub(r"^(最新的|最新|科技新闻|新闻|快讯|动态)\s*", "", cleaned).strip()
        cleaned = re.sub(r"(最新的|最新|科技新闻|新闻|快讯|动态)$", "", cleaned).strip()
        cleaned = re.sub(r"^[里在]\s*", "", cleaned).strip()
        cleaned = re.sub(r"[里在上]\s*$", "", cleaned).strip()
        cleaned = re.sub(r"的$", "", cleaned).strip()
        cleaned = re.sub(r"了$", "", cleaned).strip()
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    def _extract_site_metric_entity(self, text: str, site_key: str) -> str:
        markers = [re.escape(marker) for marker in _SITE_MARKERS.get(site_key, ())]
        site_pattern = "(?:" + "|".join(markers) + ")" if markers else ""
        metric_pattern = r"(?:粉丝数|粉丝|followers|subscriber)"
        normalized = re.sub(r"\s+", " ", text).strip()

        patterns: list[str] = []
        if site_pattern:
            patterns.extend(
                [
                    rf"(?P<entity>.+?)\s*在\s*{site_pattern}.*?{metric_pattern}",
                    rf"{site_pattern}\s*(?P<entity>.+?)\s*的?\s*{metric_pattern}",
                    rf"(?P<entity>.+?)\s*的?\s*{metric_pattern}",
                ]
            )
        else:
            patterns.append(rf"(?P<entity>.+?)\s*的?\s*{metric_pattern}")

        for pattern in patterns:
            match = re.search(pattern, normalized, flags=re.IGNORECASE)
            if not match:
                continue
            entity = match.group("entity").strip()
            entity = self._strip_site_terms(entity)
            if entity:
                return entity

        return self._strip_site_terms(normalized)

    def _is_distance_request(self, text: str) -> bool:
        lowered = text.lower()
        if not any(marker.lower() in lowered for marker in _DISTANCE_INTENT_MARKERS):
            return False
        if any(marker in text for marker in _TRAVEL_MARKERS):
            return False
        return True

    def _extract_route_pair(self, text: str) -> tuple[str, str] | None:
        normalized = text.strip().rstrip("。.!！?？")
        for suffix in _DISTANCE_SUFFIXES:
            if normalized.lower().endswith(suffix.lower()):
                normalized = normalized[: -len(suffix)].strip()
                break
        for connector in _DISTANCE_CONNECTORS:
            if connector in normalized:
                left, right = normalized.split(connector, 1)
                start = left.strip(" 、，,.。")
                end = right.strip(" 、，,.。")
                if start and end:
                    return start, end
        return None

    def _extract_date_constraints(self, text: str) -> list[str]:
        matches = re.findall(r"(?:20\d{2}[年/-]\d{1,2}[月/-]\d{1,2}日?|\d{1,2}月\d{1,2}日|明天|后天|今天)", text)
        return matches[:3]

    def _try_distance_fallback(self, intent: WebResearchIntent) -> dict[str, Any] | None:
        pair = intent.constraints.get("route") or self._extract_route_pair(intent.normalized_query)
        if pair is None:
            return None
        start_name, end_name = pair
        start = self._geocode_place(start_name)
        end = self._geocode_place(end_name)
        if start is None or end is None:
            return None
        distance_km = self._haversine_km(start["lat"], start["lon"], end["lat"], end["lon"])
        rounded_distance = int(round(distance_km))
        summary = f"{start_name}到{end_name}的直线距离约为 {rounded_distance} 公里。"
        recommendation = "这是基于地理坐标计算的大圆直线距离，不是航班或公路里程。"
        response_text = (
            "请求已接收。"
            f"系统已通过地理坐标计算：{start_name}到{end_name}的直线距离约 {rounded_distance} 公里。"
            "结论：这是大圆直线距离，不是航班或驾车里程。"
        )
        sources = [
            {"title": f"Nominatim geocoding: {start_name}", "url": start["source_url"]},
            {"title": f"Nominatim geocoding: {end_name}", "url": end["source_url"]},
        ]
        return {
            "summary": summary,
            "recommendation": recommendation,
            "sources": sources,
            "distance_km": rounded_distance,
            "distance_type": "great_circle",
            "response_text": response_text,
        }

    def _try_weather_lookup(self, intent: WebResearchIntent) -> dict[str, Any] | None:
        location = str(intent.constraints.get("location", "") or intent.target_entity or "").strip()
        location_source = "explicit"
        geo_source_url = ""
        if not location:
            detected = self._detect_current_location()
            if detected is None:
                return None
            location = str(detected.get("location", "") or "").strip()
            geo_source_url = str(detected.get("source_url", "") or "")
            location_source = "detected"
        if not location:
            return None
        geo = self._weather_geocode_place(location)
        if geo is None:
            return None

        day_offset = max(0, min(2, int(intent.constraints.get("day_offset", 0) or 0)))
        try:
            response = requests.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": geo["lat"],
                    "longitude": geo["lon"],
                    "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
                    "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                    "timezone": "auto",
                    "forecast_days": max(1, day_offset + 1),
                },
                timeout=18,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return None

        current = payload.get("current", {}) if isinstance(payload.get("current"), dict) else {}
        daily = payload.get("daily", {}) if isinstance(payload.get("daily"), dict) else {}
        dates = daily.get("time") if isinstance(daily.get("time"), list) else []
        max_values = daily.get("temperature_2m_max") if isinstance(daily.get("temperature_2m_max"), list) else []
        min_values = daily.get("temperature_2m_min") if isinstance(daily.get("temperature_2m_min"), list) else []
        rain_values = daily.get("precipitation_probability_max") if isinstance(daily.get("precipitation_probability_max"), list) else []
        weather_codes = daily.get("weather_code") if isinstance(daily.get("weather_code"), list) else []

        index = min(day_offset, len(dates) - 1) if dates else 0
        current_code = int(current.get("weather_code", 0) or 0)
        forecast_code = int(weather_codes[index] or current_code) if weather_codes and index < len(weather_codes) else current_code
        weather_label = self._weather_code_to_text(forecast_code)
        max_temp = self._safe_float(max_values[index]) if index < len(max_values) else None
        min_temp = self._safe_float(min_values[index]) if index < len(min_values) else None
        rain_prob = self._safe_float(rain_values[index]) if index < len(rain_values) else None
        current_temp = self._safe_float(current.get("temperature_2m"))
        apparent_temp = self._safe_float(current.get("apparent_temperature"))
        wind_speed = self._safe_float(current.get("wind_speed_10m"))

        day_label = "今天"
        if day_offset == 1:
            day_label = "明天"
        elif day_offset == 2:
            day_label = "后天"

        temp_bits: list[str] = []
        if current_temp is not None and day_offset == 0:
            temp_bits.append(f"当前约 {round(current_temp):.0f}°C")
        if min_temp is not None and max_temp is not None:
            temp_bits.append(f"{day_label} {round(min_temp):.0f}°C ~ {round(max_temp):.0f}°C")
        if apparent_temp is not None and day_offset == 0:
            temp_bits.append(f"体感约 {round(apparent_temp):.0f}°C")
        if wind_speed is not None:
            temp_bits.append(f"风速约 {round(wind_speed):.0f} km/h")
        if rain_prob is not None:
            temp_bits.append(f"降雨概率约 {round(rain_prob):.0f}%")

        location_prefix = f"{location}{day_label}"
        if location_source == "detected":
            location_prefix = f"当前定位附近（{location}）{day_label}"
        summary = f"{location_prefix}天气为{weather_label}。"
        if temp_bits:
            summary += " " + "，".join(temp_bits) + "。"
        recommendation = self._weather_recommendation(weather_label, rain_prob, max_temp)
        response_text = f"请求已接收。{summary}{recommendation}"
        sources = []
        if geo_source_url:
            sources.append({"title": "IP Geolocation", "url": geo_source_url})
        sources.extend(
            [
                {"title": f"Open-Meteo Geocoding: {location}", "url": geo["source_url"]},
                {"title": f"Open-Meteo Forecast: {location}", "url": str(response.url or "https://api.open-meteo.com/")},
            ]
        )
        return {
            "summary": summary,
            "recommendation": recommendation,
            "sources": sources,
            "city": location,
            "temp": round(current_temp) if current_temp is not None else None,
            "high": round(max_temp) if max_temp is not None else None,
            "low": round(min_temp) if min_temp is not None else None,
            "feels_like": round(apparent_temp) if apparent_temp is not None else None,
            "wind": f"{round(wind_speed):.0f} km/h" if wind_speed is not None else "",
            "condition": weather_label,
            "icon_type": self._weather_icon_type(forecast_code),
            "weather_location": location,
            "weather_label": weather_label,
            "location_source": location_source,
            "response_text": response_text,
        }

    def _geocode_place(self, place: str) -> dict[str, Any] | None:
        try:
            response = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={
                    "q": place,
                    "format": "jsonv2",
                    "limit": 1,
                    "accept-language": "zh-CN,en",
                },
                headers={"User-Agent": "Fairy/1.0 (desktop assistant)"},
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return None
        if not payload:
            return None
        item = payload[0]
        try:
            lat = float(item["lat"])
            lon = float(item["lon"])
        except Exception:
            return None
        source_url = str(response.url) if response.url else "https://nominatim.openstreetmap.org/"
        return {"lat": lat, "lon": lon, "source_url": source_url}

    def _weather_geocode_place(self, place: str) -> dict[str, Any] | None:
        try:
            response = requests.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={
                    "name": place,
                    "count": 1,
                    "language": "zh",
                    "format": "json",
                },
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return None
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list) or not results:
            return None
        item = results[0]
        try:
            lat = float(item["latitude"])
            lon = float(item["longitude"])
        except Exception:
            return None
        source_url = str(response.url) if response.url else "https://geocoding-api.open-meteo.com/"
        return {"lat": lat, "lon": lon, "source_url": source_url}

    def _detect_current_location(self) -> dict[str, Any] | None:
        try:
            response = requests.get(
                "https://ipwho.is/",
                timeout=10,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return None
        if not isinstance(payload, dict) or payload.get("success") is False:
            return None
        city = str(payload.get("city", "") or "").strip()
        region = str(payload.get("region", "") or "").strip()
        country = str(payload.get("country", "") or "").strip()
        location = city or region or country
        if not location:
            return None
        return {
            "location": location,
            "city": city,
            "region": region,
            "country": country,
            "source_url": str(response.url or "https://ipwho.is/"),
        }

    def _safe_float(self, value: Any) -> float | None:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    def _weather_code_to_text(self, code: int) -> str:
        mapping = {
            0: "晴",
            1: "大致晴朗",
            2: "多云",
            3: "阴",
            45: "有雾",
            48: "雾凇",
            51: "小毛雨",
            53: "毛雨",
            55: "较强毛雨",
            61: "小雨",
            63: "降雨",
            65: "大雨",
            71: "小雪",
            73: "降雪",
            75: "大雪",
            80: "阵雨",
            81: "较强阵雨",
            82: "强阵雨",
            95: "雷暴",
            96: "雷暴夹小冰雹",
            99: "雷暴夹冰雹",
        }
        return mapping.get(int(code or 0), "天气多变")

    def _weather_recommendation(self, weather_label: str, rain_prob: float | None, max_temp: float | None) -> str:
        if rain_prob is not None and rain_prob >= 55:
            return "建议带伞，出门前再看一眼实时降雨。"
        if "雷暴" in weather_label:
            return "不推荐长时间户外停留，优先避开雷暴时段。"
        if max_temp is not None and max_temp >= 32:
            return "气温偏高，建议补水并注意防晒。"
        if "雪" in weather_label or "雾" in weather_label:
            return "能见度和路面条件可能一般，出行要留更多余量。"
        return "整体看可正常安排出行，临出门前再看一次实时变化更稳。"

    def _weather_icon_type(self, code: int) -> str:
        numeric = int(code or 0)
        if numeric in {45, 48}:
            return "fog"
        if numeric in {51, 53, 55, 61, 63, 65, 80, 81, 82}:
            return "rain"
        if numeric in {71, 73, 75}:
            return "snow"
        if numeric in {95, 96, 99}:
            return "storm"
        if numeric in {1, 2, 3}:
            return "cloud"
        return "sun"

    def _haversine_km(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        radius_km = 6371.0088
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        delta_phi = math.radians(lat2 - lat1)
        delta_lambda = math.radians(lon2 - lon1)
        a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return radius_km * c

    def _name_matches(self, target_entity: str, candidate_name: str) -> bool:
        target = re.sub(r"\s+", "", target_entity.lower())
        candidate = re.sub(r"\s+", "", candidate_name.lower())
        if not target:
            return True
        if target in candidate or candidate in target:
            return True
        shared = sum(1 for ch in set(target) if ch in candidate)
        return shared >= max(2, min(4, len(set(target))))
