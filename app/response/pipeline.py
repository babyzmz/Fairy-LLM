from __future__ import annotations

import html
import logging
import re
from typing import Any

from app.response.card_layout_policy import ResponseCardLayoutPolicy
from app.response.card_schema_registry import CardSchemaRegistry
from app.response.models import CardPayload, NormalizedAssistantResponse, ResponseProgressEvent, SpeechPayload
from app.response.policy import ResponseModalityPlanner
from app.services.assets import (
    AssetResolver,
    MapPreviewService,
    ThumbnailService,
    get_asset_resolver,
    get_map_preview_service,
    get_thumbnail_service,
)


logger = logging.getLogger(__name__)


class ResponsePipeline:
    def __init__(
        self,
        *,
        language: str = "zh_CN",
        map_preview_service: MapPreviewService | None = None,
        thumbnail_service: ThumbnailService | None = None,
        asset_resolver: AssetResolver | None = None,
    ) -> None:
        self.language = language
        self.planner = ResponseModalityPlanner()
        self.schema_registry = CardSchemaRegistry()
        self.layout_policy = ResponseCardLayoutPolicy()
        self.map_preview_service = map_preview_service or get_map_preview_service()
        self.thumbnail_service = thumbnail_service or get_thumbnail_service()
        self.asset_resolver = asset_resolver or get_asset_resolver()

    def set_language(self, language: str) -> None:
        self.language = language

    def plan_request(self, user_text: str, *, previous_structured: dict[str, Any] | None = None):
        return self.planner.plan_request(user_text, previous_structured=previous_structured)

    def build_progress_event(self, event_name: str, payload: dict[str, Any], *, user_text: str = "") -> ResponseProgressEvent | None:
        query = (user_text or "").strip()
        count = int(payload.get("count", 0) or payload.get("result_count", 0) or payload.get("total_count", 0) or 0)
        tool_name = str(payload.get("tool_name", "") or "").strip()
        phase = str(payload.get("phase", "") or "").strip().lower()
        subtype = str(payload.get("subtype", "") or "").strip().lower()
        stage_name = str(payload.get("stage", "") or "").strip().lower()

        if event_name == "structured_tool_progress" and phase:
            if phase == "execution_started":
                return ResponseProgressEvent(
                    stage="execution_started",
                    text=self._localize(
                        "\u6b63\u5728\u542f\u52a8\u7f51\u9875\u8bbf\u95ee\u6267\u884c\u94fe\u8def\u3002",
                        "Starting the web access execution ladder.",
                    ),
                )
            if phase == "web_access_decided":
                access_mode = str(payload.get("access_mode") or "http_fetch").strip()
                intent_type = str(payload.get("intent_type") or "general_web_research").strip()
                return ResponseProgressEvent(
                    stage="web_access_decided",
                    text=self._localize(
                        f"已完成联网访问决策：{intent_type} / {access_mode}。",
                        f"Web access decided: {intent_type} via {access_mode}.",
                    ),
                )
            if phase == "retrieval_plan_built":
                return ResponseProgressEvent(
                    stage="retrieval_plan_built",
                    text=self._localize(
                        "已生成网页检索计划，正在选择最佳入口。",
                        "Retrieval plan ready. Choosing the best entry path.",
                    ),
                )
            if phase == "understanding_request":
                return ResponseProgressEvent(
                    stage="understanding_request",
                    text=self._localize(
                        "正在理解你要找的是参数、发布、新闻还是一般信息。",
                        "Understanding whether you need specs, release info, news, or general information.",
                    ),
                )
            if phase == "opening_page":
                return ResponseProgressEvent(
                    stage="opening_page",
                    text=self._localize(
                        "正在打开网页入口页...",
                        "Opening the site entry page...",
                    ),
                )
            if phase == "understanding_page":
                return ResponseProgressEvent(
                    stage="understanding_page",
                    text=self._localize(
                        "正在理解当前页面结构和页面类型...",
                        "Understanding the current page structure and page type...",
                    ),
                )
            if phase == "page_answer_context_built":
                blocked = bool(payload.get("blocked_for_answer"))
                text_zh = "已从当前页提炼出可回答上下文。"
                text_en = "Built answer-ready context from the current page."
                if blocked:
                    reason = str(payload.get("block_reason") or "").strip()
                    text_zh = f"已完成当前页梳理，但这页暂不适合直接回答：{reason}"
                    text_en = f"Built page context, but this page should not be answered from yet: {reason}"
                return ResponseProgressEvent(stage="page_answer_context_built", text=self._localize(text_zh, text_en))
            if phase == "ranking_links":
                return ResponseProgressEvent(
                    stage="ranking_links",
                    text=self._localize(
                        "正在比较页面里的候选链接...",
                        "Comparing candidate links on the page...",
                    ),
                )
            if phase == "navigating_deeper":
                return ResponseProgressEvent(
                    stage="navigating_deeper",
                    text=self._localize(
                        "正在继续深入到更相关的页面...",
                        "Navigating deeper into a more relevant page...",
                    ),
                )
            if phase == "extracting_answer":
                return ResponseProgressEvent(
                    stage="extracting_answer",
                    text=self._localize(
                        "正在从目标页提取答案...",
                        "Extracting the answer from the target page...",
                    ),
                )
            if phase == "answer_blocked":
                reason = str(payload.get("block_reason") or "").strip()
                return ResponseProgressEvent(
                    stage="answer_blocked",
                    text=self._localize(
                        f"当前页被判定为不宜直接回答：{reason}",
                        f"The current page was blocked for direct answering: {reason}",
                    ),
                )
            if phase == "answer_validation_blocked":
                reason = str(payload.get("reason") or "").strip()
                return ResponseProgressEvent(
                    stage="answer_validation_blocked",
                    text=self._localize(
                        f"已生成答案草稿，但终态校验未通过：{reason}",
                        f"Draft answer generated, but terminal validation rejected it: {reason}",
                    ),
                )
            if phase == "terminal_answer_validated":
                valid = bool(payload.get("terminal_answer_valid"))
                reason = str(payload.get("terminal_answer_validation_reason") or "").strip()
                text_zh = "当前页的答案终态校验已通过。"
                text_en = "The current page passed terminal answer validation."
                if not valid:
                    text_zh = f"当前页的答案终态校验未通过：{reason}"
                    text_en = f"The current page failed terminal answer validation: {reason}"
                return ResponseProgressEvent(stage="terminal_answer_validated", text=self._localize(text_zh, text_en))
            if phase == "fallback_action_chosen":
                chosen_action = str(payload.get("chosen_action") or "").strip()
                reason = str(payload.get("reason") or "").strip()
                return ResponseProgressEvent(
                    stage="fallback_action_chosen",
                    text=self._localize(
                        f"模型决策不稳定，已选择兜底动作：{chosen_action} ({reason})",
                        f"Model decision was unstable. Chose fallback action: {chosen_action} ({reason}).",
                    ),
                )
            if phase == "stop_decided":
                stop_reason = str(payload.get("stop_reason") or "").strip()
                stop_detail = str(payload.get("stop_detail") or "").strip()
                return ResponseProgressEvent(
                    stage="stop_decided",
                    text=self._localize(
                        f"已决定结束当前网页流程：{stop_reason} / {stop_detail}",
                        f"Web loop stop decided: {stop_reason} / {stop_detail}.",
                    ),
                )
            if phase == "stop_candidate_rejected":
                return ResponseProgressEvent(
                    stage="stop_candidate_rejected",
                    text=self._localize(
                        "停止信号还不够稳定，正在继续查找...",
                        "The stop signal was too weak, so the browse flow is continuing.",
                    ),
                )
            if phase == "http_fetch_started":
                return ResponseProgressEvent(
                    stage="http_fetch_started",
                    text=self._localize(
                        "正在进行搜索并抓取网页内容...",
                        "Searching and fetching web content...",
                    ),
                )
            if phase == "rendered_read_started":
                return ResponseProgressEvent(
                    stage="rendered_read_started",
                    text=self._localize(
                        "静态提取不足，正在读取渲染后的页面...",
                        "Static extraction was weak. Reading the rendered page...",
                    ),
                )
            if phase == "browser_interaction_started":
                return ResponseProgressEvent(
                    stage="browser_interaction_started",
                    text=self._localize(
                        "需要页面交互，正在打开浏览器并执行动作...",
                        "Browser interaction required. Opening the page and performing actions...",
                    ),
                )
            if phase == "browser_attempted":
                available = bool(payload.get("available"))
                if available:
                    text_zh = "\u6d4f\u89c8\u5668\u4ea4\u4e92\u80fd\u529b\u53ef\u7528\uff0c\u6b63\u5728\u8fdb\u5165\u9875\u9762\u4ea4\u4e92\u3002"
                    text_en = "Browser interaction is available. Entering the interactive page flow."
                else:
                    reason = str(payload.get("reason") or "browser_unavailable").strip()
                    text_zh = f"\u6d4f\u89c8\u5668\u80fd\u529b\u4e0d\u53ef\u7528\uff0c\u6b63\u5728\u56de\u9000\u5904\u7406\uff1a{reason}"
                    text_en = f"Browser interaction is unavailable. Falling back instead: {reason}."
                return ResponseProgressEvent(stage="browser_attempted", text=self._localize(text_zh, text_en))
            if phase == "visual_read_started":
                return ResponseProgressEvent(
                    stage="visual_read_started",
                    text=self._localize(
                        "DOM 提取不可靠，正在通过视觉读取页面区域...",
                        "DOM extraction was unreliable. Reading the page visually...",
                    ),
                )
            if phase == "fallback_applied":
                stage = str(payload.get("stage") or "fallback").strip()
                reason = str(payload.get("reason") or "unknown").strip()
                return ResponseProgressEvent(
                    stage="fallback_applied",
                    text=self._localize(
                        f"\u5df2\u89e6\u53d1\u8bbf\u95ee\u964d\u7ea7\uff1a{stage} ({reason})",
                        f"Fallback applied: {stage} ({reason}).",
                    ),
                )
            if phase == "web_access_fallback_applied":
                return ResponseProgressEvent(
                    stage="web_access_fallback_applied",
                    text=self._localize(
                        "前一层网页访问不足，正在升级到更高层级...",
                        "Earlier web access was insufficient. Escalating to a higher access level...",
                    ),
                )
            if phase in {"desktop_automation_dispatch"}:
                action_name = str(payload.get("action_name") or "desktop_automation").strip()
                return ResponseProgressEvent(
                    stage="desktop_automation_dispatch",
                    text=self._localize(
                        f"桌面自动化已显式触发：{action_name}",
                        f"Explicit desktop automation requested: {action_name}.",
                    ),
                )
            if phase in {"desktop_automation_result"}:
                action_name = str(payload.get("action_name") or "desktop_automation").strip()
                return ResponseProgressEvent(
                    stage="desktop_automation_result",
                    text=self._localize(
                        f"桌面自动化未执行：{action_name}",
                        f"Desktop automation was not executed: {action_name}.",
                    ),
                )
            if phase in {"desktop_action_dispatch"}:
                action_name = str(payload.get("action_name") or "desktop_action").strip()
                return ResponseProgressEvent(
                    stage="desktop_action_dispatch",
                    text=self._localize(
                        f"正在执行桌面动作：{action_name}",
                        f"Executing desktop action: {action_name}.",
                    ),
                )
            if phase in {"desktop_action_result"}:
                action_name = str(payload.get("action_name") or "desktop_action").strip()
                status_text = str(payload.get("status") or "ok").strip().lower()
                if status_text == "ok":
                    return ResponseProgressEvent(
                        stage="desktop_action_result",
                        text=self._localize(
                            f"桌面动作已完成：{action_name}",
                            f"Desktop action finished: {action_name}.",
                        ),
                    )
                return ResponseProgressEvent(
                    stage="desktop_action_result",
                    text=self._localize(
                        f"桌面动作执行失败：{action_name}",
                        f"Desktop action failed: {action_name}.",
                    ),
                )
            if phase in {"lookup_started", "query_analyzed", "search_plan_created"}:
                return ResponseProgressEvent(
                    stage="planning",
                    text=self._localize(
                        "已完成查询理解，正在规划执行路径...",
                        "Query understood. Planning the execution path...",
                    ),
                )
            if phase in {"stage_entered"} and stage_name == "stage1":
                return ResponseProgressEvent(
                    stage="searching",
                    text=self._localize(
                        "正在读取结构化实时数据源...",
                        "Checking structured realtime sources...",
                    ),
                )
            if phase in {"provider_success"}:
                label = self._structured_label(subtype)
                return ResponseProgressEvent(
                    stage="results_found",
                    text=self._localize(
                        f"已拿到 {label} 的结构化结果，正在整理回答...",
                        f"Structured {label} results received. Preparing the answer...",
                    ),
                )
            if phase in {"search_results_received", "candidates_ranked", "snippet_extracted", "fallback_results_received"}:
                result_count = count or int(payload.get("pages_opened", 0) or 0)
                return ResponseProgressEvent(
                    stage="results_found",
                    text=self._localize(
                        f"已找到 {result_count or '一些'} 条候选结果，正在继续筛选...",
                        f"Found {result_count or 'some'} candidate results. Refining them now...",
                    ),
                )
            if phase in {"fallback_search_started"}:
                fallback_query = str(payload.get("query") or "").strip()
                return ResponseProgressEvent(
                    stage="searching",
                    text=self._localize(
                        f"初次检索结果不足，正在尝试更稳的查询：{fallback_query or 'fallback query'}",
                        f"Initial search was weak. Trying a broader query: {fallback_query or 'fallback query'}.",
                    ),
                )
            if phase in {"page_opening", "page_opened", "structured_value_found"}:
                return ResponseProgressEvent(
                    stage="reading",
                    text=self._localize(
                        "正在读取来源页面并提取关键信息...",
                        "Reading source pages and extracting key details...",
                    ),
                )
            if phase in {"evidence_scored", "validation_complete", "synthesizing", "synthesis_complete"}:
                return ResponseProgressEvent(
                    stage="summarizing",
                    text=self._localize(
                        "正在比对结果并生成总结...",
                        "Comparing findings and generating the summary...",
                    ),
                )
            if phase in {"result_ready"}:
                return ResponseProgressEvent(
                    stage="finalizing",
                    text=self._localize(
                        "结果已整理完成，正在准备展示...",
                        "The result is ready. Preparing the final response...",
                    ),
                )
            if phase in {"search_exhausted"}:
                return ResponseProgressEvent(
                    stage="finalizing",
                    text=self._localize(
                        "已尝试多轮检索，但暂未找到可靠结果。",
                        "Multiple search attempts finished without reliable results.",
                    ),
                )

        if event_name == "news_fetch_start":
            return ResponseProgressEvent(stage="searching", text=self._localize("\u6b63\u5728\u6293\u53d6\u65b0\u95fb\u6e90...", "Searching news sources..."))
        if event_name == "news_fetch_done":
            return ResponseProgressEvent(
                stage="results_found",
                text=self._localize(
                    f"\u5df2\u627e\u5230 {count} \u6761\u5019\u9009\u65b0\u95fb\uff0c\u6b63\u5728\u7b5b\u9009\u91cd\u70b9...",
                    f"Found {count} headline candidates. Ranking the useful ones now.",
                ),
            )
        if event_name == "news_content_fetch_start":
            return ResponseProgressEvent(
                stage="summarizing",
                text=self._localize("\u6b63\u5728\u8865\u6293\u6b63\u6587\u5e76\u751f\u6210\u6458\u8981...", "Opening articles and summarizing them..."),
            )
        if event_name == "news_briefing_ready":
            return ResponseProgressEvent(stage="finalizing", text=self._localize("\u65b0\u95fb\u6458\u8981\u5df2\u51c6\u5907\u597d\u3002", "News briefing is ready."))
        if event_name == "rag_retrieval_started":
            return ResponseProgressEvent(stage="searching", text=self._localize("\u6b63\u5728\u641c\u7d22\u76f8\u5173\u4e0a\u4e0b\u6587...", "Searching related context..."))
        if event_name == "rag_retrieval_finished":
            return ResponseProgressEvent(
                stage="results_found",
                text=self._localize(
                    f"\u5df2\u627e\u5230 {count} \u6761\u76f8\u5173\u5185\u5bb9\uff0c\u6b63\u5728\u6574\u7406...",
                    f"Found {count} relevant results. Organizing them now.",
                ),
            )
        if event_name == "tool_call_start" and tool_name == "search_web":
            return ResponseProgressEvent(stage="searching", text=self._localize("\u6b63\u5728\u641c\u7d22\u7f51\u9875\u7ed3\u679c...", "Searching the web..."))
        if event_name == "tool_call_done" and tool_name == "search_web":
            return ResponseProgressEvent(
                stage="results_found",
                text=self._localize("\u5df2\u627e\u5230\u521d\u6b65\u7ed3\u679c\uff0c\u6b63\u5728\u7ee7\u7eed\u7b5b\u9009...", "Initial web results found. Filtering them now."),
            )
        if event_name == "tool_call_start" and tool_name in {"open_url", "extract_page_text"}:
            return ResponseProgressEvent(stage="reading", text=self._localize("\u6b63\u5728\u8bfb\u53d6\u6765\u6e90\u9875\u9762...", "Reading source pages..."))
        if event_name == "tool_call_done" and tool_name == "compare_structured_results":
            return ResponseProgressEvent(stage="summarizing", text=self._localize("\u6b63\u5728\u6bd4\u5bf9\u7ed3\u679c\u5e76\u751f\u6210\u7b54\u6848...", "Comparing findings and generating the answer..."))
        if event_name == "skill_routed" and query:
            chosen_skill = str(payload.get("chosen_skill", "") or "").strip() or "default"
            return ResponseProgressEvent(
                stage="planning",
                text=self._localize(f"\u5df2\u9009\u62e9\u5904\u7406\u8def\u5f84\uff1a{chosen_skill}\u3002", f"Selected execution path: {chosen_skill}."),
            )
        return None

    def _structured_label(self, subtype: str) -> str:
        mapping = {
            "weather": self._localize("天气", "weather"),
            "time": self._localize("时间", "time"),
            "crypto": self._localize("加密货币", "crypto"),
            "stock": self._localize("股票", "stock"),
            "exchange": self._localize("汇率", "exchange rate"),
            "fuel": self._localize("油价", "fuel price"),
            "sports": self._localize("赛事", "sports"),
            "news": self._localize("新闻", "news"),
        }
        return mapping.get(subtype, self._localize("结果", "result"))

    def normalize_result(
        self,
        *,
        user_text: str,
        payload: dict[str, Any],
        assistant_text: str,
        assistant_html: str = "",
        request_plan=None,
        request_id: str = "",
        session_id: str = "",
    ) -> NormalizedAssistantResponse:
        request_plan = request_plan or self.plan_request(user_text)
        structured = payload.get("structured") if isinstance(payload.get("structured"), dict) else {}
        plain_text = assistant_text.strip() or self._plain_text(assistant_html)
        rich_text = assistant_html if request_plan.modality == "text_only" else ""

        raw_cards = self._collect_raw_card_candidates(
            request_plan=request_plan,
            payload=payload,
            structured=structured,
            plain_text=plain_text,
        )

        card_payloads: list[CardPayload] = []
        fallback_reason = ""
        for candidate_type, raw_data, reason in raw_cards:
            layout = self.layout_policy.resolve(candidate_type, raw_data)
            logger.info(
                "card_layout_selected type=%s layout=%s reason=%s",
                candidate_type,
                layout.mode,
                layout.reason,
            )
            card_payload = self.schema_registry.normalize_card_payload(
                candidate_type,
                raw_data,
                layout=layout.mode,
                fallback_reason=reason,
                metadata={
                    "intent": request_plan.intent,
                    "source_reason": reason,
                    "layout_reason": layout.reason,
                },
            )
            if card_payload is None:
                continue
            if card_payload.type == "generic_info" and candidate_type not in {"generic_info", "generic_info_card"}:
                fallback_reason = reason or f"schema_fallback:{candidate_type}"
            card_payloads.append(card_payload)

        modality = request_plan.modality
        if not card_payloads:
            modality = "text_only"
        elif any(card.type == "location" for card in card_payloads):
            modality = "card_primary_text_summary"
        elif modality == "text_only":
            modality = "text_plus_card"

        text_reply = self._build_text_reply(modality=modality, plain_text=plain_text, card_payloads=card_payloads)
        speech_payload = self._build_speech_payload(request_plan.intent, modality, text_reply, card_payloads)
        normalized_errors = [
            item
            for item in list(payload.get("errors") or [])
            if isinstance(item, dict) and (item.get("code") or item.get("message"))
        ]

        logger.info(
            "response_cards_generated intent=%s modality=%s card_types=%s fallback_reason=%s",
            request_plan.intent,
            modality,
            ",".join(card.type for card in card_payloads),
            fallback_reason,
        )

        return NormalizedAssistantResponse(
            intent=request_plan.intent,
            modality=modality,
            text_reply=text_reply,
            text_rich_html=rich_text if modality == "text_only" else "",
            request_id=request_id,
            session_id=session_id,
            speech_payload=speech_payload,
            card_payloads=card_payloads,
            progress_events=[],
            meta={
                "intent": request_plan.intent,
                "modality": modality,
                "planner_confidence": float(getattr(request_plan, "planner_confidence", 0.0) or 0.0),
            },
            errors=normalized_errors,
        )

    def _collect_raw_card_candidates(
        self,
        *,
        request_plan,
        payload: dict[str, Any],
        structured: dict[str, Any],
        plain_text: str,
    ) -> list[tuple[str, dict[str, Any], str]]:
        candidates: list[tuple[str, dict[str, Any], str]] = []

        explicit_card = self._extract_structured_card_candidate(structured)
        if explicit_card:
            candidates.append(explicit_card)

        weather_payload = self._extract_weather_payload(structured, plain_text, intent=request_plan.intent)
        if weather_payload:
            candidates.append(("weather", weather_payload, "weather_signals"))

        time_payload = self._extract_time_payload(structured, plain_text, intent=request_plan.intent)
        if time_payload:
            candidates.append(("time", time_payload, "time_signals"))

        location_payload = self._extract_location_payload(structured)
        if not location_payload and getattr(request_plan, "force_card_type", "") in {"location", "location_map_card"}:
            location_payload = self._extract_location_payload(getattr(request_plan, "context_payload", {}) or {})
        if location_payload:
            candidates.append(("location", location_payload, "location_signals"))

        news_payload = self._extract_news_payload(structured)
        if news_payload:
            candidates.append(("news_list", news_payload, "news_items"))

        explicit_type = self._infer_structured_response_type(structured, payload, request_plan.intent)
        existing_types = {card_type for card_type, _raw, _reason in candidates}
        if explicit_type and explicit_type not in existing_types:
            candidates.append((explicit_type, dict(structured), f"explicit_card_type:{explicit_type}"))

        if not candidates:
            generic_info_payload = self._extract_generic_info_payload(structured, payload, plain_text)
            if generic_info_payload:
                candidates.append(("generic_info", generic_info_payload, "structured_fallback"))

        return candidates

    def _extract_structured_card_candidate(self, structured: dict[str, Any]) -> tuple[str, dict[str, Any], str] | None:
        card_blob = structured.get("card")
        if not isinstance(card_blob, dict):
            return None
        card_type = str(card_blob.get("type") or structured.get("card_type") or "").strip().lower()
        if not card_type:
            return None
        raw_data = dict(card_blob.get("data") or {})
        if not raw_data:
            raw_data = {key: value for key, value in card_blob.items() if key != "type"}
        if not raw_data:
            return None
        return card_type, raw_data, "structured_card"

    def _infer_structured_response_type(self, structured: dict[str, Any], payload: dict[str, Any], intent: str) -> str:
        explicit_type = ""
        card_blob = structured.get("card")
        if isinstance(card_blob, dict):
            explicit_type = str(card_blob.get("type") or card_blob.get("card_type") or "").strip().lower()
        if not explicit_type:
            explicit_type = str(structured.get("card_type") or payload.get("card_type") or "").strip().lower()
        if explicit_type:
            return explicit_type
        if intent == "weather":
            return "weather"
        if intent == "time":
            return "time"
        if intent == "location":
            return "location"
        if intent == "news":
            return "news_list"
        return ""

    def _build_text_reply(
        self,
        *,
        modality: str,
        plain_text: str,
        card_payloads: list[CardPayload],
    ) -> str:
        weather_card = next((card for card in card_payloads if card.type == "weather"), None)
        if weather_card is not None:
            data = weather_card.data
            city = str(data.get("city") or self._localize("\u5929\u6c14", "Weather")).strip()
            condition = str(data.get("condition") or self._localize("\u5f53\u524d", "Current")).strip()
            temp = self._display_number(data.get("temperature_c"))
            high = self._display_number(data.get("high_c"))
            low = self._display_number(data.get("low_c"))
            if city and condition and temp:
                return self._localize(
                    f"{city} \u73b0\u5728 {temp}\u00b0C\uff0c{condition}\u3002\u4eca\u5929\u5927\u7ea6 {low}\u00b0C \u5230 {high}\u00b0C\u3002",
                    f"{city} is {temp}\u00b0C and {condition.lower()}. Expect roughly {low}\u00b0C to {high}\u00b0C today.",
                )

        time_card = next((card for card in card_payloads if card.type == "time"), None)
        if time_card is not None:
            data = time_card.data
            location = str(data.get("location") or self._localize("\u5f53\u5730\u65f6\u95f4", "Local time")).strip()
            time_text = str(data.get("time_text") or "").strip()
            date_text = str(data.get("date_text") or "").strip()
            weekday = str(data.get("weekday") or "").strip()
            period = str(data.get("period") or "").strip()
            pieces = [item for item in (date_text, weekday, period) if item]
            detail = " ".join(pieces).strip()
            if location and time_text:
                if detail:
                    return self._localize(
                        f"{location} \u73b0\u5728\u662f {time_text}\uff0c{detail}\u3002",
                        f"In {location}, it is {time_text}. {detail}.",
                    )
                return self._localize(
                    f"{location} \u73b0\u5728\u662f {time_text}\u3002",
                    f"In {location}, it is {time_text}.",
                )

        location_card = next((card for card in card_payloads if card.type == "location"), None)
        if location_card is not None:
            data = location_card.data
            bits = [
                str(data.get("title") or "").strip(),
                str(data.get("address") or "").strip(),
                str(data.get("distance_text") or "").strip(),
            ]
            bits = [item for item in bits if item]
            if bits:
                separator = " | " if self.language.startswith("en") else "\uff0c"
                return separator.join(bits[:3])

        news_card = next((card for card in card_payloads if card.type == "news_list"), None)
        if news_card is not None:
            return self._limit_sentences(plain_text, max_sentences=3, max_chars=260)

        web_card = next((card for card in card_payloads if card.type in {"specs", "compare", "release", "web_brief"}), None)
        if web_card is not None:
            if plain_text.strip():
                return self._limit_sentences(plain_text, max_sentences=3, max_chars=260)
            title = str(web_card.data.get("title") or "").strip()
            summary = str(web_card.data.get("summary") or web_card.data.get("recommendation") or "").strip()
            if summary:
                if web_card.type == "web_brief" and title and title not in summary:
                    summary = f"{title}：{summary}"
                return self._limit_sentences(summary, max_sentences=3, max_chars=260)

        generic_card = next((card for card in card_payloads if card.type == "generic_info"), None)
        if generic_card is not None:
            summary = str(generic_card.data.get("summary") or "").strip()
            if summary:
                return self._limit_sentences(summary, max_sentences=3, max_chars=260)

        if modality == "text_only":
            return plain_text
        return self._limit_sentences(plain_text, max_sentences=3, max_chars=260)

    def _build_speech_payload(
        self,
        intent: str,
        modality: str,
        text_reply: str,
        card_payloads: list[CardPayload],
    ) -> SpeechPayload:
        weather_card = next((card for card in card_payloads if card.type == "weather"), None)
        if weather_card is not None:
            data = weather_card.data
            city = str(data.get("city") or "").strip()
            temp = self._display_number(data.get("temperature_c"))
            condition = str(data.get("condition") or "").strip()
            high = self._display_number(data.get("high_c"))
            low = self._display_number(data.get("low_c"))
            return SpeechPayload(
                mode="concise_structured",
                text=self._localize(
                    f"{city} {temp} \u5ea6\uff0c{condition}\uff0c\u9ad8\u6e29 {high} \u5ea6\uff0c\u4f4e\u6e29 {low} \u5ea6\u3002",
                    f"{city}: {temp} degrees, {condition.lower()}, high {high}, low {low}.",
                ),
                allow_streaming=False,
            )

        time_card = next((card for card in card_payloads if card.type == "time"), None)
        if time_card is not None:
            data = time_card.data
            location = str(data.get("location") or "").strip()
            time_text = str(data.get("time_text") or "").strip()
            date_text = str(data.get("date_text") or "").strip()
            weekday = str(data.get("weekday") or "").strip()
            period = str(data.get("period") or "").strip()
            timezone = str(data.get("timezone") or "").strip()
            suffix = "，".join(item for item in (date_text, weekday, period, timezone) if item).strip()
            spoken = self._localize(
                f"{location} 现在是 {time_text}。{suffix}" if suffix else f"{location} 现在是 {time_text}。",
                f"In {location}, it is {time_text}. {suffix}" if suffix else f"In {location}, it is {time_text}.",
            )
            return SpeechPayload(
                mode="concise_structured",
                text=self._limit_sentences(spoken, max_sentences=2, max_chars=140),
                allow_streaming=False,
            )

        location_card = next((card for card in card_payloads if card.type == "location"), None)
        if location_card is not None:
            data = location_card.data
            speech_text = " ".join(
                item
                for item in (
                    str(data.get("title") or "").strip(),
                    str(data.get("address") or "").strip(),
                    str(data.get("distance_text") or "").strip(),
                )
                if item
            ).strip() or text_reply
            return SpeechPayload(
                mode="concise_structured",
                text=self._limit_sentences(speech_text, max_sentences=2, max_chars=140),
                allow_streaming=False,
            )

        news_card = next((card for card in card_payloads if card.type == "news_list"), None)
        if news_card is not None:
            return SpeechPayload(
                mode="summary_first",
                text=self._build_news_speech(news_card.data, fallback=text_reply),
                allow_streaming=False,
            )

        web_card = next((card for card in card_payloads if card.type in {"specs", "compare", "release", "web_brief"}), None)
        if web_card is not None:
            speech_text = str(web_card.data.get("summary") or web_card.data.get("recommendation") or text_reply).strip()
            return SpeechPayload(
                mode="summary_first",
                text=self._limit_sentences(speech_text, max_sentences=2, max_chars=180),
                allow_streaming=False,
            )

        if intent == "text" and modality == "text_only":
            return SpeechPayload(
                mode="detailed_explainer",
                text=self._limit_sentences(text_reply, max_sentences=5, max_chars=420),
                allow_streaming=True,
            )

        return SpeechPayload(
            mode="summary_first",
            text=self._limit_sentences(text_reply, max_sentences=2, max_chars=180),
            allow_streaming=False,
        )

    def _extract_weather_payload(self, structured: dict[str, Any], assistant_text: str, *, intent: str = "") -> dict[str, Any]:
        weather_blob = structured.get("weather") if isinstance(structured.get("weather"), dict) else {}
        source = weather_blob or structured
        has_weather_metrics = any(
            source.get(key) not in {None, ""}
            for key in (
                "weather_label",
                "condition",
                "temp",
                "temperature_c",
                "high",
                "high_c",
                "low",
                "low_c",
                "feels_like",
                "feels_like_c",
                "humidity",
                "humidity_percent",
                "wind",
                "wind_kmh",
                "icon_type",
                "icon_code",
            )
        )
        if intent != "weather" and not has_weather_metrics:
            return {}
        summary = str(source.get("summary") or structured.get("summary") or assistant_text).strip()
        temperature_c = source.get("temperature_c", source.get("temp"))
        humidity_percent = source.get("humidity_percent", source.get("humidity"))
        wind_kmh = source.get("wind_kmh")
        if temperature_c in {None, ""}:
            temperature_c = self._extract_number(summary, r"(-?\d+(?:\.\d+)?)\s*°C")
        if humidity_percent in {None, ""}:
            humidity_percent = self._extract_number(summary, r"(?:湿度|humidity)\s*(\d+(?:\.\d+)?)%")
        if wind_kmh in {None, ""}:
            wind_kmh = self._extract_number(summary, r"(?:风速|wind)\s*(\d+(?:\.\d+)?)\s*km/h")
        icon_key = self._resolve_weather_icon_key(
            source.get("icon_key") or source.get("icon_code") or source.get("icon_type") or source.get("condition_key") or source.get("condition")
        )
        return {
            "city": str(source.get("city") or source.get("weather_location") or "").strip(),
            "country": str(source.get("country") or "").strip(),
            "condition": str(source.get("condition") or source.get("weather_label") or "").strip(),
            "temperature_c": temperature_c,
            "high_c": source.get("high_c", source.get("high")),
            "low_c": source.get("low_c", source.get("low")),
            "feels_like_c": source.get("feels_like_c", source.get("feels_like")),
            "humidity_percent": humidity_percent,
            "wind_kmh": wind_kmh,
            "wind": source.get("wind"),
            "icon_code": source.get("icon_code", source.get("icon_type")),
            "icon_key": icon_key,
            "icon_path": "",
            "condition_key": source.get("condition_key", source.get("condition")),
            "summary": summary,
            "hourly_curve": source.get("hourly_curve"),
        }

    def _extract_time_payload(self, structured: dict[str, Any], assistant_text: str, *, intent: str = "") -> dict[str, Any]:
        time_blob = structured.get("time") if isinstance(structured.get("time"), dict) else {}
        source = time_blob or structured
        has_time_metrics = any(
            source.get(key) not in {None, ""}
            for key in ("time", "date", "timezone", "tz_name", "weekday", "period")
        )
        parsed_from_text = self._parse_time_payload_from_text(assistant_text) if intent == "time" or has_time_metrics else {}
        if intent != "time" and not has_time_metrics and not parsed_from_text:
            return {}
        secondary = list(source.get("secondary") or []) if isinstance(source.get("secondary"), list) else []
        summary = str(source.get("summary") or structured.get("summary") or parsed_from_text.get("summary") or assistant_text).strip()
        location = str(source.get("location") or source.get("city") or source.get("title") or parsed_from_text.get("location") or "").strip()
        if location.endswith("当前时间"):
            location = location[:-4].strip()
        if location.endswith("褰撳墠鏃堕棿"):
            location = location[:-6].strip()
        return {
            "location": location,
            "time_text": str(source.get("time") or source.get("primary") or parsed_from_text.get("time_text") or "").strip(),
            "date_text": str(source.get("date") or parsed_from_text.get("date_text") or (secondary[0] if len(secondary) >= 1 else "")).strip(),
            "weekday": str(source.get("weekday") or parsed_from_text.get("weekday") or (secondary[1] if len(secondary) >= 2 else "")).strip(),
            "period": str(source.get("period") or source.get("period_zh") or parsed_from_text.get("period") or (secondary[2] if len(secondary) >= 3 else "")).strip(),
            "timezone": str(source.get("timezone") or source.get("tz_name") or parsed_from_text.get("timezone") or "").strip(),
            "is_daytime": source.get("is_daytime", parsed_from_text.get("is_daytime")),
            "summary": summary,
        }

    def _parse_time_payload_from_text(self, assistant_text: str) -> dict[str, Any]:
        text = " ".join(str(assistant_text or "").split())
        if not text:
            return {}
        colon_pattern = re.match(
            r"^(?P<location>[^:：\n]{1,80})[:：]\s*(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<time>\d{1,2}:\d{2})(?:\s+(?P<period>[\u4e00-\u9fa5A-Za-z]+))?(?:\s*\((?P<weekday>[^)]+)\))?$",
            text,
        )
        if colon_pattern:
            data = colon_pattern.groupdict()
            return {
                "location": str(data.get("location") or "").strip(),
                "time_text": str(data.get("time") or "").strip(),
                "date_text": str(data.get("date") or "").strip(),
                "weekday": str(data.get("weekday") or "").strip(),
                "period": str(data.get("period") or "").strip(),
                "timezone": "",
                "is_daytime": None,
                "summary": text,
            }
        localized_pattern = re.match(
            r"^(?P<location>.+?)\s+(?:现在是|is)\s+(?P<time>\d{1,2}:\d{2})(?:\s*(?P<period>[\u4e00-\u9fa5A-Za-z]+))?(?:[，,]\s*(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<weekday>[^()]+?))?(?:\s*\((?P<weekday_en>[^)]+)\))?$",
            text,
        )
        if localized_pattern:
            data = localized_pattern.groupdict()
            weekday = str(data.get("weekday") or data.get("weekday_en") or "").strip()
            return {
                "location": str(data.get("location") or "").strip(),
                "time_text": str(data.get("time") or "").strip(),
                "date_text": str(data.get("date") or "").strip(),
                "weekday": weekday,
                "period": str(data.get("period") or "").strip(),
                "timezone": "",
                "is_daytime": None,
                "summary": text,
            }
        return {}

    def _extract_location_payload(self, structured: dict[str, Any]) -> dict[str, Any]:
        location_blob = structured.get("location") if isinstance(structured.get("location"), dict) else {}
        source = location_blob or structured
        lat = self._float_or_none(source.get("lat"))
        lon = self._float_or_none(source.get("lon"))
        has_explicit_location_fields = any(
            str(source.get(key, "") or "").strip()
            for key in ("map_url", "url", "image_url", "address", "place_name", "external_map_url", "map_preview_url")
        )
        has_weather_signals = any(
            source.get(key) not in {None, ""}
            for key in ("temperature_c", "temp", "condition", "weather_label", "humidity", "wind", "wind_kmh")
        )
        if lat is None and lon is None and has_weather_signals and not has_explicit_location_fields:
            return {}
        preview_source = str(
            source.get("map_preview_path")
            or source.get("map_preview_url")
            or source.get("image_path")
            or source.get("image_url")
            or ""
        ).strip()
        preview_path = self.map_preview_service.resolve_preview_path(preview_source, lat=lat, lon=lon)
        if lat is None or lon is None:
            if not has_explicit_location_fields:
                return {}
        return {
            "title": str(source.get("title") or source.get("place_name") or source.get("address") or self._localize("\u5730\u56fe\u4f4d\u7f6e", "Map location")).strip(),
            "address": str(source.get("address") or source.get("place_name") or source.get("title") or "").strip(),
            "city": str(source.get("city") or "").strip(),
            "region": str(source.get("region") or "").strip(),
            "country": str(source.get("country") or "").strip(),
            "lat": lat,
            "lon": lon,
            "distance_text": str(source.get("distance_text") or source.get("distance") or "").strip(),
            "map_preview_path": preview_path,
            "map_preview_url": preview_path,
            "external_map_url": str(source.get("external_map_url") or source.get("map_url") or source.get("url") or "").strip(),
            "summary": str(source.get("summary") or structured.get("summary") or "").strip(),
        }

    def _extract_news_payload(self, structured: dict[str, Any]) -> dict[str, Any]:
        items = self._extract_news_items(structured)
        if not items:
            return {}
        return {
            "title": self._localize("\u65b0\u95fb\u901f\u89c8", "News Briefing"),
            "items": items[:8],
        }

    def _extract_generic_info_payload(self, structured: dict[str, Any], payload: dict[str, Any], assistant_text: str) -> dict[str, Any]:
        if not structured:
            return {}
        summary = str(structured.get("summary") or payload.get("summary") or "").strip()
        fields: list[dict[str, str]] = []
        explicit_fields = structured.get("fields")
        if isinstance(explicit_fields, list):
            for item in explicit_fields:
                if not isinstance(item, dict):
                    continue
                label = str(item.get("label") or item.get("name") or "").strip()
                value = str(item.get("value") or "").strip()
                if label and value:
                    fields.append({"label": label, "value": value})
        if not fields:
            for key in ("metric_name", "metric_value", "recommendation"):
                value = str(structured.get(key, "") or "").strip()
                if value:
                    fields.append({"label": key.replace("_", " ").title(), "value": value})
        if not fields and not summary and not assistant_text.strip():
            return {}
        return {
            "title": summary or self._localize("\u7ed3\u6784\u5316\u4fe1\u606f", "Structured info"),
            "summary": self._limit_sentences(assistant_text or summary, max_sentences=3, max_chars=220),
            "fields": fields[:6],
        }

    def _extract_news_items(self, structured: dict[str, Any]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        items.extend(self._news_items_from_list(structured.get("news_items")))
        briefing = structured.get("briefing")
        if isinstance(briefing, dict):
            items.extend(self._news_items_from_list(briefing.get("top_items")))
            items.extend(self._news_items_from_list(briefing.get("project_related")))
        if not items:
            items.extend(self._news_items_from_list(structured.get("project_related")))
        if not items:
            items.extend(self._news_items_from_list(structured.get("observations")))
        article = structured.get("article")
        if not items and isinstance(article, dict):
            analysis = structured.get("analysis") if isinstance(structured.get("analysis"), dict) else {}
            headline = str(article.get("headline") or article.get("title") or "").strip()
            url = str(article.get("url", "") or "").strip()
            summary = str(analysis.get("short_comment", "") or article.get("summary", "") or "").strip()
            source = str(article.get("source") or "").strip()
            tags = list(article.get("tags", []) or [])
            image_url = str(article.get("image_url", "") or article.get("image", "") or "").strip()
            image_path = self.asset_resolver.resolve_news_thumbnail(
                self.thumbnail_service.resolve_image(image_url, namespace="news") if image_url else ""
            )
            if headline and url:
                items.append(
                    {
                        "headline": headline,
                        "source": source,
                        "published_at": str(article.get("published_at") or "").strip(),
                        "summary": summary,
                        "tags": tags,
                        "url": url,
                        "image_path": image_path,
                        "image_url": image_path,
                    }
                )
        return [item for item in items if item.get("headline") and item.get("url")]

    def _news_items_from_list(self, source: Any) -> list[dict[str, Any]]:
        if not isinstance(source, list):
            return []
        normalized: list[dict[str, Any]] = []
        for item in source:
            if not isinstance(item, dict):
                continue
            headline = str(item.get("headline") or item.get("title") or "").strip()
            url = str(item.get("url", "") or "").strip()
            if not headline or not url:
                continue
            image_source = str(item.get("image_url", "") or item.get("image", "") or item.get("thumbnail", "") or "").strip()
            image_path = self.asset_resolver.resolve_news_thumbnail(
                self.thumbnail_service.resolve_image(image_source, namespace="news") if image_source else ""
            )
            normalized.append(
                {
                    "headline": headline,
                    "source": str(item.get("source") or "").strip(),
                    "published_at": str(item.get("published_at") or "").strip(),
                    "summary": str(item.get("summary", "") or item.get("short_comment", "") or "").strip(),
                    "tags": list(item.get("tags", []) or []),
                    "url": url,
                    "image_path": image_path,
                    "image_url": image_path,
                }
            )
        return normalized

    def _resolve_weather_icon_key(self, value: Any) -> str:
        token = str(value or "").strip().lower()
        if not token:
            return "unknown"
        normalized = token.replace("_", "-").replace(" ", "-")
        if any(term in normalized for term in ("fog", "\u96fe")):
            return "fog"
        if any(term in normalized for term in ("storm", "thunder", "\u96f7")):
            return "storm"
        if any(term in normalized for term in ("snow", "\u96ea")):
            return "snow"
        if any(term in normalized for term in ("rain", "shower", "\u96e8")):
            return "rain"
        if any(term in normalized for term in ("cloud", "overcast", "\u4e91", "\u9634")):
            return "cloud"
        if any(term in normalized for term in ("sun", "clear", "\u6674")):
            return "sun"
        return "unknown"

    def _build_news_speech(self, card_data: dict[str, Any], *, fallback: str) -> str:
        items = card_data.get("items")
        if not isinstance(items, list) or not items:
            return self._limit_sentences(fallback, max_sentences=2, max_chars=180)
        top_lines: list[str] = []
        for item in items[:2]:
            if not isinstance(item, dict):
                continue
            headline = str(item.get("headline") or "").strip()
            summary = str(item.get("summary") or "").strip()
            if headline and summary:
                top_lines.append(f"{headline}: {summary}")
            elif headline:
                top_lines.append(headline)
        return self._limit_sentences(" ".join(top_lines) or fallback, max_sentences=2, max_chars=180)

    def _plain_text(self, rich_text: str) -> str:
        if not rich_text:
            return ""
        text = re.sub(r"<br\s*/?>", "\n", rich_text, flags=re.IGNORECASE)
        text = re.sub(r"</(p|div|li|ul|ol|h\d)>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        return html.unescape(text).strip()

    def _limit_sentences(self, text: str, *, max_sentences: int, max_chars: int) -> str:
        compact = " ".join((text or "").split())
        if not compact:
            return ""
        if len(compact) <= max_chars:
            sentence_parts = re.split(r"(?<=[。！？.!?])\s*", compact)
            merged = " ".join(part for part in sentence_parts[:max_sentences] if part)
            return merged or compact
        shortened = compact[: max_chars - 1].rstrip()
        return shortened + "..."

    def _display_number(self, value: Any) -> str:
        try:
            if value in {None, ""}:
                return "--"
            number = float(value)
        except (TypeError, ValueError):
            return "--"
        if number.is_integer():
            return str(int(number))
        return f"{number:.1f}".rstrip("0").rstrip(".")

    def _float_or_none(self, value: Any) -> float | None:
        try:
            if value in {None, ""}:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    def _extract_number(self, text: str, pattern: str) -> float | None:
        match = re.search(pattern, str(text or ""), flags=re.IGNORECASE)
        if not match:
            return None
        try:
            return float(match.group(1))
        except (TypeError, ValueError):
            return None

    def _localize(self, zh_text: str, en_text: str) -> str:
        return en_text if self.language.startswith("en") else zh_text
