from __future__ import annotations

from typing import Any

from .decision_models import WebAccessDecision
from .source_registry import extract_explicit_url, preferred_domains_for_source, resolve_source_descriptor


_INTERACTIVE_MARKERS = (
    "打开",
    "open",
    "点击",
    "click",
    "站内搜索",
    "搜索框",
    "翻页",
    "下一页",
    "更多",
    "展开",
    "栏目",
    "tab",
    "pagination",
)
_VISUAL_MARKERS = (
    "顶部公告",
    "最上面",
    "top banner",
    "hero",
    "横幅",
    "公告写了什么",
    "页面布局",
    "页面上面",
    "看看这个网页",
)
_RENDERED_MARKERS = (
    "渲染",
    "rendered",
    "js",
    "javascript",
    "动态内容",
    "页面加载后",
)
_WEB_RESEARCH_MARKERS = (
    "新闻",
    "最新",
    "发布",
    "官网",
    "文档",
    "查一下",
    "搜一下",
    "看看",
    "research",
    "latest",
    "news",
    "docs",
)
_FORCED_BROWSE_RELEASE_TERMS = (
    "发布了吗",
    "什么时候发布",
    "发布时间",
    "发售了吗",
    "发售时间",
    "上市了吗",
    "上市时间",
    "release date",
    "released",
    "announced",
    "launch date",
)
_FORCED_BROWSE_SPECS_TERMS = (
    "参数",
    "配置",
    "规格",
    "详细配置",
    "技术规格",
    "spec",
    "specs",
    "specifications",
    "technical specifications",
    "configuration",
)
_FORCED_BROWSE_NEWS_TERMS = (
    "新闻",
    "资讯",
    "消息",
    "新消息",
    "最近有什么新消息",
    "今天有什么新闻",
    "今天有什么新消息",
    "最新",
    "news",
    "latest",
    "newsroom",
    "what's new",
    "whats new",
    "updates",
)
_FORCED_BROWSE_COMPARE_TERMS = (
    "区别",
    "差异",
    "对比",
    "比较",
    " vs ",
    "versus",
    "compare",
    "comparison",
)
_FORCED_BROWSE_GENERAL_INFO_TERMS = (
    "官网",
    "说明",
    "介绍",
    "介绍一下",
    "文档",
    "帮助",
    "教程",
    "指南",
    "怎么用",
    "如何",
    "如何使用",
    "怎么部署",
    "是什么",
    "是干嘛的",
    "about",
    "overview",
    "docs",
    "pricing",
    "price",
    "guide",
    "help",
    "what is",
)


class WebAccessResolver:
    def resolve(
        self,
        *,
        raw_query: str,
        resolved_query: str,
        resolved_capability: str,
        slots: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        source_hint: str = "",
        followup_info: dict[str, Any] | None = None,
    ) -> WebAccessDecision:
        query = str(raw_query or resolved_query or "").strip()
        lowered = query.lower()
        resolved_slots = dict(slots or {})
        runtime_context = dict(context or {})
        followup_data = dict(followup_info or {})
        explicit_source = str(source_hint or resolved_slots.get("source") or "").strip()
        descriptor = resolve_source_descriptor(query, explicit_source=explicit_source)
        preferred_domains = preferred_domains_for_source(descriptor)
        source_domain = preferred_domains[0] if preferred_domains else ""
        explicit_url = extract_explicit_url(query)
        current_page_url = str(runtime_context.get("last_url") or "").strip()
        target_url = explicit_url
        matched_rules: list[str] = []
        task_type_hint = str(followup_data.get("web_task_type") or "").strip().lower()

        if resolved_capability in {"weather_lookup", "time_lookup", "location_lookup", "display_information"}:
            return WebAccessDecision(
                needs_web=False,
                access_mode="none",
                intent_type="structured_lookup",
                reason="structured_realtime_capability",
                matched_rules=["structured_lookup"],
            )

        forced_browse_task = task_type_hint or self._forced_browse_task_type(query)
        if forced_browse_task:
            matched_rules.extend(["forced_web_browse", f"web_task_type:{forced_browse_task}"])
            return WebAccessDecision(
                needs_web=True,
                access_mode="http_fetch",
                intent_type="source_constrained_lookup" if descriptor is not None else "general_web_research",
                source_name=descriptor.canonical_name if descriptor else None,
                source_domain=source_domain or None,
                preferred_domains=preferred_domains,
                needs_rendered_page=False,
                needs_interaction=False,
                needs_visual_reading=False,
                reason=f"forced_web_browse:{forced_browse_task}",
                target_url=target_url or self._default_target_url(descriptor, lowered),
                matched_rules=matched_rules,
            )

        if self._contains_any(lowered, _VISUAL_MARKERS):
            matched_rules.append("visual_page_understanding")
            return WebAccessDecision(
                needs_web=True,
                access_mode="visual_read",
                intent_type="visual_page_understanding",
                source_name=descriptor.canonical_name if descriptor else None,
                source_domain=source_domain or None,
                preferred_domains=preferred_domains,
                needs_rendered_page=True,
                needs_interaction=True,
                needs_visual_reading=True,
                reason="visual_target_detected",
                target_url=target_url or current_page_url or self._default_target_url(descriptor, lowered),
                matched_rules=matched_rules,
            )

        if self._contains_any(lowered, _INTERACTIVE_MARKERS):
            matched_rules.append("interactive_site_task")
            return WebAccessDecision(
                needs_web=True,
                access_mode="browser_interaction",
                intent_type="interactive_site_task",
                source_name=descriptor.canonical_name if descriptor else None,
                source_domain=source_domain or None,
                preferred_domains=preferred_domains,
                needs_rendered_page=True,
                needs_interaction=True,
                needs_visual_reading=False,
                reason="interactive_site_signal",
                target_url=target_url or self._default_target_url(descriptor, lowered),
                matched_rules=matched_rules,
            )

        if self._contains_any(lowered, _RENDERED_MARKERS):
            matched_rules.append("dynamic_site_lookup")
            return WebAccessDecision(
                needs_web=True,
                access_mode="rendered_read",
                intent_type="dynamic_site_lookup",
                source_name=descriptor.canonical_name if descriptor else None,
                source_domain=source_domain or None,
                preferred_domains=preferred_domains,
                needs_rendered_page=True,
                needs_interaction=False,
                needs_visual_reading=False,
                reason="rendered_content_signal",
                target_url=target_url or self._default_target_url(descriptor, lowered),
                matched_rules=matched_rules,
            )

        if descriptor is not None:
            matched_rules.append("source_constrained_lookup")
            return WebAccessDecision(
                needs_web=True,
                access_mode="http_fetch",
                intent_type="source_constrained_lookup",
                source_name=descriptor.canonical_name,
                source_domain=source_domain or None,
                preferred_domains=preferred_domains,
                needs_rendered_page=False,
                needs_interaction=False,
                needs_visual_reading=False,
                reason="source_registry_match",
                target_url=target_url or self._default_target_url(descriptor, lowered),
                matched_rules=matched_rules,
            )

        if resolved_capability in {"news_lookup", "generic_search", "explanation"} or self._contains_any(lowered, _WEB_RESEARCH_MARKERS):
            matched_rules.append("general_web_research")
            return WebAccessDecision(
                needs_web=True,
                access_mode="http_fetch",
                intent_type="general_web_research",
                preferred_domains=preferred_domains,
                reason="web_research_query",
                target_url=target_url,
                matched_rules=matched_rules,
            )

        if target_url:
            return WebAccessDecision(
                needs_web=True,
                access_mode="rendered_read",
                intent_type="dynamic_site_lookup",
                preferred_domains=preferred_domains,
                needs_rendered_page=True,
                reason="explicit_url_detected",
                target_url=target_url or current_page_url,
                matched_rules=["explicit_url_detected"],
            )

        return WebAccessDecision(
            needs_web=False,
            access_mode="none",
            intent_type="structured_lookup",
            reason="no_web_signal",
            matched_rules=["no_web_signal"],
        )

    @staticmethod
    def _contains_any(lowered: str, markers: tuple[str, ...]) -> bool:
        return any(marker.lower() in lowered for marker in markers)

    @staticmethod
    def _default_target_url(descriptor: Any, lowered: str) -> str:
        if descriptor is None:
            return ""
        if "doc" in lowered and descriptor.section_urls.get("docs"):
            return descriptor.section_urls["docs"]
        if any(token in lowered for token in ("news", "新闻", "最新", "发布", "消息", "新消息")) and descriptor.section_urls.get("news"):
            return descriptor.section_urls["news"]
        if "科技" in lowered and descriptor.section_urls.get("tech"):
            return descriptor.section_urls["tech"]
        return descriptor.home_url

    @staticmethod
    def _forced_browse_task_type(query: str) -> str:
        text = str(query or "").strip()
        lowered = text.lower()
        if any(term in text for term in _FORCED_BROWSE_RELEASE_TERMS if not term.isascii()) or any(
            term in lowered for term in _FORCED_BROWSE_RELEASE_TERMS if term.isascii()
        ):
            return "release"
        if any(term in text for term in _FORCED_BROWSE_COMPARE_TERMS if not term.isascii()) or any(
            term in lowered for term in _FORCED_BROWSE_COMPARE_TERMS if term.isascii()
        ):
            return "compare"
        if any(term in text for term in _FORCED_BROWSE_SPECS_TERMS if not term.isascii()) or any(
            term in lowered for term in _FORCED_BROWSE_SPECS_TERMS if term.isascii()
        ):
            return "specs"
        if any(term in text for term in _FORCED_BROWSE_NEWS_TERMS if not term.isascii()) or any(
            term in lowered for term in _FORCED_BROWSE_NEWS_TERMS if term.isascii()
        ):
            return "news"
        if any(term in text for term in _FORCED_BROWSE_GENERAL_INFO_TERMS if not term.isascii()) or any(
            term in lowered for term in _FORCED_BROWSE_GENERAL_INFO_TERMS if term.isascii()
        ):
            return "general_info"
        return ""
