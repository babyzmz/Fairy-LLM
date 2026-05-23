from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin

from .decision_models import BrowserAction, RetrievalPlan, WebAccessDecision
from .source_registry import extract_explicit_url, resolve_source_descriptor


_CHINESE_NEWS_TOPICS = ("科技", "AI", "人工智能", "手机", "数码", "财经", "汽车", "游戏")
_TOPIC_FOLLOWUP_MARKERS = ("那", "再", "顺便", "继续", "那今天", "那今天的", "再看看", "那 IT", "那it")
_NEW_TARGET_MARKERS = (
    "打开",
    "官网",
    "网页",
    "页面",
    "链接",
    "发布",
    "公告",
    "顶部",
    "上面",
    "看看这个网页",
    "http://",
    "https://",
)
_WEB_CONTINUATION_MARKERS = ("继续", "继续找", "接着找", "再找找", "换个来源", "换个来源继续找")
_RELEASE_TERMS_ZH = ("发布了吗", "什么时候发布", "发售了吗", "上市了吗", "发布时间", "发售时间")
_RELEASE_TERMS_EN = ("release date", "announced", "released", "launch date", "rumor")
_SPECS_TERMS_ZH = ("参数", "配置", "规格", "详情参数")
_SPECS_TERMS_EN = ("specs", "spec", "specifications", "configuration")


_COMPARE_TERMS_ZH = ("区别", "差异", "对比", "比较", "哪个好")
_COMPARE_TERMS_EN = (" vs ", "versus", "compare", "comparison", "difference")
_FORCED_BROWSE_TASK_TYPES = {"specs", "release", "news", "product_lookup", "compare", "general_info"}
_SAFE_NEWS_TERMS_ZH = (
    "\u65b0\u95fb",
    "\u8d44\u8baf",
    "\u6d88\u606f",
    "\u65b0\u6d88\u606f",
    "\u6700\u65b0",
    "\u4eca\u5929",
    "\u4eca\u65e5",
)
_SAFE_RELEASE_TERMS_ZH = (
    "\u53d1\u5e03\u4e86\u5417",
    "\u4ec0\u4e48\u65f6\u5019\u53d1\u5e03",
    "\u53d1\u552e\u4e86\u5417",
    "\u4e0a\u5e02\u4e86\u5417",
    "\u53d1\u5e03\u65f6\u95f4",
    "\u53d1\u552e\u65f6\u95f4",
    "\u53d1\u5e03",
    "\u53d1\u552e",
    "\u4e0a\u5e02",
)
_SAFE_SPECS_TERMS_ZH = (
    "\u53c2\u6570",
    "\u914d\u7f6e",
    "\u89c4\u683c",
    "\u8be6\u60c5\u53c2\u6570",
)
_SAFE_COMPARE_TERMS_ZH = (
    "\u533a\u522b",
    "\u5dee\u5f02",
    "\u5bf9\u6bd4",
    "\u6bd4\u8f83",
    "\u54ea\u4e2a\u597d",
)
_SAFE_GENERAL_INFO_TERMS_ZH = (
    "\u8bf4\u660e",
    "\u4ecb\u7ecd",
    "\u6587\u6863",
    "\u5e2e\u52a9",
    "\u6559\u7a0b",
    "\u6307\u5357",
    "\u5b9a\u4ef7",
    "\u4ef7\u683c",
    "\u600e\u4e48\u7528",
    "\u5982\u4f55",
    "\u662f\u5e72\u561b\u7684",
)


class RetrievalPlanBuilder:
    def build(
        self,
        *,
        query: str,
        decision: WebAccessDecision,
        slots: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> RetrievalPlan:
        resolved_slots = dict(slots or {})
        runtime_context = dict(context or {})
        query = str(query or "").strip()
        explicit_url = extract_explicit_url(query)
        target_url = explicit_url or decision.target_url or self._default_target_url(decision)
        continuation = self._resolve_continuation(query=query, context=runtime_context)
        topic, carryover_applied, carryover_reason = self._resolve_topic(
            query=query,
            decision=decision,
            slots=resolved_slots,
            context=runtime_context,
        )
        date = self._resolve_date_scope(query=query, slots=resolved_slots)
        query_language = "zh" if self._looks_chinese(query) or (topic and self._looks_chinese(topic)) else "en"
        if not target_url:
            target_url = self._default_news_target_url(
                query=query,
                topic=topic,
                language=query_language,
                intent_type=decision.intent_type,
            )

        if decision.access_mode == "none":
            return RetrievalPlan(access_mode="none")

        if continuation["applied"]:
            return self._build_continuation_plan(
                query=query,
                decision=decision,
                context=runtime_context,
                topic=topic,
                date=date,
                language=query_language,
                continuation=continuation,
            )

        if decision.intent_type == "source_constrained_lookup":
            descriptor = resolve_source_descriptor(query, explicit_source=decision.source_name or decision.source_domain or "")
            primary_queries, fallback_queries, strategy = self._source_constrained_queries(
                query=query,
                topic=topic,
                date=date,
                source_name=decision.source_name or "",
                domain=decision.source_domain or "",
                language=query_language,
            )
            task_type, entity = self._browse_task(
                query=query,
                topic=topic,
                descriptor=descriptor,
            )
            entry_urls = self._entry_urls_for_source_task(
                descriptor=descriptor,
                task_type=task_type,
                entity=entity,
            )
            navigation_targets = self._navigation_targets_for_source_task(
                descriptor=descriptor,
                task_type=task_type,
                entity=entity,
                topic=topic,
                query=query,
            )
            stop_conditions = self._stop_conditions_for_source_task(task_type)
            return RetrievalPlan(
                access_mode=decision.access_mode,
                primary_queries=primary_queries,
                fallback_queries=fallback_queries,
                preferred_domains=list(decision.preferred_domains),
                target_urls=entry_urls[:1] or ([target_url] if target_url else []),
                browser_actions=self._browser_actions_for_query(query, target_url, topic=topic, decision=decision),
                source_constraints={
                    "source_name": decision.source_name,
                    "source_domain": decision.source_domain,
                    "query_strategy": strategy,
                    "source_constraint_applied": bool(decision.source_domain or decision.source_name),
                    "topic_carryover_applied": carryover_applied,
                    "topic_carryover_reason": carryover_reason,
                    "topic": topic,
                    "date_scope": date,
                    "user_query": query,
                    "original_user_query": query,
                    "task_type": task_type,
                    "entity": entity,
                    "force_web_browse": task_type in _FORCED_BROWSE_TASK_TYPES,
                    "browse_strategy": "source_constrained_browse" if task_type in _FORCED_BROWSE_TASK_TYPES else "",
                    "max_hops": 2,
                    "max_candidate_links": 2,
                    "entry_urls": entry_urls,
                    "navigation_targets": navigation_targets,
                    "revised_query": "",
                },
                stop_conditions=stop_conditions or ["reliable_items_found", "source_constrained_result_found"],
            )

        if decision.intent_type == "interactive_site_task":
            return RetrievalPlan(
                access_mode=decision.access_mode,
                primary_queries=[],
                fallback_queries=[],
                target_urls=[target_url] if target_url else [],
                preferred_domains=list(decision.preferred_domains),
                browser_actions=self._browser_actions_for_query(query, target_url, topic=topic, decision=decision),
                source_constraints={
                    "source_name": decision.source_name,
                    "source_domain": decision.source_domain,
                    "query_strategy": "direct_open",
                    "source_constraint_applied": bool(decision.source_domain or decision.source_name),
                    "topic_carryover_applied": carryover_applied,
                    "topic_carryover_reason": carryover_reason,
                    "topic": topic,
                },
                stop_conditions=["interactive_page_extracted"],
            )

        if decision.intent_type == "visual_page_understanding":
            return RetrievalPlan(
                access_mode=decision.access_mode,
                target_urls=[target_url] if target_url else [],
                preferred_domains=list(decision.preferred_domains),
                browser_actions=self._browser_actions_for_query(query, target_url, topic=topic, decision=decision, visual_only=True),
                visual_targets=self._visual_targets_for_query(query, context=runtime_context),
                source_constraints={
                    "source_name": decision.source_name,
                    "source_domain": decision.source_domain,
                    "query_strategy": "visual_region_read",
                    "topic_carryover_applied": False,
                    "topic_carryover_reason": "visual_query_resets_topic",
                },
                stop_conditions=["visual_summary_ready"],
            )

        if decision.intent_type == "dynamic_site_lookup":
            fallback_queries = [f"{query} 最新", f"{query} 新闻"] if query_language == "zh" else [f"{query} latest", f"{query} news"]
            return RetrievalPlan(
                access_mode=decision.access_mode,
                primary_queries=[query],
                fallback_queries=fallback_queries,
                preferred_domains=list(decision.preferred_domains),
                target_urls=[target_url] if target_url else [],
                browser_actions=self._browser_actions_for_query(query, target_url, topic=topic, decision=decision),
                source_constraints={
                    "query_strategy": "dynamic_site_lookup",
                    "topic_carryover_applied": False,
                    "topic_carryover_reason": "dynamic_site_query_uses_raw_text",
                },
                stop_conditions=["rendered_page_extracted"],
            )

        task_type, entity = self._browse_task(
            query=query,
            topic=topic,
            descriptor=resolve_source_descriptor(query, explicit_source=decision.source_name or decision.source_domain or ""),
        )
        primary_queries, fallback_queries, strategy = self._general_queries(
            query=query,
            topic=topic,
            date=date,
            language=query_language,
        )
        return RetrievalPlan(
            access_mode=decision.access_mode,
            primary_queries=primary_queries,
            fallback_queries=fallback_queries,
            preferred_domains=list(decision.preferred_domains),
            target_urls=[target_url] if target_url else [],
            source_constraints={
                "source_name": decision.source_name,
                "source_domain": decision.source_domain,
                "query_strategy": strategy,
                "source_constraint_applied": bool(decision.source_domain or decision.source_name),
                "topic_carryover_applied": carryover_applied,
                "topic_carryover_reason": carryover_reason,
                "topic": topic,
                "date_scope": date,
                "user_query": query,
                "original_user_query": query,
                "task_type": task_type,
                "entity": entity,
                "force_web_browse": task_type in _FORCED_BROWSE_TASK_TYPES,
                "browse_strategy": "forced_web_browse" if task_type in _FORCED_BROWSE_TASK_TYPES else "",
                "max_hops": 2,
                "max_candidate_links": 2,
                "revised_query": "",
            },
            stop_conditions=["reliable_web_result_found"],
        )

    @staticmethod
    def _default_target_url(decision: WebAccessDecision) -> str:
        if decision.target_url:
            return decision.target_url
        if decision.source_domain:
            return f"https://{decision.source_domain}"
        return ""

    @staticmethod
    def _default_news_target_url(*, query: str, topic: str, language: str, intent_type: str) -> str:
        if language != "zh" or intent_type != "general_web_research":
            return ""
        text = str(query or "")
        looks_like_news = bool(topic) or any(token in text for token in ("新闻", "资讯", "头条", "最新", "今天", "今日"))
        if not looks_like_news:
            return ""
        if "科技" in text or topic == "科技":
            return "https://www.ithome.com/tags/%E7%A7%91%E6%8A%80/"
        return "https://www.ithome.com/list/"

    @staticmethod
    def _resolve_continuation(*, query: str, context: dict[str, Any]) -> dict[str, Any]:
        cleaned = str(query or "").strip()
        strategy = ""
        if cleaned in _WEB_CONTINUATION_MARKERS or any(cleaned.startswith(marker) for marker in _WEB_CONTINUATION_MARKERS):
            strategy = "expand_source" if "换个来源" in cleaned else "continue_previous_web_plan"
        last_query_strategy = str(context.get("last_query_strategy") or "").strip()
        last_result_kind = str(context.get("last_result_kind") or "").strip()
        last_plan = dict(context.get("last_retrieval_plan") or {})
        applied = bool(strategy and (last_query_strategy or last_result_kind or last_plan))
        return {
            "applied": applied,
            "strategy": strategy if applied else "",
            "last_query_strategy": last_query_strategy,
            "last_result_kind": last_result_kind,
            "last_plan": last_plan,
            "last_failure_reason": str(context.get("last_failure_reason") or "").strip(),
            "last_request_id": str(context.get("last_request_id") or "").strip(),
            "last_browse_mode_used": str(context.get("last_browse_mode_used") or "").strip(),
            "last_task_type": str(context.get("last_task_type") or "").strip(),
            "last_navigation_hops": int(context.get("last_navigation_hops") or 0),
            "last_selected_links": list(context.get("last_selected_links") or []),
            "last_final_page_type": str(context.get("last_final_page_type") or "").strip(),
            "last_final_page_url": str(context.get("last_final_page_url") or "").strip(),
        }

    def _build_continuation_plan(
        self,
        *,
        query: str,
        decision: WebAccessDecision,
        context: dict[str, Any],
        topic: str,
        date: str,
        language: str,
        continuation: dict[str, Any],
    ) -> RetrievalPlan:
        last_plan = dict(continuation.get("last_plan") or {})
        last_constraints = dict(last_plan.get("source_constraints") or {})
        last_topic = str(last_constraints.get("topic") or context.get("last_topic") or topic).strip()
        original_user_query = str(
            last_constraints.get("original_user_query")
            or last_constraints.get("user_query")
            or context.get("last_query")
            or query
            or last_topic
        ).strip()
        continuation_subject = str(
            last_constraints.get("entity")
            or last_topic
            or original_user_query
            or query
        ).strip()
        last_strategy = str(continuation.get("last_query_strategy") or last_constraints.get("query_strategy") or "").strip()
        last_browse_mode = str(continuation.get("last_browse_mode_used") or "").strip()
        last_task_type = str(continuation.get("last_task_type") or last_constraints.get("task_type") or "").strip()
        last_entity = str(last_constraints.get("entity") or "").strip()
        day_term = "今天" if date in {"", "today"} else date

        if continuation.get("strategy") == "continue_previous_web_plan" and last_browse_mode == "source_constrained_browse":
            selected_links = list(continuation.get("last_selected_links") or [])
            final_page_url = str(continuation.get("last_final_page_url") or "").strip()
            entry_urls = [
                str(item.get("url") or "").strip()
                for item in selected_links
                if str(item.get("url") or "").strip() and str(item.get("url") or "").strip() != final_page_url
            ]
            if not entry_urls and final_page_url:
                entry_urls = [final_page_url]
            return RetrievalPlan(
                access_mode=decision.access_mode,
                primary_queries=list(last_plan.get("primary_queries") or []),
                fallback_queries=list(last_plan.get("fallback_queries") or []),
                target_urls=entry_urls[:1],
                preferred_domains=list(last_plan.get("preferred_domains") or []),
                source_constraints={
                    **last_constraints,
                    "continuation_applied": True,
                    "continuation_strategy": "continue_previous_web_plan",
                    "continuation_of_request_id": continuation.get("last_request_id") or "",
                    "browse_strategy": "source_constrained_browse",
                    "force_web_browse": True,
                    "task_type": last_task_type,
                    "entity": last_entity,
                    "user_query": original_user_query,
                    "original_user_query": original_user_query,
                    "max_hops": max(1, 2 - int(continuation.get("last_navigation_hops") or 0)),
                    "max_candidate_links": int(last_constraints.get("max_candidate_links") or 2),
                    "entry_urls": entry_urls[:2],
                    "navigation_targets": list(last_constraints.get("navigation_targets") or []),
                    "revised_query": str(last_constraints.get("revised_query") or "").strip(),
                },
                stop_conditions=list(last_plan.get("stop_conditions") or ["source_browse_target_reached"]),
            )

        if continuation.get("strategy") == "expand_source" and str(last_constraints.get("source_name") or "").strip():
            if last_browse_mode == "source_constrained_browse" and last_task_type in {"release", "specs", "product_lookup", "general_info"}:
                subject = last_entity or continuation_subject
                if last_task_type == "release":
                    primary_queries = self._dedupe([f"{subject} 发布时间", f"{subject} 发布", f"{subject} release date"])
                    fallback_queries = self._dedupe([f"{subject} 正式发布", f"{subject} announced", f"{subject} 最新消息"])
                elif last_task_type == "specs":
                    primary_queries = self._dedupe([f"{subject} 参数", f"{subject} 规格", f"{subject} specs"])
                    fallback_queries = self._dedupe([f"{subject} specifications", f"{subject} 配置", f"{subject} 官方参数"])
                else:
                    primary_queries = self._dedupe([f"{subject} 官方信息", f"{subject} 产品信息"])
                    fallback_queries = self._dedupe([f"{subject} latest", f"{subject} official"])
                if last_task_type == "general_info":
                    primary_queries = self._dedupe([f"{subject} 瀹樻柟淇℃伅", f"{subject} official info", f"{subject} about"])
                    fallback_queries = self._dedupe([f"{subject} overview", f"{subject} docs", f"{subject} latest"])
                return RetrievalPlan(
                    access_mode=decision.access_mode,
                    primary_queries=primary_queries,
                    fallback_queries=fallback_queries,
                    target_urls=[],
                    source_constraints={
                        "query_strategy": "generic_web_continuation",
                        "continuation_applied": True,
                        "continuation_strategy": "expand_source",
                        "continuation_of_request_id": continuation.get("last_request_id") or "",
                        "topic": last_topic or continuation_subject,
                        "date_scope": date,
                        "user_query": original_user_query or str(subject).strip(),
                        "original_user_query": original_user_query or str(subject).strip(),
                        "task_type": last_task_type or "general_info",
                        "entity": subject,
                        "force_web_browse": True,
                        "browse_strategy": "forced_web_browse",
                        "max_hops": 2,
                        "max_candidate_links": 2,
                        "revised_query": "",
                    },
                    stop_conditions=["reliable_web_result_found"],
                )
            primary_queries = self._dedupe([
                f"{day_term} {continuation_subject}新闻".strip(),
                f"{continuation_subject} 最新消息".strip(),
                continuation_subject,
            ])
            fallback_queries = self._dedupe([
                f"{continuation_subject} 新闻".strip(),
                f"{continuation_subject} 资讯".strip(),
                f"{continuation_subject} 最新".strip(),
            ])
            target_url = self._default_news_target_url(
                query=query,
                topic=continuation_subject,
                language=language,
                intent_type="general_web_research",
            )
            return RetrievalPlan(
                access_mode=decision.access_mode,
                primary_queries=primary_queries,
                fallback_queries=fallback_queries,
                target_urls=[],
                source_constraints={
                    "query_strategy": "generic_news_continuation",
                    "continuation_applied": True,
                    "continuation_strategy": "expand_source",
                    "continuation_of_request_id": continuation.get("last_request_id") or "",
                    "topic": last_topic or continuation_subject,
                    "date_scope": date,
                    "user_query": original_user_query or continuation_subject,
                    "original_user_query": original_user_query or continuation_subject,
                    "task_type": "news",
                    "entity": continuation_subject,
                    "force_web_browse": True,
                    "browse_strategy": "forced_web_browse",
                    "max_hops": 2,
                    "max_candidate_links": 2,
                    "revised_query": "",
                },
                stop_conditions=["reliable_web_result_found"],
            )

        last_result_kind = str(continuation.get("last_result_kind") or "").strip()
        if last_strategy.startswith("generic_news") or last_result_kind == "news_card":
            primary_queries = self._dedupe([
                f"{day_term} {continuation_subject}新闻".strip(),
                f"{continuation_subject} 最新消息".strip(),
                f"{continuation_subject} 新闻".strip(),
            ])
            fallback_queries = self._dedupe([
                f"{continuation_subject} 资讯".strip(),
                f"{continuation_subject} 最新".strip(),
                continuation_subject,
            ])
            return RetrievalPlan(
                access_mode=decision.access_mode,
                primary_queries=primary_queries,
                fallback_queries=fallback_queries,
                target_urls=[],
                source_constraints={
                    "query_strategy": "generic_news_continuation",
                    "continuation_applied": True,
                    "continuation_strategy": "continue_previous_web_plan",
                    "continuation_of_request_id": continuation.get("last_request_id") or "",
                    "topic": last_topic or continuation_subject,
                    "date_scope": date,
                    "user_query": original_user_query or continuation_subject,
                    "original_user_query": original_user_query or continuation_subject,
                    "task_type": "news",
                    "entity": continuation_subject,
                    "force_web_browse": True,
                    "browse_strategy": "forced_web_browse",
                    "max_hops": 2,
                    "max_candidate_links": 2,
                    "revised_query": "",
                },
                stop_conditions=["reliable_web_result_found"],
            )

        previous_primary = list(last_plan.get("primary_queries") or [])
        previous_fallback = list(last_plan.get("fallback_queries") or [])
        primary_queries = self._dedupe(previous_primary + previous_fallback[:2])
        fallback_queries = self._dedupe(
            previous_fallback + [f"{continuation_subject} 官方消息".strip(), f"{continuation_subject} 最新情况".strip()]
        )
        target_urls = [str(item).strip() for item in list(last_plan.get("target_urls") or []) if str(item).strip()]
        return RetrievalPlan(
            access_mode=decision.access_mode,
            primary_queries=primary_queries,
            fallback_queries=fallback_queries,
            target_urls=target_urls[:1],
            preferred_domains=list(last_plan.get("preferred_domains") or []),
            source_constraints={
                "query_strategy": "generic_web_continuation",
                "continuation_applied": True,
                "continuation_strategy": str(continuation.get("strategy") or "continue_previous_web_plan"),
                "continuation_of_request_id": continuation.get("last_request_id") or "",
                "topic": last_topic or continuation_subject,
                "date_scope": date,
                "user_query": original_user_query or continuation_subject,
                "original_user_query": original_user_query or continuation_subject,
                "task_type": last_task_type or "general_info",
                "entity": last_entity or continuation_subject,
                "force_web_browse": True,
                "browse_strategy": "forced_web_browse",
                "max_hops": 2,
                "max_candidate_links": 2,
                "revised_query": "",
            },
            stop_conditions=["reliable_web_result_found"],
        )

    def _resolve_topic(
        self,
        *,
        query: str,
        decision: WebAccessDecision,
        slots: dict[str, Any],
        context: dict[str, Any],
    ) -> tuple[str, bool, str]:
        explicit_topic = self._extract_explicit_topic(query)
        if explicit_topic:
            return explicit_topic, False, "explicit_topic_in_query"

        slot_topic = str(slots.get("topic") or "").strip()
        context_topic = str(context.get("last_topic") or "").strip()
        if slot_topic and slot_topic in query:
            return slot_topic, False, "slot_topic_present_in_query"

        base_topic = slot_topic or context_topic
        if not base_topic:
            return "", False, "no_topic_available"

        if self._should_carry_topic(query=query, decision=decision):
            return base_topic, True, "followup_topic_inherited"

        return "", False, "new_query_target_clears_topic"

    def _should_carry_topic(self, *, query: str, decision: WebAccessDecision) -> bool:
        stripped = str(query or "").strip()
        lowered = stripped.lower()
        if not stripped:
            return False
        if decision.intent_type in {"visual_page_understanding", "interactive_site_task"}:
            return False
        if any(marker.lower() in lowered for marker in _NEW_TARGET_MARKERS):
            return False
        if len(stripped) > 16:
            return False
        return any(stripped.startswith(marker) or marker.lower() in lowered for marker in _TOPIC_FOLLOWUP_MARKERS)

    @staticmethod
    def _extract_explicit_topic(query: str) -> str:
        text = str(query or "")
        lowered = text.lower()
        for topic in _CHINESE_NEWS_TOPICS:
            topic_text = str(topic or "")
            if not topic_text:
                continue
            if re.search(r"[一-鿿]", topic_text):
                if topic_text in text:
                    return topic_text
                continue
            pattern = rf"(?<![A-Za-z0-9]){re.escape(topic_text.lower())}(?![A-Za-z0-9])"
            if re.search(pattern, lowered):
                return topic_text
        return ""

    @staticmethod
    def _resolve_date_scope(*, query: str, slots: dict[str, Any]) -> str:
        raw = str(slots.get("date") or "").strip().lower()
        if raw:
            return raw
        query_text = str(query or "")
        if "今天" in query_text or "今日" in query_text or "today" in query_text.lower():
            return "today"
        return ""

    @staticmethod
    def _looks_like_release_query(query: str) -> bool:
        text = str(query or "").strip()
        lowered = text.lower()
        return (
            any(term in text for term in _RELEASE_TERMS_ZH)
            or any(term in text for term in _SAFE_RELEASE_TERMS_ZH)
            or any(term in lowered for term in _RELEASE_TERMS_EN)
        )

    @staticmethod
    def _release_subject(query: str) -> str:
        text = str(query or "").strip()
        text = re.sub(r"^(看看|帮我看看|帮我查查|查查|查一下|看下|看一看)\s*", "", text)
        text = re.sub(r"(发布了吗|什么时候发布|发售了吗|上市了吗|发布时间|发售时间|发布|上市|发售)\??$", "", text)
        for marker in _SAFE_RELEASE_TERMS_ZH:
            text = re.sub(re.escape(marker) + r"\??$", "", text)
        return text.strip(" ？?，,。.")

    @staticmethod
    def _is_ascii_heavy(text: str) -> bool:
        stripped = str(text or "").strip()
        if not stripped:
            return False
        ascii_chars = sum(1 for ch in stripped if ord(ch) < 128 and ch.isalnum())
        return ascii_chars >= max(4, len(stripped) // 3)

    @staticmethod
    def _looks_like_specs_query(query: str) -> bool:
        text = str(query or "").strip()
        lowered = text.lower()
        return (
            any(term in text for term in _SPECS_TERMS_ZH)
            or any(term in text for term in _SAFE_SPECS_TERMS_ZH)
            or any(term in lowered for term in _SPECS_TERMS_EN)
        )

    @staticmethod
    def _spec_subject(query: str) -> str:
        text = str(query or "").strip()
        text = re.sub(r"^(帮我查查|帮我看看|帮我查一下|查查|查一下|看看|看下|看一看)\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"(参数|配置|规格|详情参数|specs?|specifications?)$", "", text, flags=re.IGNORECASE)
        for marker in _SAFE_SPECS_TERMS_ZH:
            text = re.sub(re.escape(marker) + r"$", "", text, flags=re.IGNORECASE)
        return text.strip(" ？?，,。.")

    def _source_constrained_queries(
        self,
        *,
        query: str,
        topic: str,
        date: str,
        source_name: str,
        domain: str,
        language: str,
    ) -> tuple[list[str], list[str], str]:
        if self._looks_like_release_query(query):
            subject = self._release_subject(query) or query.strip()
            if language == "zh":
                primary = self._dedupe([
                    f"site:{domain} {subject} 发布时间".strip() if domain else f"{subject} 发布时间".strip(),
                    f"site:{domain} {subject} 发布".strip() if domain else f"{subject} 发布".strip(),
                    query.strip(),
                ])
                fallback = self._dedupe([
                    f"{source_name} {subject} 发布".strip(),
                    f"{source_name} {subject} 官方消息".strip(),
                    f"{subject} 发售".strip(),
                    f"{subject} 上市".strip(),
                    *( [f"site:{domain} {subject} release date".strip()] if domain and self._is_ascii_heavy(subject) else [] ),
                    *( [f"{subject} release date", f"{subject} announced"] if self._is_ascii_heavy(subject) else [] ),
                ])
            else:
                primary = self._dedupe([
                    f"site:{domain} {subject} release date".strip() if domain else f"{subject} release date".strip(),
                    f"site:{domain} {subject} announced".strip() if domain else f"{subject} announced".strip(),
                    query.strip(),
                ])
                fallback = self._dedupe([
                    f"{source_name} {subject} release".strip(),
                    f"{subject} launch date".strip(),
                    f"{subject} latest news".strip(),
                    query.strip(),
                ])
            return primary, fallback, "source_constrained_release"

        if self._looks_like_specs_query(query):
            subject = self._spec_subject(query) or query.strip()
            if language == "zh":
                primary = self._dedupe([
                    f"site:{domain} {subject} 参数".strip() if domain else f"{subject} 参数".strip(),
                    f"site:{domain} {subject} 规格".strip() if domain else f"{subject} 规格".strip(),
                    query.strip(),
                ])
                fallback = self._dedupe([
                    f"{source_name} {subject} 参数".strip(),
                    f"{source_name} {subject} 规格".strip(),
                    f"{subject} 配置".strip(),
                    f"{subject} 官方参数".strip(),
                    *( [f"site:{domain} {subject} specs".strip()] if domain and self._is_ascii_heavy(subject) else [] ),
                    *( [f"{subject} specs", f"{subject} specifications"] if self._is_ascii_heavy(subject) else [] ),
                ])
            else:
                primary = self._dedupe([
                    f"site:{domain} {subject} specs".strip() if domain else f"{subject} specs".strip(),
                    f"site:{domain} {subject} specifications".strip() if domain else f"{subject} specifications".strip(),
                    query.strip(),
                ])
                fallback = self._dedupe([
                    f"{source_name} {subject} specs".strip(),
                    f"{subject} configuration".strip(),
                    query.strip(),
                ])
            return primary, fallback, "source_constrained_specs"

        if language == "zh":
            day_term = "今天" if date in {"", "today"} else date
            primary: list[str] = []
            if domain and topic:
                primary.append(f"site:{domain} {topic} 新闻 {day_term}".strip())
            elif domain:
                primary.append(f"site:{domain} {query}".strip())
            fallback: list[str] = []
            if source_name and topic:
                fallback.append(f"{source_name} {topic}新闻")
                fallback.append(f"{source_name} {day_term} {topic}")
            if source_name:
                fallback.append(f"{source_name} 今日新闻")
            if topic:
                fallback.append(f"{day_term} {topic}新闻")
            fallback.append(query.strip())
            return self._dedupe(primary), self._dedupe(fallback), "source_constrained"

        primary = [f"site:{domain} {topic or query} latest news".strip()] if domain else [query.strip()]
        fallback = self._dedupe([
            f"{source_name} {topic or query}".strip(),
            f"{query} latest".strip(),
            f"{query} news".strip(),
            query.strip(),
        ])
        return self._dedupe(primary), fallback, "source_constrained"

    def _browse_task(
        self,
        *,
        query: str,
        topic: str,
        descriptor: Any,
    ) -> tuple[str, str]:
        if self._looks_like_release_query(query):
            return "release", self._release_subject(query) or query.strip()
        if self._looks_like_specs_query(query):
            return "specs", self._spec_subject(query) or query.strip()
        if self._looks_like_compare_query(query):
            return "compare", self._compare_subject(query) or query.strip()
        lowered = str(query or "").strip().lower()
        if any(token in lowered for token in ("news", "latest")) or any(token in query for token in _SAFE_NEWS_TERMS_ZH) or topic:
            return "news", topic or ""
        if self._looks_like_general_info_query(query):
            return "general_info", query.strip()
        if descriptor is not None:
            product_subject = self._product_subject(query, source_name=getattr(descriptor, "canonical_name", ""))
            if product_subject:
                if str(getattr(descriptor, "key", "") or "").startswith("dynamic:") and self._looks_like_general_info_query(product_subject):
                    return "general_info", query.strip()
                return "product_lookup", product_subject
        return "general_info", self._product_subject(query, source_name=getattr(descriptor, "canonical_name", "")) or query.strip()

    def _entry_urls_for_source_task(
        self,
        *,
        descriptor: Any,
        task_type: str,
        entity: str,
    ) -> list[str]:
        if descriptor is None:
            return []
        entrypoints = dict(getattr(descriptor, "entrypoints", {}) or {})
        urls: list[str] = []
        product_urls = self._known_product_urls(descriptor, entity)
        if task_type == "specs":
            urls.extend(product_urls)
            urls.extend(entrypoints.get("products", ()))
            urls.extend(entrypoints.get("news", ()))
        elif task_type == "release":
            urls.extend(entrypoints.get("news", ()))
            urls.extend(product_urls)
            urls.append(str(getattr(descriptor, "home_url", "") or "").strip())
        elif task_type == "news":
            urls.extend(entrypoints.get("tech", ()))
            urls.extend(entrypoints.get("news", ()))
            urls.append(str(getattr(descriptor, "home_url", "") or "").strip())
        elif task_type == "product_lookup":
            urls.extend(product_urls)
            urls.extend(entrypoints.get("products", ()))
            urls.append(str(getattr(descriptor, "home_url", "") or "").strip())
        elif task_type == "general_info":
            urls.extend(product_urls)
            urls.extend(entrypoints.get("products", ()))
            urls.extend(entrypoints.get("news", ()))
            urls.extend(entrypoints.get("general_info", ()))
            urls.append(str(getattr(descriptor, "home_url", "") or "").strip())
        elif task_type == "compare":
            urls.extend(product_urls)
            urls.extend(entrypoints.get("products", ()))
            urls.extend(entrypoints.get("general_info", ()))
            urls.append(str(getattr(descriptor, "home_url", "") or "").strip())
        else:
            urls.append(str(getattr(descriptor, "home_url", "") or "").strip())
        return self._dedupe(urls)

    def _navigation_targets_for_source_task(
        self,
        *,
        descriptor: Any,
        task_type: str,
        entity: str,
        topic: str,
        query: str,
    ) -> list[str]:
        if descriptor is None:
            return self._dedupe([entity, topic, query])
        hints = list((getattr(descriptor, "navigation_hints", {}) or {}).get(task_type, ()))
        if task_type == "news" and topic:
            return self._dedupe([topic, *hints])
        if task_type == "compare":
            return self._dedupe([entity, query, *hints])
        if task_type == "general_info":
            return self._dedupe([entity, query, *hints])
        return self._dedupe([entity, *hints])

    @staticmethod
    def _stop_conditions_for_source_task(task_type: str) -> list[str]:
        if task_type == "specs":
            return ["specs_page_reached", "specs_signals_detected"]
        if task_type == "release":
            return ["release_page_reached", "release_signals_detected"]
        if task_type == "news":
            return ["news_index_ready", "reliable_items_found"]
        if task_type == "product_lookup":
            return ["product_page_reached", "entity_page_reached"]
        if task_type == "compare":
            return ["compare_page_reached", "compare_signals_detected"]
        if task_type == "general_info":
            return ["general_info_page_reached", "general_info_signals_detected"]
        return []

    def _known_product_urls(self, descriptor: Any, entity: str) -> list[str]:
        entity_key = str(entity or "").strip().lower()
        if descriptor is None or not entity_key:
            return []
        paths: list[str] = []
        for key, values in dict(getattr(descriptor, "known_product_paths", {}) or {}).items():
            normalized_key = str(key or "").strip().lower()
            if normalized_key in entity_key or entity_key in normalized_key:
                paths.extend(str(item).strip() for item in values if str(item).strip())
        return self._dedupe([urljoin(str(getattr(descriptor, "home_url", "") or "").strip(), path) for path in paths])

    @staticmethod
    def _product_subject(query: str, *, source_name: str) -> str:
        text = str(query or "").strip()
        patterns = [
            r"^(看看|帮我看看|帮我查查|查查|查一下|帮我查一下|看下|看一看)\s*",
        ]
        for pattern in patterns:
            text = re.sub(pattern, "", text, flags=re.IGNORECASE)
        if source_name:
            text = text.replace(source_name, "")
        for marker in ("官网", "最新", "新消息", "消息", "新闻"):
            text = text.replace(marker, "")
        return text.strip(" ，。！？?/")

    @staticmethod
    def _looks_like_general_info_query(query: str) -> bool:
        text = str(query or "").strip()
        lowered = text.lower()
        zh_terms = ("说明", "介绍", "文档", "帮助", "教程", "指南", "定价", "价格", "怎么用", "如何", "是干嘛的")
        en_terms = ("docs", "documentation", "guide", "help", "faq", "about", "overview", "pricing", "price", "how to")
        return any(term in text for term in zh_terms) or any(term in text for term in _SAFE_GENERAL_INFO_TERMS_ZH) or any(term in lowered for term in en_terms)

    @staticmethod
    def _looks_like_compare_query(query: str) -> bool:
        text = str(query or "").strip()
        lowered = text.lower()
        return (
            any(term in text for term in _COMPARE_TERMS_ZH)
            or any(term in text for term in _SAFE_COMPARE_TERMS_ZH)
            or any(term in lowered for term in _COMPARE_TERMS_EN)
        )

    @staticmethod
    def _compare_subject(query: str) -> str:
        text = str(query or "").strip()
        lowered = text.lower()
        for marker in list(_COMPARE_TERMS_ZH) + list(_SAFE_COMPARE_TERMS_ZH):
            text = text.replace(marker, " ")
        for marker in _COMPARE_TERMS_EN:
            lowered = lowered.replace(marker, " ")
        compact = " ".join(lowered.split()).strip(" 锛屻€傦紒锛?/")
        return compact or " ".join(text.split()).strip(" 锛屻€傦紒锛?/")

    def _general_queries(self, *, query: str, topic: str, date: str, language: str) -> tuple[list[str], list[str], str]:
        if self._looks_like_release_query(query):
            subject = self._release_subject(query) or query.strip()
            primary = self._dedupe([
                f"{subject} 发布时间".strip(),
                f"{subject} 发布".strip(),
                query.strip(),
            ])
            fallback = self._dedupe([
                f"{subject} 发售".strip(),
                f"{subject} 上市".strip(),
                f"{subject} 官方消息".strip(),
                f"{subject} 最新消息".strip(),
                *( [f"{subject} release date", f"{subject} announced"] if self._is_ascii_heavy(subject) else [] ),
            ])
            return primary, fallback, "generic_web_release"

        if self._looks_like_specs_query(query):
            subject = self._spec_subject(query) or query.strip()
            primary = self._dedupe([
                f"{subject} 参数".strip(),
                f"{subject} 规格".strip(),
                query.strip(),
            ])
            fallback = self._dedupe([
                f"{subject} 配置".strip(),
                f"{subject} 详细参数".strip(),
                *( [f"{subject} specs", f"{subject} specifications"] if self._is_ascii_heavy(subject) else [] ),
                f"{subject} 官方参数".strip(),
            ])
            return primary, fallback, "generic_web_specs"

        if self._looks_like_compare_query(query):
            subject = self._compare_subject(query) or query.strip()
            primary = self._dedupe([
                f"{subject} 瀵规瘮".strip(),
                f"{subject} 鍖哄埆".strip(),
                query.strip(),
            ])
            fallback = self._dedupe([
                f"{subject} compare".strip(),
                f"{subject} vs".strip(),
                f"{subject} comparison".strip(),
            ])
            return primary, fallback, "generic_web_compare"

        if language == "zh":
            day_term = "今天" if date in {"", "today"} else date
            looks_like_news = bool(topic) or any(token in query for token in ("新闻", "资讯", "头条", "最近", "最新"))
            primary: list[str] = []
            fallback: list[str] = []
            if looks_like_news:
                if topic:
                    primary.append(f"{day_term} {topic}新闻".strip())
                    primary.append(f"{topic} 最新消息".strip())
                    fallback.extend([f"{topic} 新闻", f"{topic} 资讯", f"{topic} 最新"])
                else:
                    primary.append(query.strip())
                fallback.append(query.strip())
                return self._dedupe(primary), self._dedupe(fallback), "generic_news"

            primary = [query.strip()]
            fallback = self._dedupe([
                f"{query} 最新情况".strip(),
                f"{query} 官方消息".strip(),
                f"{query} 发布时间".strip(),
                query.strip(),
            ])
            return self._dedupe(primary), fallback, "generic_web"

        primary = [query.strip()]
        fallback = self._dedupe([f"{query} latest", f"{query} official", f"{query} news", query.strip()])
        return primary, fallback, "generic_web"

    def _browser_actions_for_query(
        self,
        query: str,
        target_url: str,
        *,
        topic: str = "",
        decision: WebAccessDecision,
        visual_only: bool = False,
    ) -> list[BrowserAction]:
        lowered = str(query or "").strip().lower()
        actions: list[BrowserAction] = []
        if target_url:
            actions.append({"type": "open_url", "value": target_url, "timeout_ms": 12000})
        if visual_only:
            actions.append({"type": "capture_screenshot"})
            actions.append({"type": "extract_page"})
            return actions
        if "搜索" in query or "search" in lowered:
            actions.append({
                "type": "type",
                "selector": "input[type='search'], input[name='q'], input[type='text']",
                "value": topic or query,
                "timeout_ms": 8000,
            })
            actions.append({"type": "submit", "selector": "input[type='search'], input[name='q'], input[type='text']"})
            actions.append({"type": "wait", "timeout_ms": 1000})
            actions.append({"type": "extract_page"})
            return actions
        if decision.source_domain == "openai.com" and any(token in lowered for token in ("发布", "latest", "news", "最新")):
            return [
                {"type": "open_url", "value": target_url or "https://openai.com/news/", "timeout_ms": 12000},
                {"type": "wait", "timeout_ms": 1200},
                {"type": "extract_page"},
            ]
        if any(token in lowered for token in ("news", "发布", "latest", "最新")):
            actions.append({"type": "click_text", "value": "News"})
            actions.append({"type": "wait", "timeout_ms": 900})
            actions.append({"type": "extract_page"})
            return actions
        if "科技" in query:
            actions.append({"type": "click_text", "value": "科技"})
            actions.append({"type": "wait", "timeout_ms": 900})
            actions.append({"type": "extract_page"})
            return actions
        actions.append({"type": "extract_page"})
        return actions

    @staticmethod
    def _visual_targets_for_query(query: str, *, context: dict[str, Any]) -> list[dict[str, Any]]:
        targets: list[dict[str, Any]] = []
        lowered = str(query or "").strip().lower()
        if any(token in lowered for token in ("顶部", "top", "banner", "公告", "最上面")):
            targets.append({"region": "top_banner"})
        if any(token in lowered for token in ("hero", "首屏")):
            targets.append({"region": "hero_section"})
        if not targets:
            targets.append({"region": "results_panel"})
        if context.get("last_url"):
            for item in targets:
                item["page_url"] = str(context.get("last_url") or "")
        return targets

    @staticmethod
    def _looks_chinese(text: str) -> bool:
        return bool(re.search(r"[一-鿿]", str(text or "")))

    @staticmethod
    def _dedupe(items: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for item in items:
            cleaned = " ".join(str(item or "").split()).strip()
            if not cleaned:
                continue
            lowered = cleaned.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            deduped.append(cleaned)
        return deduped
