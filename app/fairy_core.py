from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

from app.app_preferences import load_app_preferences
from app.ai.llm_client import LLMClient
from app.capabilities.browser_capability import BrowserCapability
from app.capabilities.command_capability import CommandCapability
from app.capabilities.document_capability import DocumentCapability
from app.capabilities.screen_capability import ScreenCapability
from app.mcp_client_layer import MCPClientLayer
from app.memory import MemoryManager
from app.news.news_service import NewsService
from app.models.skill_result import SkillResult
from app.persona import PersonaEngine, get_effective_persona_mode
from app.persona.tone_preference import build_fairy_tone_preference_result, looks_like_fairy_tone_preference
from app.prompts import build_json_helper_prompt
from app.rag import RagManager, get_reindex_manager
from app.route_context import RouteContext
from app.storage.repositories import ActionLogRepo
from app.system_notifications import NotificationEngine
from app.tool_registry import ToolRegistry
from app.tools.browser.http_page_loader import crawl_webpage
from app.tools.search.html_search import search_web_detailed, search_web_queries

from app.lazy_runtime.lazy_dispatcher import LazyDispatcher
from app.runtime.execution_policy import apply_strict_mode, should_apply_strict_mode
from app.skills.bundles.runtime_types import BundleRuntimeServices
from app.web_access.browser_executor import BrowserExecutor
from app.web_access.bundle_runtime import WebRuntimeFacade
from app.web_access.execution_ladder import ExecutionLadder
from app.web_access.retrieval_plan_builder import RetrievalPlanBuilder
from app.web_access.visual_reader import VisualReader
from app.web_access.access_resolver import WebAccessResolver


logger = logging.getLogger(__name__)
CoreEventCallback = Callable[[str, dict[str, Any]], None]
ResponseChunkCallback = Callable[[str], None]

_SPEC_REQUEST_MARKERS = (
    "参数",
    "配置",
    "规格",
    "spec",
    "specs",
    "specification",
    "specifications",
    "technical specifications",
)
_SPEC_FIELD_ORDER = (
    "芯片",
    "屏幕",
    "存储",
    "摄像头",
    "接口",
    "尺寸重量",
    "续航",
)
_SPEC_FIELD_TOKENS: dict[str, tuple[str, ...]] = {
    "芯片": ("芯片", "处理器", "chip", "processor", "soc", "cpu", "m4", "m3", "m2", "a17", "a18"),
    "屏幕": ("屏幕", "显示屏", "display", "screen", "xdr", "retina", "oled", "promotion", "resolution", "nits", "inch", "尺寸"),
    "存储": ("存储", "storage", "capacity", "gb", "tb"),
    "摄像头": ("摄像头", "camera", "front camera", "rear camera", "ultra wide", "megapixel", "mp", "lidar"),
    "接口": ("接口", "端口", "port", "connector", "usb-c", "thunderbolt", "wifi", "bluetooth", "cellular"),
    "尺寸重量": ("尺寸重量", "尺寸", "重量", "dimensions", "dimension", "size", "height", "width", "depth", "weight", "mm", "g"),
    "续航": ("续航", "电池", "battery", "power", "video playback", "wireless web"),
}
_SPEC_SKIP_LINE_TOKENS = (
    "section_hint=",
    "buy",
    "shop",
    "learn more",
    "overview",
    "support",
    "compare",
    "contact us",
)
_COMPARE_REQUEST_MARKERS = ("区别", "差异", "对比", "比较", " vs ", "versus", "compare", "comparison")
_RELEASE_REQUEST_MARKERS = ("发布", "发售", "上市", "release date", "released", "announced", "launch date")
_WEB_BRIEF_REQUEST_MARKERS = (
    "官网",
    "文档",
    "docs",
    "guide",
    "教程",
    "how to",
    "what is",
    "about",
    "overview",
    "介绍",
    "怎么用",
    "如何",
)
_RELEASE_STATUS_MARKERS = (
    "available now",
    "coming soon",
    "now available",
    "ships",
    "pre-order",
    "现已发售",
    "即将发售",
    "开放预购",
    "已发布",
    "已上市",
)
_BRIEF_SKIP_LINE_TOKENS = (
    "buy",
    "shop",
    "learn more",
    "sign in",
    "log in",
    "contact us",
    "support",
    "privacy",
    "cookies",
)
class FairySkillLLMHelper:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm
        self.memory_context = ""

    def set_memory_context(self, memory_context: str) -> None:
        self.memory_context = memory_context.strip()

    def summarize_web_result(
        self,
        user_request: str,
        pages: list[dict[str, Any]],
        comparison: dict[str, Any],
        *,
        memory_context: str = "",
        task_type_hint: str = "",
        answer_focus: str = "",
    ) -> dict[str, Any]:
        metric_result = self._extract_metric_summary(user_request, pages)
        if metric_result is not None:
            return metric_result
        hinted_task_type = self._normalize_web_task_type(task_type_hint)
        evidence_task_type = self._infer_web_task_type_from_evidence(pages, comparison)
        model_answer = self._model_synthesize_web_answer(
            user_request=user_request,
            pages=pages,
            comparison=comparison,
            task_type_hint=hinted_task_type or evidence_task_type,
            answer_focus=answer_focus,
            memory_context=memory_context,
        )
        effective_task_type = (
            self._normalize_web_task_type(str((model_answer or {}).get("task_type") or ""))
            or hinted_task_type
            or evidence_task_type
        )
        formatted = self._build_web_card_payload(
            task_type=effective_task_type,
            user_request=user_request,
            pages=pages,
            comparison=comparison,
        )
        card_blob = dict((formatted or {}).get("card") or {})
        card_type = str(card_blob.get("type") or effective_task_type or "").strip().lower()
        summary = str((model_answer or {}).get("summary") or "").strip()
        response_text = str((model_answer or {}).get("response_text") or "").strip()
        recommendation = str((model_answer or {}).get("recommendation") or "").strip()
        if not summary:
            summary = str((formatted or {}).get("summary") or "").strip()
        if not response_text:
            response_text = str((formatted or {}).get("response_text") or summary or "").strip()
        if not recommendation:
            recommendation = str((formatted or {}).get("recommendation") or "").strip()
        if card_blob:
            card_data = dict(card_blob.get("data") or {})
            if summary and not str(card_data.get("summary") or "").strip():
                card_data["summary"] = summary
            card_blob["data"] = card_data
        else:
            card_type = card_type or "web_brief"
            card_blob = {
                "type": card_type,
                "data": {
                    "title": "网页结果",
                    "summary": summary or "当前页面能确认到一些相关信息。",
                    "bullets": [],
                    "source_url": "",
                    "source_label": "",
                },
            }
        result = {
            "summary": summary or "当前页面能确认到一些相关信息。",
            "recommendation": recommendation,
            "response_text": response_text or "当前页面能确认到一些相关信息，但还不足以整理成更完整的结论。",
            "card": card_blob,
        }
        if isinstance((formatted or {}).get("fields"), list):
            result["fields"] = list((formatted or {}).get("fields") or [])
        if str((formatted or {}).get("matched_url") or "").strip():
            result["matched_url"] = str((formatted or {}).get("matched_url") or "").strip()
        return result

    def _model_synthesize_web_answer(
        self,
        *,
        user_request: str,
        pages: list[dict[str, Any]],
        comparison: dict[str, Any],
        task_type_hint: str,
        answer_focus: str,
        memory_context: str,
    ) -> dict[str, Any]:
        prompt = {
            "user_request": user_request,
            "task_type_hint": task_type_hint,
            "answer_focus": str(answer_focus or "").strip(),
            "pages": [
                {
                    "title": str(page.get("title") or "").strip(),
                    "url": str(page.get("url") or "").strip(),
                    "headings": list(page.get("headings") or [])[:8],
                    "key_values": list(page.get("key_values") or [])[:10],
                    "table_rows": list(page.get("table_rows") or [])[:10],
                    "body_text_excerpt": str(page.get("body_text") or "")[:2200],
                }
                for page in pages[:3]
                if isinstance(page, dict)
            ],
            "comparison": {
                "items": list(comparison.get("items") or [])[:4] if isinstance(comparison, dict) else [],
                "differences": list(comparison.get("differences") or [])[:6] if isinstance(comparison, dict) else [],
                "recommendation": str(comparison.get("recommendation") or "").strip() if isinstance(comparison, dict) else "",
            },
        }
        try:
            response = self.llm.execute_task(
                self._with_runtime_context(
                    build_json_helper_prompt(
                        keys=("task_type", "summary", "recommendation", "response_text"),
                        extra_rules=(
                            "Infer task_type semantically from the request and evidence. Allowed values: specs, compare, news, release, product_lookup, general_info.",
                            "When answer_focus is provided, make it the primary target of the response.",
                            "Write concise Chinese.",
                            "response_text should answer directly in 2-4 natural sentences without process narration.",
                        ),
                    ),
                    memory_context,
                ),
                json.dumps(prompt, ensure_ascii=False),
                max_tokens=520,
                temperature=0.1,
                instruction_label="Web answer synthesis",
            )
        except Exception:
            logger.debug("web_answer_synthesis_failed", exc_info=True)
            return {}
        parsed = self._parse_json_or_fallback(str(response.text or ""), {})
        if not isinstance(parsed, dict):
            return {}
        task_type = self._normalize_web_task_type(str(parsed.get("task_type") or ""))
        return {
            "task_type": task_type,
            "summary": str(parsed.get("summary") or "").strip(),
            "recommendation": str(parsed.get("recommendation") or "").strip(),
            "response_text": str(parsed.get("response_text") or "").strip(),
        }

    @staticmethod
    def _normalize_web_task_type(task_type: str) -> str:
        normalized = str(task_type or "").strip().lower()
        if normalized in {"specs", "compare", "news", "release", "product_lookup", "general_info"}:
            return normalized
        legacy_aliases = {
            "general_fact_lookup": "general_info",
            "browser_navigation": "general_info",
            "document_read": "general_info",
            "product_price_lookup": "product_lookup",
        }
        return legacy_aliases.get(normalized, "")

    def _build_web_card_payload(
        self,
        *,
        task_type: str,
        user_request: str,
        pages: list[dict[str, Any]],
        comparison: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_task = str(task_type or "").strip().lower()
        if normalized_task == "compare":
            return self._extract_compare_summary(user_request, pages, comparison, force=True) or {}
        if normalized_task == "specs":
            return self._extract_specs_summary(user_request, pages, force=True) or {}
        if normalized_task == "release":
            return self._extract_release_summary(user_request, pages, force=True) or {}
        if normalized_task == "news":
            return self._extract_release_summary(user_request, pages, force=True) or self._extract_web_brief_summary(user_request, pages, force=True) or {}
        if normalized_task in {"product_lookup", "general_info"}:
            return self._extract_web_brief_summary(user_request, pages, force=True) or {}
        if comparison and list(comparison.get("items") or []):
            return self._extract_compare_summary(user_request, pages, comparison, force=True) or {}
        return self._extract_web_brief_summary(user_request, pages, force=True) or {}

    def _infer_web_task_type_from_evidence(self, pages: list[dict[str, Any]], comparison: dict[str, Any]) -> str:
        if isinstance(comparison, dict) and len(list(comparison.get("items") or [])) >= 2:
            return "compare"
        combined = " ".join(
            str(item).lower()
            for page in pages[:3]
            if isinstance(page, dict)
            for item in (
                page.get("title"),
                " ".join(list(page.get("headings") or [])[:4]),
                " ".join(list(page.get("key_values") or [])[:6]),
                " ".join(list(page.get("table_rows") or [])[:6]),
                str(page.get("body_text") or "")[:600],
            )
            if item
        )
        if any(token in combined for token in ("spec", "technical specifications", "参数", "配置", "规格", "display", "battery", "storage")):
            return "specs"
        if any(token in combined for token in ("announced", "available now", "coming soon", "发布", "发售", "上市", "newsroom", "press release")):
            return "release"
        return "general_info"

    def _extract_metric_summary(self, user_request: str, pages: list[dict[str, Any]]) -> dict[str, Any] | None:
        lowered_request = user_request.lower()
        if not any(marker in lowered_request for marker in ("粉丝", "粉丝数", "followers", "subscriber")):
            return None

        subject = self._extract_metric_subject(user_request)
        candidates: list[dict[str, Any]] = []
        for page in pages:
            domain = str((page.get("structured_signals") or {}).get("domain", "")).lower()
            body_text = str(page.get("body_text", "") or "")
            if not body_text.strip():
                continue
            candidates.extend(self._collect_metric_candidates(body_text, subject, page, domain))

        if not candidates:
            return None

        best = sorted(candidates, key=lambda item: (-int(item["score"]), item["line_index"]))[0]
        follower_count = str(best["metric"])
        channel_name = str(best["name"])
        summary = f"“{channel_name}”当前识别到的粉丝数约为 {follower_count}。"
        recommendation = "建议打开对应主页再次确认，因为平台展示数字可能随时间变化。"
        response_text = f"“{channel_name}”当前识别到的粉丝数约为 {follower_count}。"
        source_meta = self._build_source_meta(str(best.get("url") or "").strip())
        return {
            "summary": summary,
            "recommendation": recommendation,
            "response_text": response_text,
            "metric_name": "followers",
            "metric_value": follower_count,
            "matched_name": channel_name,
            "matched_url": best.get("url", ""),
            "card": {
                "type": "generic_info",
                "data": {
                    "title": channel_name,
                    "summary": summary,
                    "fields": [
                        {"label": "粉丝数", "value": follower_count},
                    ],
                    **source_meta,
                },
            },
        }

    def _extract_compare_summary(
        self,
        user_request: str,
        pages: list[dict[str, Any]],
        comparison: dict[str, Any],
        *,
        force: bool = False,
    ) -> dict[str, Any] | None:
        lowered_request = user_request.lower()
        if not force and not any(marker in lowered_request for marker in _COMPARE_REQUEST_MARKERS):
            return None
        items = list(comparison.get("items") or [])
        if len(items) < 2:
            return None

        normalized_items: list[dict[str, Any]] = []
        for item in items[:4]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("url") or "").strip()
            if not title:
                continue
            highlights: list[str] = []
            for raw in list(item.get("specs") or [])[:3]:
                value = str(raw or "").strip()
                if value:
                    highlights.append(value)
            for raw in list(item.get("prices") or [])[:2]:
                value = str(raw or "").strip()
                if value and value not in highlights:
                    highlights.append(value)
            if not highlights:
                for raw in list(item.get("key_values") or [])[:2]:
                    value = str(raw or "").strip()
                    if value:
                        highlights.append(value)
            normalized_items.append(
                {
                    "title": title,
                    "url": str(item.get("url") or "").strip(),
                    "highlights": highlights[:3],
                    "summary": str(item.get("summary") or "").strip(),
                }
            )
        if len(normalized_items) < 2:
            return None

        shared_points = [str(item).strip() for item in list(comparison.get("shared_points") or []) if str(item).strip()][:5]
        differences = [str(item).strip() for item in list(comparison.get("differences") or []) if str(item).strip()][:6]
        title = self._extract_compare_subject(user_request, normalized_items)
        leading_difference = f"{title}的主要区别在于：{'；'.join(differences[:3])}。" if differences else ""
        if not leading_difference:
            contrast_parts: list[str] = []
            for item in normalized_items[:2]:
                item_title = str(item.get("title") or "").strip()
                highlights = [str(raw).strip() for raw in list(item.get("highlights") or []) if str(raw).strip()]
                if item_title and highlights:
                    contrast_parts.append(f"{item_title}更偏向{' / '.join(highlights[:2])}")
            if contrast_parts:
                leading_difference = "；".join(contrast_parts) + "。"
        summary = leading_difference or f"{title}各有侧重。"
        recommendation = str(comparison.get("recommendation") or "如需继续，我可以把差异收敛成购买建议或按参数逐项展开。").strip()
        response_text = self._join_sentences(summary, recommendation)
        source_url = ""
        collected_sources = self._collect_sources(pages)
        if collected_sources:
            source_url = str(collected_sources[0].get("url") or "").strip()
        source_meta = self._build_source_meta(source_url)
        return {
            "summary": summary,
            "recommendation": recommendation,
            "response_text": response_text,
            "card": {
                "type": "compare",
                "data": {
                    "title": title,
                    "summary": summary,
                    "items": normalized_items,
                    "shared_points": shared_points,
                    "differences": differences,
                    "recommendation": recommendation,
                    "sources": collected_sources,
                    **source_meta,
                },
            },
        }

    def _extract_specs_summary(self, user_request: str, pages: list[dict[str, Any]], *, force: bool = False) -> dict[str, Any] | None:
        lowered_request = user_request.lower()
        if not force and not any(marker in lowered_request for marker in _SPEC_REQUEST_MARKERS):
            return None

        best_fields: dict[str, dict[str, Any]] = {}
        matched_url = ""
        subject = ""
        for page in pages:
            if not isinstance(page, dict):
                continue
            if not matched_url:
                matched_url = str(page.get("url") or "").strip()
            if not subject:
                subject = self._extract_specs_subject(user_request, page)
            for line in self._spec_candidate_lines(page):
                parsed = self._parse_spec_candidate(line)
                if parsed is None:
                    continue
                label, value, score = parsed
                existing = best_fields.get(label)
                if existing is None or int(score) > int(existing.get("score") or 0):
                    best_fields[label] = {"label": label, "value": value, "score": score}

        ordered_fields = [
            {"label": label, "value": str(best_fields[label]["value"]).strip()}
            for label in _SPEC_FIELD_ORDER
            if label in best_fields and str(best_fields[label]["value"]).strip()
        ]
        if not ordered_fields:
            return None

        subject = subject or "当前产品"
        response_fields = ordered_fields[:4]
        field_text = "；".join(
            f"{item['label']}：{self._limit_spec_value(item['value'], max_chars=42)}"
            for item in response_fields
        )
        if len(response_fields) >= 2:
            summary = f"{subject}的主要参数包括：{field_text}。"
        else:
            summary = f"当前页面只确认到 {subject} 的部分参数：{field_text}。"
        recommendation = "如果你要，我可以继续按尺寸、存储版本或不同代际帮你对比。"
        response_text = summary
        source_meta = self._build_source_meta(matched_url)
        return {
            "summary": summary,
            "recommendation": recommendation,
            "response_text": response_text,
            "fields": ordered_fields[:6],
            "matched_url": matched_url,
            "card": {
                "type": "specs",
                "data": {
                    "title": subject,
                    "summary": summary,
                    "fields": ordered_fields[:6],
                    **source_meta,
                },
            },
        }

    def _extract_release_summary(self, user_request: str, pages: list[dict[str, Any]], *, force: bool = False) -> dict[str, Any] | None:
        lowered_request = user_request.lower()
        if not force and not any(marker in lowered_request for marker in _RELEASE_REQUEST_MARKERS):
            return None
        if not pages:
            return None
        page = next((item for item in pages if isinstance(item, dict) and (item.get("body_text") or item.get("title"))), None)
        if page is None:
            return None

        title = self._extract_title_subject(str(page.get("title") or "").strip()) or "当前条目"
        lines = self._collect_brief_lines(page, limit=8)
        date_text = self._extract_release_date(page, lines)
        status_text = self._extract_release_status(lines)
        highlights = [line for line in lines if line not in {date_text, status_text}][:4]
        if not date_text and not highlights and not status_text:
            return None

        release_state = self._classify_release_state(title, status_text, lines)
        summary_bits = [self._release_lead(title, release_state)]
        if date_text:
            summary_bits.append(f"当前可确认的时间是 {date_text}。")
        normalized_status = self._normalize_release_status_text(status_text, release_state)
        if normalized_status:
            summary_bits.append(f"{normalized_status}。")
        summary = self._join_sentences(*summary_bits)
        response_text = summary
        if highlights:
            response_text = self._join_sentences(response_text, f"关键信息包括：{'；'.join(highlights[:2])}。")
        recommendation = "如需继续，我可以再帮你核对不同地区发布时间、价格或官方公告原文。"
        source_meta = self._build_source_meta(str(page.get("url") or "").strip())
        return {
            "summary": summary,
            "recommendation": recommendation,
            "response_text": response_text,
            "card": {
                "type": "release",
                "data": {
                    "title": title,
                    "summary": summary,
                    "date": date_text,
                    "status": normalized_status,
                    "highlights": highlights,
                    **source_meta,
                },
            },
        }

    def _extract_web_brief_summary(self, user_request: str, pages: list[dict[str, Any]], *, force: bool = False) -> dict[str, Any] | None:
        if not pages:
            return None
        lowered_request = user_request.lower()
        explicit_web_brief = force or any(marker in lowered_request for marker in _WEB_BRIEF_REQUEST_MARKERS)
        page = next((item for item in pages if isinstance(item, dict) and (item.get("body_text") or item.get("title"))), None)
        if page is None:
            return None
        title = self._extract_title_subject(str(page.get("title") or "").strip()) or "网页结果"
        summary = self._extract_web_brief_text(page)
        bullets = self._collect_brief_lines(page, limit=4)
        if not explicit_web_brief and not summary and not bullets:
            return None
        summary, bullets = self._normalize_web_brief_content(title, page, summary, bullets)
        if not bullets:
            bullets = [summary]
        response_text = summary
        recommendation = "如果你要，我可以继续深入到价格、功能差异或官方文档细节。"
        source_meta = self._build_source_meta(str(page.get("url") or "").strip())
        return {
            "summary": summary,
            "recommendation": recommendation,
            "response_text": response_text,
            "card": {
                "type": "web_brief",
                "data": {
                    "title": title,
                    "summary": summary,
                    "bullets": bullets[:4],
                    **source_meta,
                },
            },
        }

    def _extract_compare_subject(self, user_request: str, items: list[dict[str, Any]]) -> str:
        cleaned = str(user_request or "").strip()
        for marker in _COMPARE_REQUEST_MARKERS:
            cleaned = cleaned.replace(marker, " ")
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -|,;，；。")
        if cleaned:
            return cleaned
        return " vs ".join(str(item.get("title") or "").strip() for item in items[:2] if str(item.get("title") or "").strip())

    def _extract_title_subject(self, title: str) -> str:
        cleaned = str(title or "").strip()
        if not cleaned:
            return ""
        parts = [part.strip() for part in re.split(r"\s*[-|｜:：]\s*", cleaned) if part.strip()]
        if not parts:
            return ""
        first = parts[0].lower()
        if first in {"apple newsroom", "newsroom", "press release"} and len(parts) >= 2:
            return parts[-1]
        return parts[0]

    def _build_source_meta(self, source_url: str, *, title: str = "") -> dict[str, str]:
        cleaned_url = str(source_url or "").strip()
        label = str(title or "").strip()
        if cleaned_url:
            host = urlparse(cleaned_url).netloc.strip().lower()
            if host.startswith("www."):
                host = host[4:]
            if host:
                label = host
        return {
            "source_url": cleaned_url,
            "source_label": label,
        }

    def _classify_release_state(self, title: str, status_text: str, lines: list[str]) -> str:
        haystack = " ".join([title, status_text, *lines[:6]]).lower()
        if any(token in haystack for token in ("coming soon", "即将发售", "即将上市", "未发布", "future release")):
            return "upcoming"
        if any(
            token in haystack
            for token in (
                "available now",
                "now available",
                "ships",
                "pre-order",
                "现已发售",
                "已发布",
                "已上市",
                "开放预购",
                "announced",
                "today announced",
            )
        ):
            return "released"
        return "mentioned"

    def _release_lead(self, title: str, state: str) -> str:
        if state == "released":
            return f"{title} 已发布。"
        if state == "upcoming":
            return f"{title} 当前页面显示尚未正式发售。"
        return f"当前页面已经提到 {title} 的发布信息。"

    def _normalize_release_status_text(self, status_text: str, state: str) -> str:
        cleaned = re.sub(r"\s+", " ", str(status_text or "").strip())
        if not cleaned:
            return ""
        if state == "released":
            return "当前页面显示已上线或已开放购买"
        if state == "upcoming":
            return "当前页面显示即将发售"
        return self._limit_spec_value(cleaned, max_chars=48)

    def _normalize_web_brief_content(
        self,
        title: str,
        page: dict[str, Any],
        summary: str,
        bullets: list[str],
    ) -> tuple[str, list[str]]:
        cleaned_summary = re.sub(r"\s+", " ", str(summary or "").strip())
        cleaned_bullets = [re.sub(r"\s+", " ", str(item or "").strip()) for item in bullets if str(item or "").strip()]
        if self._contains_cjk(cleaned_summary):
            return cleaned_summary, cleaned_bullets[:4]

        context = " ".join(
            item
            for item in [
                title,
                cleaned_summary,
                " ".join(cleaned_bullets[:3]),
                str(page.get("body_text") or "")[:600],
                str(page.get("url") or ""),
            ]
            if item
        ).lower()
        fallback_summary = f"{title} 主要介绍相关产品、功能和使用信息。"
        fallback_bullets = [
            "提供页面概览和核心说明",
            "包含当前页面的主要功能信息",
        ]
        if any(token in context for token in ("api", "sdk", "developer", "developers", "docs", "documentation", "guide", "build")):
            return (
                f"{title} 主要提供 API、模型能力说明和开发文档。",
                [
                    "提供 API 文档和接入指南",
                    "介绍模型能力、接口和示例",
                    "适合开发者查阅集成信息",
                ],
            )
        if any(token in context for token in ("pricing", "price", "plan", "plans", "subscription")):
            return (
                f"{title} 主要介绍价格、套餐和订阅方案。",
                [
                    "说明不同套餐和价格区间",
                    "展示订阅方案和使用限制",
                ],
            )
        if any(token in context for token in ("download", "install", "setup", "get started")):
            return (
                f"{title} 主要提供下载入口、安装说明和上手指引。",
                [
                    "提供下载或安装入口",
                    "包含基础配置和使用说明",
                ],
            )
        if any(token in context for token in ("news", "newsroom", "blog", "press")):
            return (
                f"{title} 主要发布更新、公告和新闻内容。",
                [
                    "包含官方更新和公告",
                    "适合查看最新动态和发布信息",
                ],
            )
        if cleaned_summary:
            fallback_summary = f"{title} 主要介绍当前页面的核心信息。"
        return fallback_summary, fallback_bullets

    @staticmethod
    def _contains_cjk(text: str) -> bool:
        return bool(re.search(r"[\u4e00-\u9fff]", str(text or "")))

    @staticmethod
    def _join_sentences(*parts: str) -> str:
        normalized: list[str] = []
        for raw in parts:
            text = str(raw or "").strip()
            if not text:
                continue
            if text[-1] not in "。！？!?":
                text += "。"
            normalized.append(text)
        return " ".join(normalized)

    def _collect_sources(self, pages: list[dict[str, Any]]) -> list[dict[str, str]]:
        sources: list[dict[str, str]] = []
        seen: set[str] = set()
        for page in pages:
            if not isinstance(page, dict):
                continue
            url = str(page.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            sources.append(
                {
                    "title": str(page.get("title") or url).strip(),
                    "url": url,
                }
            )
        return sources[:4]

    def _collect_brief_lines(self, page: dict[str, Any], *, limit: int) -> list[str]:
        candidates: list[str] = []
        seen: set[str] = set()

        def add_line(raw: Any) -> None:
            cleaned = re.sub(r"\s+", " ", str(raw or "").replace("\u00a0", " ").strip())
            lowered = cleaned.lower()
            if len(cleaned) < 6 or lowered in seen:
                return
            if any(token in lowered for token in _BRIEF_SKIP_LINE_TOKENS):
                return
            seen.add(lowered)
            candidates.append(cleaned)

        for item in list(page.get("key_values") or [])[:8]:
            add_line(item)
        for item in list(page.get("table_rows") or [])[:8]:
            add_line(item)
        for item in list(page.get("headings") or [])[:6]:
            add_line(item)
        for item in str(page.get("body_text") or "").splitlines()[:40]:
            add_line(item)
        return candidates[:limit]

    def _extract_release_date(self, page: dict[str, Any], lines: list[str]) -> str:
        structured_signals = page.get("structured_signals") if isinstance(page.get("structured_signals"), dict) else {}
        date_text = str(structured_signals.get("date") or "").strip()
        if date_text:
            return date_text
        text = "\n".join(lines + [str(page.get("body_text") or "")[:1600]])
        match = re.search(r"(20\d{2}[-/年]\d{1,2}(?:[-/月]\d{1,2})?)", text)
        if match:
            return match.group(1)
        return ""

    def _extract_release_status(self, lines: list[str]) -> str:
        for line in lines:
            lowered = line.lower()
            if any(token in lowered for token in _RELEASE_STATUS_MARKERS):
                return line
        return ""

    def _extract_web_brief_text(self, page: dict[str, Any]) -> str:
        meta_description = str(page.get("meta_description") or "").strip()
        if meta_description:
            return self._limit_spec_value(meta_description, max_chars=140)
        lines = self._collect_brief_lines(page, limit=3)
        if lines:
            return self._limit_spec_value(lines[0], max_chars=140)
        body_text = re.sub(r"\s+", " ", str(page.get("body_text") or "").strip())
        if body_text:
            return self._limit_spec_value(body_text, max_chars=140)
        return ""

    def _extract_specs_subject(self, user_request: str, page: dict[str, Any]) -> str:
        title = str(page.get("title") or "").strip()
        if title:
            subject = re.split(r"\s*[-|｜:：]\s*", title, maxsplit=1)[0].strip()
            if subject:
                return subject

        subject = user_request.strip()
        for prefix in ("帮我查查", "帮我查一下", "帮我搜一下", "请帮我", "请", "麻烦", "查查", "查一下", "搜一下", "搜索一下"):
            if subject.startswith(prefix):
                subject = subject[len(prefix) :].strip()
                break
        for marker in ("的参数", "参数", "配置", "规格", "specifications", "specification", "specs", "spec"):
            subject = subject.replace(marker, " ")
        subject = re.sub(r"\s+", " ", subject).strip()
        return subject

    def _spec_candidate_lines(self, page: dict[str, Any]) -> list[str]:
        lines: list[str] = []
        seen: set[str] = set()
        snapshot = page.get("snapshot") if isinstance(page.get("snapshot"), dict) else {}

        def add_line(raw: Any) -> None:
            cleaned = re.sub(r"\s+", " ", str(raw or "").replace("\u00a0", " ").strip())
            if len(cleaned) < 3:
                return
            lowered = cleaned.lower()
            if lowered in seen:
                return
            if any(token in lowered for token in _SPEC_SKIP_LINE_TOKENS):
                return
            seen.add(lowered)
            lines.append(cleaned)

        for item in list(page.get("key_values") or []):
            add_line(item)
        for item in list(page.get("table_rows") or []):
            add_line(item)
        for item in list(snapshot.get("key_values") or []):
            add_line(item)
        for item in list(snapshot.get("table_rows") or []):
            add_line(item)
        for item in list(page.get("headings") or [])[:8]:
            add_line(item)
        for item in str(page.get("body_text") or "").splitlines()[:120]:
            add_line(item)
        return lines

    def _parse_spec_candidate(self, line: str) -> tuple[str, str, int] | None:
        cleaned = re.sub(r"\s+", " ", str(line or "").strip())
        if len(cleaned) < 3:
            return None
        lowered = cleaned.lower()
        explicit_label = ""
        value = ""
        score = 0

        if " | " in cleaned:
            parts = [part.strip() for part in cleaned.split("|") if part.strip()]
            if len(parts) >= 2:
                explicit_label = parts[0]
                value = " / ".join(parts[1:])
                score += 3
        elif re.search(r"[:：=]", cleaned):
            parts = re.split(r"\s*[:：=]\s*", cleaned, maxsplit=1)
            if len(parts) == 2:
                explicit_label = parts[0].strip()
                value = parts[1].strip()
                score += 3

        label = self._match_spec_field(explicit_label or cleaned)
        if not label:
            return None
        if explicit_label:
            score += 2
        score += sum(1 for token in _SPEC_FIELD_TOKENS.get(label, ()) if token in lowered)

        candidate_value = value or cleaned
        if re.search(r"\d", candidate_value):
            score += 1
        normalized_value = self._normalize_spec_value(candidate_value, label)
        if not normalized_value:
            return None
        return label, normalized_value, score

    def _match_spec_field(self, text: str) -> str:
        lowered = str(text or "").strip().lower()
        best_label = ""
        best_score = 0
        for label, tokens in _SPEC_FIELD_TOKENS.items():
            score = sum(1 for token in tokens if token in lowered)
            if score > best_score:
                best_label = label
                best_score = score
        return best_label if best_score > 0 else ""

    def _normalize_spec_value(self, value: str, label: str) -> str:
        cleaned = re.sub(r"\s+", " ", str(value or "").strip(" -|,;，；。"))
        if not cleaned:
            return ""
        if len(cleaned) > 120:
            cleaned = cleaned[:120].rstrip(" ,;，；。")
        label_text = self._match_spec_field(cleaned)
        if cleaned == label or (label_text == label and len(cleaned) <= len(label) + 2):
            return ""
        return cleaned

    @staticmethod
    def _limit_spec_value(value: str, *, max_chars: int) -> str:
        cleaned = str(value or "").strip()
        if len(cleaned) <= max_chars:
            return cleaned
        return cleaned[: max_chars - 1].rstrip(" ,;，；。") + "…"

    def _extract_metric_subject(self, user_request: str) -> str:
        subject = user_request.strip()
        for prefix in ("帮我查查", "帮我查一下", "帮我搜一下", "帮我搜索一下", "帮我", "请帮我", "请", "麻烦", "查查", "查一下", "搜一下", "搜索一下"):
            if subject.startswith(prefix):
                subject = subject[len(prefix) :].strip()
                break
        for marker in ("b站里", "B站里", "b站", "B站", "哔哩哔哩", "哔站", "bilibili"):
            subject = subject.replace(marker, " ")
        for marker in ("里", "有多少粉丝了", "有多少粉丝", "粉丝数", "粉丝", "现在", "目前", "当前", "的"):
            subject = subject.replace(marker, " ")
        subject = re.sub(r"\s+", " ", subject).strip()
        return subject

    def _collect_metric_candidates(self, body_text: str, subject: str, page: dict[str, Any], domain: str) -> list[dict[str, Any]]:
        lines = [line.strip() for line in body_text.splitlines() if line.strip()]
        if len(lines) < 2:
            return []
        subject_lower = subject.lower()
        candidates: list[dict[str, Any]] = []
        for idx, line in enumerate(lines[:-1]):
            next_line = lines[idx + 1]
            metric_match = self._extract_followers_from_line(next_line)
            if metric_match is None:
                continue
            score = 0
            normalized_name = line.lower().replace(" ", "")
            normalized_subject = subject_lower.replace(" ", "")
            if normalized_subject and normalized_subject in normalized_name:
                score += 100
            elif normalized_subject:
                shared = sum(1 for ch in set(normalized_subject) if ch in normalized_name)
                score += shared * 8
            if "bilibili" in domain:
                score += 20
            candidates.append(
                {
                    "name": line,
                    "metric": metric_match,
                    "score": score,
                    "line_index": idx,
                    "url": page.get("url", ""),
                }
            )
        return candidates

    def _extract_followers_from_line(self, line: str) -> str | None:
        match = re.search(r"(\d+(?:\.\d+)?(?:万|亿)?)\s*粉丝", line)
        if match:
            return match.group(1)
        return None

    def summarize_document_result(
        self,
        user_request: str,
        target_path: str,
        summary_text: str,
        sections: list[str],
        revised_path: str,
        warnings: list[str],
        *,
        memory_context: str = "",
    ) -> dict[str, Any]:
        prompt = {
            "user_request": user_request,
            "target_path": target_path,
            "summary_text": summary_text,
            "sections": sections,
            "revised_path": revised_path,
            "warnings": warnings,
        }
        response = self.llm.execute_task(
            self._with_runtime_context(
                build_json_helper_prompt(
                    keys=("summary", "recommendation", "response_text"),
                    extra_rules=(
                        "Write concise Chinese.",
                        "If revised_path exists, mention that a new copy was written.",
                    ),
                ),
                memory_context,
            ),
            json.dumps(prompt, ensure_ascii=False),
            max_tokens=320,
            temperature=0.1,
            instruction_label="Tool helper instructions",
        )
        fallback_text = "请求已接收。文档已读取并整理。"
        if revised_path:
            fallback_text += " 修订版已写入新文件。"
        return self._parse_json_or_fallback(
            response.text,
            fallback={
                "summary": summary_text[:300],
                "recommendation": "如需继续修改，请提供更具体要求。",
                "response_text": fallback_text,
            },
        )

    def summarize_screen_result(
        self,
        user_request: str,
        active_app: dict[str, Any],
        description: str,
        *,
        memory_context: str = "",
    ) -> dict[str, Any]:
        prompt = {
            "user_request": user_request,
            "active_app": active_app,
            "description": description,
        }
        response = self.llm.execute_task(
            self._with_runtime_context(
                build_json_helper_prompt(
                    keys=("summary", "important_regions", "actionable_elements", "suggested_next_step", "response_text"),
                    extra_rules=("Write concise Chinese.",),
                ),
                memory_context,
            ),
            json.dumps(prompt, ensure_ascii=False),
            max_tokens=420,
            temperature=0.1,
            instruction_label="Tool helper instructions",
        )
        return self._parse_json_or_fallback(
            response.text,
            fallback={
                "summary": description[:400],
                "important_regions": [],
                "actionable_elements": [],
                "suggested_next_step": "建议先查看当前焦点区域和最显著的主按钮。",
                "response_text": "屏幕内容已读取。界面结构已初步识别。建议先查看最显著的主按钮和当前焦点区域。",
            },
        )

    def _with_runtime_context(self, system_prompt: str, memory_context: str = "") -> str:
        sections = [system_prompt.strip()]
        effective_memory = (memory_context or self.memory_context or "").strip()
        if effective_memory:
            sections.append(effective_memory)
        return "\n\n".join(section for section in sections if section)

    def _parse_json_or_fallback(self, text: str, fallback: dict[str, Any]) -> dict[str, Any]:
        try:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                return json.loads(text[start : end + 1])
        except Exception:
            pass
        return fallback


class FairyCore:
    def __init__(
        self,
        llm: LLMClient,
        *,
        event_callback: CoreEventCallback | None = None,
        response_chunk_callback: ResponseChunkCallback | None = None,
        web_runtime: Any | None = None,
    ) -> None:
        self.llm = llm
        self.event_callback = event_callback
        self._response_chunk_callback = response_chunk_callback
        self.registry = ToolRegistry()
        self.mcp = MCPClientLayer(self.registry, event_callback=self._on_mcp_event)
        self.llm_helper = FairySkillLLMHelper(llm)
        self.memory = MemoryManager()
        self.rag = RagManager()
        self.reindex = get_reindex_manager()
        self.action_logs = ActionLogRepo()
        self.notification_engine = NotificationEngine(rag_manager=self.rag, reindex_manager=self.reindex)
        self._persona: PersonaEngine | None = None
        self.news = NewsService(event_callback=self._emit_event)
        self.project_name = Path.cwd().name or "workspace"
        self._active_task_id = ""
        self._active_goal = ""
        self._active_done_steps: list[str] = []
        self._active_session_id = ""
        self._active_attachment_paths: list[str] = []
        self._web_runtime = web_runtime or self._build_default_web_runtime()

        self.browser = BrowserCapability(self.registry, self.mcp, web_runtime=self._web_runtime)
        self.documents = DocumentCapability(self.registry, self.mcp, llm)
        self.commands = CommandCapability(self.registry, self.mcp)
        self.screen = ScreenCapability(self.registry, self.mcp, llm)

        self._lazy_dispatcher = LazyDispatcher(
            self.llm,
            self.registry,
            services=BundleRuntimeServices(
                llm=self.llm,
                llm_helper=self.llm_helper,
                browser=self.browser,
                documents=self.documents,
                commands=self.commands,
                screen=self.screen,
                news=self.news,
                registry=self.registry,
                web_runtime=self._web_runtime,
            ),
            event_callback=self._emit_event,
        )
        logger.info("lazy_dispatcher_initialized")

    def _build_default_web_runtime(self) -> WebRuntimeFacade:
        browser_executor = BrowserExecutor()
        execution_ladder = ExecutionLadder(
            search_tool=self._search_web,
            search_tool_detailed=self._search_web_detailed,
            fetch_page=self._fetch_page,
            browser_executor=browser_executor,
            visual_reader=VisualReader(),
        )
        return WebRuntimeFacade(
            access_resolver=WebAccessResolver(),
            retrieval_plan_builder=RetrievalPlanBuilder(),
            execution_ladder=execution_ladder,
        )

    def _search_web(
        self,
        query: str,
        *,
        max_results: int = 5,
        timeout_sec: int = 12,
        preferred_domains: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        return search_web_queries(
            [query],
            max_results_per_query=max_results,
            timeout_sec=timeout_sec,
            preferred_domains=list(preferred_domains or []),
        )

    def _search_web_detailed(
        self,
        query: str,
        *,
        max_results: int = 5,
        timeout_sec: int = 12,
        preferred_domains: list[str] | None = None,
    ) -> dict[str, Any]:
        return search_web_detailed(
            query,
            max_results=max_results,
            timeout_sec=timeout_sec,
            preferred_domains=list(preferred_domains or []),
        )

    def _fetch_page(self, url: str) -> str:
        page = crawl_webpage(url)
        return str(page.html or page.text_content or "")

    def _get_persona_engine(self) -> PersonaEngine:
        if self._persona is None:
            self._persona = PersonaEngine()
        return self._persona

    def handle_request(
        self,
        user_request: str,
        *,
        attachment_paths: Iterable[str] | None = None,
        route_context: RouteContext | None = None,
        request_origin: str = "main_chat",
        request_id: str = "",
    ) -> SkillResult:
        route_context = route_context or RouteContext()
        attachments = list(attachment_paths or [])
        session_id = route_context.session_id
        task_id = self.memory.new_task_id()
        self._active_task_id = task_id
        self._active_goal = user_request
        self._active_done_steps = ["received"]
        self._active_session_id = session_id
        self._active_attachment_paths = list(attachments)
        self.memory.start_task(task_id, user_request)

        logger.info("user_request_received text=%s origin=%s request_id=%s", user_request.strip(), request_origin, request_id)
        self._emit_event("user_request_received", {"text": user_request, "origin": request_origin, "request_id": request_id})

        debug_result = self.memory.handle_debug_command(user_request)
        if debug_result is not None:
            result = SkillResult(
                skill_name="memory_debug",
                success=bool(debug_result.get("ok", True)),
                summary="记忆调试命令已执行。",
                response_text=str(debug_result.get("response_text", "")).strip(),
                structured={"task_id": task_id},
            )
            self._finalize_task(task_id, user_request, result)
            return result

        rag_debug_result = self.rag.handle_debug_command(user_request)
        if rag_debug_result is not None:
            result = SkillResult(
                skill_name="rag_debug",
                success=bool(rag_debug_result.get("ok", True)),
                summary="RAG 调试命令已执行。",
                response_text=str(rag_debug_result.get("response_text", "")).strip(),
                structured={"task_id": task_id},
            )
            self._finalize_task(task_id, user_request, result)
            return result

        if looks_like_fairy_tone_preference(user_request):
            result = build_fairy_tone_preference_result(
                task_id=task_id,
                request_origin=request_origin,
                request_id=request_id,
            )
            self._finalize_task(task_id, user_request, result)
            return result

        memory_bundle, memory_prompt, memory_counts, task_category = self.memory.prepare_memory_context(
            user_request=user_request,
            project=self.project_name,
            task_id=task_id,
            attachments=attachments,
        )
        active_mode = self.llm.provider_router.mode_manager.refresh()
        rag_settings = self.rag.load_settings()
        rag_prompt = ""
        if self.rag.should_use_rag(
            user_request,
            mode=active_mode,
            task_type=task_category,
            settings=rag_settings,
            attachment_paths=attachments,
        ):
            self._emit_event("rag_retrieval_started", {"query": user_request, "top_k": rag_settings.rag_top_k})
            rag_result = self.rag.retrieve_context(
                user_request,
                top_k=rag_settings.rag_top_k,
                settings=rag_settings,
            )
            source_kinds = sorted({item.source_kind for item in rag_result.chunks})
            logger.info(
                "rag_retrieval enabled=%s results=%s top_k=%s injected_chars=%s source_kinds=%s",
                rag_settings.rag_enabled,
                len(rag_result.chunks),
                rag_settings.rag_top_k,
                rag_result.injected_chars,
                ",".join(source_kinds),
            )
            self._emit_event(
                "rag_retrieval_finished",
                {
                    "result_count": len(rag_result.chunks),
                    "top_k": rag_settings.rag_top_k,
                    "source_kinds": source_kinds,
                },
            )
            if rag_result.prompt_block:
                rag_prompt = rag_result.prompt_block
                self._emit_event(
                    "rag_context_injected",
                    {
                        "result_count": len(rag_result.chunks),
                        "context_chars": len(rag_prompt),
                        "source_kinds": source_kinds,
                    },
                )
            else:
                logger.info("rag_retrieval no results for query=%s", user_request.strip())
            if rag_settings.enable_retrieval_debug_panel:
                self._emit_event(
                    "rag_retrieval_visualized",
                    dict(rag_result.debug_snapshot or {}),
                )
                self._emit_event(
                    "injected_context_preview_generated",
                    {
                        "fingerprint": str(rag_result.debug_snapshot.get("embedding_fingerprint", "") or ""),
                        "provider": str(rag_result.debug_snapshot.get("embedding_provider", "") or ""),
                        "collection_name": str(rag_result.debug_snapshot.get("collection_name", "") or ""),
                        "progress": f"{rag_result.debug_snapshot.get('injected_context_item_count', 0)} items / {rag_result.debug_snapshot.get('injected_context_chars', 0)} chars",
                    },
                )
        combined_memory_prompt = "\n\n".join(part for part in (memory_prompt.strip(), rag_prompt.strip()) if part).strip()
        preferences = load_app_preferences()
        persona_mode = get_effective_persona_mode(preferences, active_mode)
        logger.info("persona_mode=%s active_mode=%s", persona_mode, active_mode)
        logger.info(
            "Memory used this turn: profile=%s project=%s task=%s semantic=%s category=%s",
            memory_counts.get("profile", 0),
            memory_counts.get("project", 0),
            memory_counts.get("task", 0),
            memory_counts.get("semantic", 0),
            task_category,
        )
        self._emit_event("memory_used", {**memory_counts, "task_category": task_category})

        self._emit_event("lazy_pipeline_entered", {"pipeline": "bundle_runtime"})
        lazy_result = self._lazy_dispatcher.dispatch(
            user_request,
            attachments=attachments,
            memory_prompt=combined_memory_prompt,
            route_context=route_context,
            request_origin=request_origin,
            request_id=request_id,
        )
        lazy_result.structured.setdefault("pipeline", "bundle_runtime")
        lazy_result.structured.setdefault("request_origin", request_origin)
        lazy_result.structured.setdefault("request_id", request_id)

        if lazy_result.tool_lock:
            logger.info("tool_lock_active skill=%s reason=realtime_factual_data", lazy_result.skill_name)
            self._emit_event(
                "tool_lock_applied",
                {
                    "skill": lazy_result.skill_name,
                    "reason": "realtime_factual_data",
                    "response_text": lazy_result.response_text[:100] if lazy_result.response_text else "",
                },
            )
            card_type = lazy_result.structured.get("card_type") if lazy_result.structured else None
            if should_apply_strict_mode(lazy_result.skill_name, card_type):
                lazy_result.structured = apply_strict_mode(lazy_result.structured or {})
                logger.info("strict_deterministic_mode_applied skill=%s card_type=%s", lazy_result.skill_name, card_type)
        elif persona_mode == "full":
            persona = self._get_persona_engine()
            persona_task_type = persona.classify_task_type(
                user_request,
                chosen_skill=lazy_result.skill_name,
                task_category=task_category,
            )
            styled_text, guard_report = persona.style_response(
                lazy_result.response_text or lazy_result.summary,
                task_type=persona_task_type,
                user_input=user_request,
                user_profile=memory_bundle.profile,
                recent_summary=combined_memory_prompt,
            )
            if styled_text:
                lazy_result.response_text = styled_text
        self._finalize_task(task_id, user_request, lazy_result, task_category=task_category)
        return lazy_result

    def _finalize_task(self, task_id: str, user_request: str, result: SkillResult, *, task_category: str = "") -> None:
        logger.info("skill_result_ready skill=%s success=%s origin=%s", result.skill_name, result.success, result.structured.get("request_origin", "main_chat"))
        self._emit_event("skill_result_ready", {"skill_name": result.skill_name, "success": result.success, "request_origin": result.structured.get("request_origin", "main_chat")})
        self._emit_event("final_response_ready", {"response_text": result.response_text, "request_origin": result.structured.get("request_origin", "main_chat"), "request_id": result.structured.get("request_id", "")})
        write_stats = self.memory.finish_task(
            {
                "task_id": task_id,
                "user_request": user_request,
                "skill_name": result.skill_name,
                "success": result.success,
                "summary": result.summary,
                "recommendation": result.recommendation,
                "structured": result.structured,
                "result": result.structured,
                "sources": result.sources,
                "changed_files": result.changed_files,
                "commands_run": result.commands_run,
                "validations": result.validations,
                "project": self.project_name,
                "task_category": task_category,
            }
        )
        rag_stats = self.rag.maybe_store_task_knowledge(
            user_request=user_request,
            result_summary=result.summary,
            response_text=result.response_text,
            recommendation=result.recommendation,
            task_category=task_category,
            skill_name=result.skill_name,
            session_id=self._active_session_id,
            changed_files=result.changed_files,
            commands_run=result.commands_run,
            attachment_paths=self._active_attachment_paths,
        )
        for item in rag_stats.get("events", []):
            if isinstance(item, dict) and item.get("name"):
                self._emit_event(str(item["name"]), dict(item.get("payload") or {}))
        self._emit_event("memory_write_complete", write_stats)
        logger.info(
            "rag_write_complete session_summary=%s decision_card=%s documents=%s embedding_provider=%s fallback=%s",
            rag_stats["session_summary"],
            rag_stats["decision_card"],
            rag_stats["documents"],
            rag_stats.get("embedding_provider", ""),
            rag_stats.get("embedding_fallback_used", False),
        )
        self._active_task_id = ""
        self._active_goal = ""
        self._active_done_steps = []
        self._active_session_id = ""
        self._active_attachment_paths = []

    def _emit_event(self, event: str, payload: dict[str, Any]) -> None:
        if event != "memory_write_complete":
            try:
                self.action_logs.add_event(session_id=self._active_session_id or None, event_type=event, event_data=payload)
            except Exception:
                logger.exception("Failed to persist action log event=%s", event)
        if self.event_callback is not None:
            self.event_callback(event, payload)

    def _on_mcp_event(self, event: str, payload: dict[str, Any]) -> None:
        if self._active_task_id:
            if event == "tool_call_start":
                tool_name = str(payload.get("tool_name", "") or "").strip()
                if tool_name:
                    self._active_done_steps.append(tool_name)
                    self.memory.update_task(
                        self._active_task_id,
                        self._active_goal,
                        current_step=tool_name,
                        done_steps=list(dict.fromkeys(self._active_done_steps)),
                        status="running",
                    )
            elif event == "tool_call_done":
                result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
                last_url = str(result.get("url") or result.get("final_url") or "")
                if last_url:
                    self.memory.update_task(
                        self._active_task_id,
                        self._active_goal,
                        current_step=str(payload.get("tool_name", "") or "").strip(),
                        done_steps=list(dict.fromkeys(self._active_done_steps)),
                        last_url=last_url,
                        status="running",
                    )
            elif event == "tool_call_failed":
                self.memory.update_task(
                    self._active_task_id,
                    self._active_goal,
                    current_step=str(payload.get("tool_name", "") or "").strip(),
                    done_steps=list(dict.fromkeys(self._active_done_steps)),
                    blocked_reason=str(payload.get("error", "") or "tool_failed"),
                    status="running",
                )
        self._emit_event(event, payload)

