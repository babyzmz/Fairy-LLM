from __future__ import annotations

import json
import logging
import queue
import re
import threading
import base64
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable

from app.ai.llm_client import LLMClient
from app.ai.voice.fairy_tts import FairyTTS
from app.agents.realtime_lookup.agent import RealtimeLookupAgent
from app.agents.realtime_lookup.models import RealtimeLookupRequest
from app.app_preferences import load_app_preferences
from app.fairy_core import FairyCore
from app.persona.tone_preference import build_fairy_tone_preference_result, looks_like_fairy_tone_preference
from app.runtime.fairy_presence import fairy_meta_for_response
from app.models.skill_result import SkillResult
from app.response import ResponsePipeline
from app.runtime import FairyRuntimeV2
from app.route_context import RouteContext
from app.skills.bundles.web_research.runtime import WebResearchIntent, WebResearchSkill
from app.services.assets import get_asset_resolver
from app.web_access import BrowserExecutor, ExecutionLadder, RetrievalPlanBuilder, VisualReader, WebAccessResolver
from app.web_access.bundle_runtime import WebRuntimeFacade
from app.web_access.source_registry import extract_explicit_url, resolve_source_descriptor
from app.tools.browser.http_page_loader import crawl_webpage
from app.tools.search.html_search import search_web_detailed


logger = logging.getLogger(__name__)

SERVICE_NAME = "fairy-runtime-api"
DEFAULT_SESSION_ID = "default"
_PRESENCE_GREETING_TERMS = ("你好", "您好", "hello", "hi", "hey", "在吗", "在线吗", "确认在线")
_WEATHER_TERMS = ("weather", "forecast", "\u5929\u6c14", "\u6e29\u5ea6", "\u6c14\u6e29")
_MAP_DISPLAY_TERMS = (
    "\u663e\u793a\u5730\u56fe",
    "\u7ed9\u6211\u770b\u770b\u5730\u56fe",
    "\u628a\u5730\u56fe\u6253\u5f00\u770b\u770b",
    "\u5730\u56fe\u5c55\u793a\u4e00\u4e0b",
    "show map",
    "display map",
    "open map",
)
_SHORT_FOLLOWUP_RE = re.compile(
    r"^(?:\u90a3\s*|\u90a3\u9ebc|\u90a3\u4e48|\u90a3\u8fb9|\u90a3\u513f|\u90a3\u91cc)?(?P<subject>[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z\u8def\-\s]{1,24}?)(?:\u5462|\u600e\u4e48\u6837|\u5982\u4f55)?$",
    flags=re.IGNORECASE,
)
_TOPIC_SHORT_FOLLOWUP_MARKERS = (
    "那",
    "那么",
    "那麼",
    "那边",
    "那里",
    "再看看",
    "继续",
    "顺便",
    "再查",
)
_BROWSER_TASK_TERMS = (
    "打开",
    "open",
    "官网",
    "click",
    "点击",
    "站内搜索",
    "栏目",
    "翻页",
    "下一页",
)
_VISUAL_TASK_TERMS = (
    "这个网页",
    "这个页面",
    "网页顶部公告",
    "页面顶部公告",
    "最上面",
    "顶部公告",
    "top banner",
    "hero",
)
_SCREEN_TASK_TERMS = (
    "当前屏幕",
    "分析我的当前屏幕",
    "屏幕",
    "界面",
    "窗口",
    "截图",
    "screen",
    "current screen",
)
_SCREEN_STEP_TERMS = (
    "下一步",
    "这步",
    "怎么继续",
    "要怎么继续",
    "现在要干什么",
    "接下来怎么做",
    "点哪里",
    "哪里点",
    "what should i do next",
    "what do i do next",
)
_MAP_DIRECT_PATTERNS = (
    re.compile(r"^(?:显示|打开|看看|看一下|查看)?\s*(?P<location>.+?)\s*地图$", re.IGNORECASE),
    re.compile(r"^(?P<location>.+?)\s*(?:在哪里|在哪儿|在哪)$", re.IGNORECASE),
    re.compile(r"^(?:导航到|导航去|前往)\s*(?P<location>.+)$", re.IGNORECASE),
    re.compile(r"^(?:看看|看一下|查看)\s*(?P<location>.+?)\s*位置$", re.IGNORECASE),
    re.compile(r"^(?:show|display|open)\s+map\s+for\s+(?P<location>.+)$", re.IGNORECASE),
    re.compile(r"^(?:where\s+is)\s+(?P<location>.+)$", re.IGNORECASE),
)


def _normalize_short_text(value: str) -> str:
    return re.sub(r"[\s\u3002\uff0c\uff01\uff1f,.!?]+", "", str(value or "").strip().lower())


def _looks_like_presence_greeting(message: str) -> bool:
    normalized = _normalize_short_text(message)
    if not normalized:
        return False
    if normalized in _PRESENCE_GREETING_TERMS:
        return True
    if len(normalized) <= 18 and any(term in normalized for term in _PRESENCE_GREETING_TERMS):
        return True
    return "确认在线" in normalized and len(normalized) <= 24


def build_error_contract(
    *,
    request_id: str = "",
    session_id: str = DEFAULT_SESSION_ID,
    code: str = "runtime_error",
    message: str = "Unknown runtime error.",
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response_meta = dict(meta or {})
    response_meta.setdefault(
        "fairy",
        fairy_meta_for_response(
            meta=response_meta,
            text="",
            cards=[],
            errors=[{"code": code, "message": message}],
        ),
    )
    return {
        "request_id": request_id,
        "session_id": session_id,
        "text": "",
        "cards": [],
        "meta": response_meta,
        "errors": [{"code": code, "message": message}],
    }


def resolve_asset_path(path_value: str) -> Path | None:
    resolved = get_asset_resolver().resolve_local_asset(path_value)
    if not resolved:
        return None
    path = Path(resolved).resolve()
    return path if path.exists() and path.is_file() else None


class FairyRuntimeService:
    def __init__(self) -> None:
        preferences = load_app_preferences()
        self.llm = LLMClient()
        self.response_pipeline = ResponsePipeline(language=preferences.ui_language)
        self.runtime = FairyRuntimeV2(
            language=preferences.ui_language,
            response_pipeline=self.response_pipeline,
            search_tool=self._search_web,
            fetch_page=self._fetch_page,
            bundle_executor=self._bundle_execute,
            streaming_bundle_executor=self._streaming_bundle_execute,
            capability_executors={
                "realtime_lookup": self._direct_realtime_execute,
                "weather_lookup": self._direct_realtime_execute,
                "time_lookup": self._direct_realtime_execute,
                "location_lookup": self._direct_location_execute,
                "display_information": self._direct_location_execute,
            },
            streaming_capability_executors={
                "realtime_lookup": self._stream_direct_realtime_execute,
                "weather_lookup": self._stream_direct_realtime_execute,
                "time_lookup": self._stream_direct_realtime_execute,
                "location_lookup": self._stream_direct_location_execute,
                "display_information": self._stream_direct_location_execute,
            },
            semantic_arbitration_callback=self._semantic_llm_arbitrate,
            web_browse_decision_callback=self._semantic_web_browse_decide,
        )
        self._session_structured: dict[str, dict[str, Any]] = {}
        self._session_web_context: dict[str, dict[str, Any]] = {}
        self._session_lock = threading.Lock()
        self._tts = FairyTTS()
        self._location_skill = WebResearchSkill(browser=None, llm_helper=self.llm)
        self._realtime_agent = RealtimeLookupAgent(search_tool=self._search_web, fetch_page=self._fetch_page)
        self._web_access_resolver = WebAccessResolver()
        self._retrieval_plan_builder = RetrievalPlanBuilder()
        self._browser_executor = BrowserExecutor()
        self._visual_reader = VisualReader()
        self._execution_ladder = ExecutionLadder(
            search_tool=self._search_web,
            search_tool_detailed=self._search_web_detailed,
            fetch_page=self._fetch_page,
            browser_executor=self._browser_executor,
            visual_reader=self._visual_reader,
            browse_policy_callback=self._semantic_browse_policy,
            serp_link_selection_callback=self._semantic_serp_link_selection_policy,
        )
        self._web_runtime = WebRuntimeFacade(
            access_resolver=self._web_access_resolver,
            retrieval_plan_builder=self._retrieval_plan_builder,
            execution_ladder=self._execution_ladder,
        )
        if self.llm.config.runtime_mode == "local_server" and self.llm.config.auto_start_server:
            self.llm.start_server_background()
            self.runtime.system_bridge.set_backend_status("warming_up", message="Starting local LLM server.")
        else:
            self.runtime.system_bridge.set_backend_status("ready", message="Runtime API service initialized.")
        self.runtime.set_runtime_presence_state("idle", reason="runtime_service_initialized", force=True)

    def invoke(self, *, message: str, session_id: str, attachments: list[str] | None = None) -> dict[str, Any]:
        effective_session = session_id or DEFAULT_SESSION_ID
        shortcut = self._shortcut_contract(message=message, session_id=effective_session)
        if shortcut is not None:
            self._remember_session_contract(effective_session, shortcut)
            return shortcut
        previous_structured = self._get_previous_structured(effective_session)
        contract = self.runtime.invoke(
            message=message,
            session_id=effective_session,
            attachments=list(attachments or []),
            request_origin="api_local",
            previous_structured=previous_structured,
            include_compat_fields=False,
        )
        self._remember_session_contract(effective_session, contract)
        return contract

    def stream_invoke(
        self,
        *,
        message: str,
        session_id: str,
        attachments: list[str] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Iterable[dict[str, Any]]:
        effective_session = session_id or DEFAULT_SESSION_ID
        shortcut = self._shortcut_contract(message=message, session_id=effective_session)
        if shortcut is not None:
            self._remember_session_contract(effective_session, shortcut)
            yield {
                "event": "message_start",
                "request_id": shortcut["request_id"],
                "session_id": effective_session,
                "meta": dict(shortcut.get("meta") or {}),
            }
            text = str(shortcut.get("text") or "")
            if text:
                yield {
                    "event": "text_delta",
                    "request_id": shortcut["request_id"],
                    "session_id": effective_session,
                    "text": text,
                }
            for card in list(shortcut.get("cards") or []):
                yield {
                    "event": "card",
                    "request_id": shortcut["request_id"],
                    "session_id": effective_session,
                    "card": card,
                }
            yield {
                "event": "message_end",
                "request_id": shortcut["request_id"],
                "session_id": effective_session,
                "text": text,
                "cards": list(shortcut.get("cards") or []),
                "meta": dict(shortcut.get("meta") or {}),
                "errors": [],
            }
            return
        previous_structured = self._get_previous_structured(effective_session)
        for event in self.runtime.stream_invoke(
            message=message,
            session_id=effective_session,
            attachments=list(attachments or []),
            request_origin="api_stream",
            streaming_bundle_executor=self._streaming_bundle_execute,
            cancel_event=cancel_event,
            previous_structured=previous_structured,
        ):
            payload = event.to_dict()
            if payload.get("event") == "message_end":
                self._remember_session_contract(
                    effective_session,
                    {
                        "request_id": payload.get("request_id", ""),
                        "session_id": effective_session,
                        "text": payload.get("text", ""),
                        "cards": list(payload.get("cards") or []),
                        "meta": dict(payload.get("meta") or {}),
                        "errors": list(payload.get("errors") or []),
                    },
                )
            yield payload

    def capabilities(self) -> dict[str, Any]:
        return self.runtime.capabilities_snapshot()

    def _shortcut_contract(self, *, message: str, session_id: str) -> dict[str, Any] | None:
        request_id = uuid.uuid4().hex
        if looks_like_fairy_tone_preference(message):
            result = build_fairy_tone_preference_result(
                task_id="api-shortcut",
                request_origin="api_local",
                request_id=request_id,
            )
            return self._text_shortcut_contract(
                text=result.response_text,
                session_id=session_id,
                request_id=request_id,
                intent="persona_preference",
                title="语气偏好已切换",
            )
        if _looks_like_presence_greeting(message):
            return self._text_shortcut_contract(
                text="在线。任务目标？",
                session_id=session_id,
                request_id=request_id,
                intent="presence_greeting",
                title="在线",
            )
        return None

    def _text_shortcut_contract(
        self,
        *,
        text: str,
        session_id: str,
        request_id: str,
        intent: str,
        title: str,
    ) -> dict[str, Any]:
        card = {
            "type": "generic_info",
            "version": "1",
            "data": {
                "title": title,
                "summary": text,
                "fields": [],
            },
            "layout": "single",
            "metadata": {
                "intent": intent,
                "source_reason": "api_shortcut",
            },
            "actions": [],
        }
        meta: dict[str, Any] = {
            "intent": intent,
            "modality": "text_plus_card",
            "speech": {
                "mode": "summary_first",
                "text": text,
                "allow_streaming": True,
            },
            "runtime": {
                "selected_bundle": "api-shortcut",
                "selected_capability": intent,
                "executor_path": "api_shortcut",
            },
        }
        meta["fairy"] = fairy_meta_for_response(meta=meta, text=text, cards=[card], errors=[])
        return {
            "request_id": request_id,
            "session_id": session_id,
            "text": text,
            "cards": [card],
            "meta": meta,
            "errors": [],
        }

    def _sync_llm_backend_status(self) -> None:
        snapshot = self.llm.get_runtime_snapshot()
        if snapshot.get("model_online") is True:
            summary = str(snapshot.get("backend_summary") or "").strip()
            model = str(snapshot.get("model") or self.llm.config.model or "").strip()
            message = f"Local model ready: {model}"
            if summary:
                message += f" ({summary})"
            self.runtime.system_bridge.set_backend_status("ready", message=message, emit_event=False)
            return

        startup_error = str(snapshot.get("startup_error") or "").strip()
        if startup_error:
            self.runtime.system_bridge.set_backend_status("error", message=startup_error, emit_event=False)
            return

        if self.llm.config.runtime_mode == "local_server" and self.llm.config.auto_start_server:
            self.runtime.system_bridge.set_backend_status(
                "warming_up",
                message="Starting local LLM server.",
                emit_event=False,
            )

    def system_state(self) -> dict[str, Any]:
        self._sync_llm_backend_status()
        return self.runtime.get_system_state()

    def system_events(self, *, limit: int = 25) -> list[dict[str, Any]]:
        return self.runtime.get_system_events(limit=limit)

    def perform_system_action(self, action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.runtime.execute_system_action(action, payload)

    def synthesize_voice(self, *, text: str, system_voice: bool = False) -> dict[str, Any]:
        path = self._tts.synthesize_to_file(text, system_voice=system_voice)
        try:
            audio_bytes = path.read_bytes()
        finally:
            path.unlink(missing_ok=True)
        return {
            "audio_base64": base64.b64encode(audio_bytes).decode("ascii"),
            "mime_type": "audio/wav",
        }

    def shutdown(self) -> None:
        self.runtime.set_runtime_presence_state("sleeping", reason="runtime_service_shutdown", force=True)
        self.runtime.system_bridge.set_backend_status("stopped", message="Runtime API service shutdown.")
        try:
            self.llm.shutdown()
        except Exception:
            logger.debug("runtime_service shutdown failed", exc_info=True)
        try:
            self._tts.shutdown()
        except Exception:
            logger.debug("runtime_service tts shutdown failed", exc_info=True)

    def _search_web(
        self,
        query: str,
        *,
        max_results: int = 5,
        preferred_domains: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        payload = self._search_web_detailed(
            query,
            max_results=max_results,
            preferred_domains=preferred_domains,
        )
        results = payload.get("results") if isinstance(payload, dict) else []
        return list(results or [])

    def _search_web_detailed(
        self,
        query: str,
        *,
        max_results: int = 5,
        preferred_domains: list[str] | None = None,
    ) -> dict[str, Any]:
        payload = search_web_detailed(
            query,
            max_results=max_results,
            timeout_sec=3,
            preferred_domains=preferred_domains or [],
        )
        return dict(payload or {})

    def _fetch_page(self, url: str) -> str:
        page = crawl_webpage(url, timeout_sec=8)
        return page.html

    def _semantic_llm_arbitrate(
        self,
        *,
        raw_text: str,
        normalized_text: str,
        intent_hint: str,
        default_capability: str,
        followup_target: str,
        session_context: Any,
        matched_rules: list[str],
        candidate_meta: list[dict[str, Any]],
    ) -> dict[str, str]:
        allowed = [str(item.get("capability") or "").strip() for item in candidate_meta if str(item.get("capability") or "").strip()]
        if len(allowed) < 2:
            return {}
        prompt = (
            "You are an internal semantic arbitration helper for a desktop assistant.\n"
            "Choose the single best capability from the provided candidates.\n"
            "Rules:\n"
            "- Prefer explicit realtime intent phrases over prior context.\n"
            "- Prefer switching capability when the query contains a new strong intent signal.\n"
            "- For short follow-ups like '阿德莱德呢' keep the prior intent only if the utterance mainly overrides the location.\n"
            "- Do not invent new capabilities.\n"
            "Return strict JSON only: {\"capability\": \"...\", \"reason\": \"...\"}"
        )
        payload = {
            "raw_text": raw_text,
            "normalized_text": normalized_text,
            "intent_hint": intent_hint,
            "default_capability": default_capability,
            "followup_target": followup_target,
            "last_capability": str(getattr(session_context, "last_capability", "") or ""),
            "matched_rules": matched_rules,
            "candidates": candidate_meta,
            "allowed_capabilities": allowed,
        }
        try:
            response = self.llm.execute_task(
                prompt,
                json.dumps(payload, ensure_ascii=False),
                temperature=0.0,
                max_tokens=120,
                instruction_label="Semantic arbitration",
            )
        except Exception:
            logger.debug("semantic_llm_arbitration_failed", exc_info=True)
            return {}
        text = str(response.text or "").strip()
        if not text or text.startswith("模型服务未就绪"):
            return {}
        try:
            start = text.find("{")
            end = text.rfind("}")
            if start < 0 or end < start:
                return {}
            data = json.loads(text[start : end + 1])
        except Exception:
            logger.debug("semantic_llm_arbitration_parse_failed text=%r", text)
            return {}
        capability = str(data.get("capability") or "").strip()
        if capability not in allowed:
            return {}
        return {
            "capability": capability,
            "reason": str(data.get("reason") or "").strip(),
        }

    def _semantic_web_browse_decide(
        self,
        *,
        raw_text: str,
        normalized_text: str,
        selected_capability: str,
        intent_hint: str,
        followup_target: str,
        session_context: Any,
        resolution: dict[str, Any],
    ) -> dict[str, Any]:
        prompt = (
            "You are an internal web-routing planner for a desktop assistant.\n"
            "Decide whether this request should enter the web-research bundle.\n"
            "The decision must be model-first: infer user intent from natural language, not keyword matching.\n"
            "Use web-research when the answer depends on current real-world web content such as product facts, official docs, release status, news, comparisons, pricing, or purchase advice.\n"
            "Do not use web-research for pure explanation, translation, writing, coding help, or local desktop/system tasks.\n"
            "Allowed task_type values: general_info, specs, compare, news, release, product_lookup, none.\n"
            "Map natural questions like screen size, versions, worth buying, how to choose, and whether something is out yet to the best web task type.\n"
            "Return strict JSON only with keys: should_browse, task_type, entity, search_query, answer_focus, confidence, reason."
        )
        payload = {
            "raw_text": raw_text,
            "normalized_text": normalized_text,
            "selected_capability": selected_capability,
            "intent_hint": intent_hint,
            "followup_target": followup_target,
            "last_capability": str(getattr(session_context, "last_capability", "") or ""),
            "previous_structured": dict(getattr(session_context, "previous_structured", {}) or {}),
            "resolution": resolution,
        }
        try:
            response = self.llm.execute_task(
                prompt,
                json.dumps(payload, ensure_ascii=False),
                temperature=0.0,
                max_tokens=220,
                instruction_label="Web browse routing",
            )
        except Exception:
            logger.debug("semantic_web_browse_decide_failed", exc_info=True)
            return {}
        text = str(response.text or "").strip()
        if not text or text.startswith("模型服务未就绪"):
            return {}
        try:
            start = text.find("{")
            end = text.rfind("}")
            if start < 0 or end < start:
                return {}
            data = json.loads(text[start : end + 1])
        except Exception:
            logger.debug("semantic_web_browse_decide_parse_failed text=%r", text)
            return {}
        task_type = str(data.get("task_type") or "").strip().lower()
        if task_type == "none":
            task_type = ""
        return {
            "should_browse": bool(data.get("should_browse")) and bool(task_type),
            "task_type": task_type,
            "entity": str(data.get("entity") or "").strip(),
            "search_query": str(data.get("search_query") or "").strip(),
            "answer_focus": str(data.get("answer_focus") or "").strip(),
            "confidence": float(data.get("confidence") or 0.0),
            "reason": str(data.get("reason") or "").strip(),
        }

    def _semantic_browse_policy(
        self,
        *,
        query: str,
        source_name: str,
        source_domain: str,
        task_type: str,
        entity: str,
        hop: int,
        max_hops: int,
        page_type_guess: str,
        page_snapshot: dict[str, Any],
        candidate_links: list[dict[str, Any]],
        navigation_targets: list[str],
        source_hints: dict[str, Any],
    ) -> dict[str, Any]:
        allowed_task_types = ["specs", "release", "news", "product_lookup", "general_info", "compare"]
        allowed_page_types = ["homepage", "docs", "product", "news", "generic"]
        allowed_actions = ["open_link", "stop", "search_again"]
        if not candidate_links and not page_snapshot:
            return {}
        prompt = (
            "You are an internal browse-policy helper for a desktop web agent.\n"
            "Choose the next browsing action for the current page.\n"
            "You must stay within the provided site/context and never suggest unsafe or account-related pages.\n"
            "Rules:\n"
            "- specs: prefer product/specification/compare pages.\n"
            "- release: prefer newsroom, press release, announcement, launch pages with dates.\n"
            "- news: prefer list/index pages with multiple article-like headlines.\n"
            "- product_lookup: prefer clear product or category pages matching the entity.\n"
            "- general_info: prefer the single most relevant content page for the query.\n"
            "- If the current page already satisfies the task, set action=stop.\n"
            "- If the current page is not enough but has a good next step, set action=open_link.\n"
            "- If the current source is weak and a query rewrite would help, set action=search_again.\n"
            "- Candidate link ids must come from the provided candidate_links list.\n"
            "- Return at most 2 candidate_link_ids.\n"
            "Return strict JSON only with keys: "
            "{\"task_type\":\"...\",\"page_type\":\"...\",\"action\":\"open_link|stop|search_again\","
            "\"candidate_link_ids\":[0],\"selected_link_id\":0,\"stop_reason\":\"...\","
            "\"confidence\":0.0,\"user_visible_step\":\"...\"}"
        )
        payload = {
            "query": str(query or "").strip(),
            "source_name": str(source_name or "").strip(),
            "source_domain": str(source_domain or "").strip(),
            "task_type": str(task_type or "").strip(),
            "entity": str(entity or "").strip(),
            "hop": hop,
            "max_hops": max_hops,
            "page_type_guess": str(page_type_guess or "").strip(),
            "page_snapshot": page_snapshot,
            "candidate_links": candidate_links,
            "navigation_targets": list(navigation_targets or []),
            "source_hints": dict(source_hints or {}),
            "allowed_task_types": allowed_task_types,
            "allowed_page_types": allowed_page_types,
            "allowed_actions": allowed_actions,
        }
        try:
            response = self.llm.execute_task(
                prompt,
                json.dumps(payload, ensure_ascii=False),
                temperature=0.0,
                max_tokens=220,
                instruction_label="Browse policy",
            )
        except Exception:
            logger.debug("semantic_browse_policy_failed", exc_info=True)
            return {}
        text = str(response.text or "").strip()
        if not text:
            return {}
        if text.startswith("```"):
            text = text.strip("`").strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()
        try:
            start = text.find("{")
            end = text.rfind("}")
            if start < 0 or end < start:
                return {}
            data = json.loads(text[start : end + 1])
        except Exception:
            logger.debug("semantic_browse_policy_parse_failed text=%r", text)
            return {}

        resolved_task_type = str(data.get("task_type") or "").strip().lower()
        if resolved_task_type not in allowed_task_types:
            resolved_task_type = task_type if task_type in allowed_task_types else "general_info"
        resolved_page_type = str(data.get("page_type") or "").strip().lower()
        if resolved_page_type not in allowed_page_types:
            resolved_page_type = page_type_guess if page_type_guess in allowed_page_types else "generic"
        resolved_action = str(data.get("action") or "").strip().lower()
        if resolved_action not in allowed_actions:
            resolved_action = "open_link"
        candidate_ids: list[int] = []
        for value in list(data.get("candidate_link_ids") or [])[:2]:
            try:
                index = int(value)
            except Exception:
                continue
            if 0 <= index < len(candidate_links) and index not in candidate_ids:
                candidate_ids.append(index)
        try:
            confidence = float(data.get("confidence") or 0.0)
        except Exception:
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        try:
            selected_link_id = int(data.get("selected_link_id")) if data.get("selected_link_id") is not None else None
        except Exception:
            selected_link_id = None
        if selected_link_id is not None and selected_link_id not in candidate_ids:
            candidate_ids = [selected_link_id, *candidate_ids]
            candidate_ids = [item for idx, item in enumerate(candidate_ids) if item in range(len(candidate_links)) and item not in candidate_ids[:idx]]
        return {
            "task_type": resolved_task_type,
            "page_type": resolved_page_type,
            "action": resolved_action,
            "candidate_link_ids": candidate_ids,
            "selected_link_id": selected_link_id,
            "stop_reason": str(data.get("stop_reason") or "").strip(),
            "confidence": confidence,
            "user_visible_step": str(data.get("user_visible_step") or "").strip(),
        }

    def _semantic_serp_link_selection_policy(
        self,
        *,
        query: str,
        task_type: str,
        entity: str,
        candidate_links: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not candidate_links:
            return {}
        limited_candidates = [
            {
                "url": str(item.get("url") or "").strip(),
                "title": str(item.get("title") or "").strip(),
                "snippet": str(item.get("snippet") or "").strip(),
            }
            for item in list(candidate_links or [])[:8]
            if str(item.get("url") or "").strip()
        ]
        if not limited_candidates:
            return {}
        prompt = (
            "You are an internal link-selection helper for a desktop web agent.\n"
            "Choose the single best next link from search result candidates.\n"
            "Prefer the most relevant content page for the task, not forums, support pages, or aggregators unless they are clearly the best match.\n"
            "Rules:\n"
            "- general_info: prefer official about, overview, company, docs, or homepage content pages.\n"
            "- specs: prefer official specifications, tech specs, or product detail pages.\n"
            "- compare: prefer official compare/comparison pages or pages clearly comparing the entities.\n"
            "- news: prefer official newsroom, blog, press, or latest news pages.\n"
            "- Use only one of the provided candidate URLs.\n"
            "Return strict JSON only with keys: {\"selected_url\":\"...\",\"reason\":\"...\"}"
        )
        payload = {
            "query": str(query or "").strip(),
            "task_type": str(task_type or "").strip(),
            "entity": str(entity or "").strip(),
            "candidate_links": limited_candidates,
        }
        try:
            response = self.llm.execute_task(
                prompt,
                json.dumps(payload, ensure_ascii=False),
                temperature=0.0,
                max_tokens=180,
                instruction_label="SERP link selection",
            )
        except Exception:
            logger.debug("semantic_serp_link_selection_failed", exc_info=True)
            return {}
        text = str(response.text or "").strip()
        if not text:
            return {}
        if text.startswith("```"):
            text = text.strip("`").strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()
        try:
            start = text.find("{")
            end = text.rfind("}")
            if start < 0 or end < start:
                return {}
            data = json.loads(text[start : end + 1])
        except Exception:
            logger.debug("semantic_serp_link_selection_parse_failed text=%r", text)
            return {}
        selected_url = str(data.get("selected_url") or "").strip()
        allowed_urls = {str(item.get("url") or "").strip().lower() for item in limited_candidates if str(item.get("url") or "").strip()}
        if not selected_url or selected_url.lower() not in allowed_urls:
            return {}
        return {
            "selected_url": selected_url,
            "reason": str(data.get("reason") or "").strip(),
        }

    def _direct_web_execute(
        self,
        *,
        message: str,
        session_id: str,
        request_id: str,
        attachments: list[str],
        request_origin: str,
        route_hints: dict[str, Any],
    ) -> dict[str, Any]:
        _ = attachments
        return self._execute_web_access_request(
            message=message,
            session_id=session_id,
            request_id=request_id,
            request_origin=request_origin,
            route_hints=route_hints,
            progress_callback=None,
        )

    def _stream_direct_web_execute(
        self,
        *,
        message: str,
        session_id: str,
        request_id: str,
        attachments: list[str],
        request_origin: str,
        route_hints: dict[str, Any],
        cancel_event: threading.Event | None = None,
    ) -> Iterable[dict[str, Any]]:
        _ = attachments

        def emit(event_name: str, payload: dict[str, Any]) -> None:
            if cancel_event is not None and cancel_event.is_set():
                return
            events.append({"kind": "progress", "event_name": event_name, "payload": dict(payload or {})})

        events: list[dict[str, Any]] = []
        if cancel_event is not None and cancel_event.is_set():
            yield {"kind": "error", "message": "cancelled"}
            return
        result = self._execute_web_access_request(
            message=message,
            session_id=session_id,
            request_id=request_id,
            request_origin=request_origin,
            route_hints=route_hints,
            progress_callback=emit,
        )
        if cancel_event is not None and cancel_event.is_set():
            yield {"kind": "error", "message": "cancelled"}
            return
        for event in events:
            yield event
        yield {"kind": "result", "payload": result}

    def _direct_realtime_execute(
        self,
        *,
        message: str,
        session_id: str,
        request_id: str,
        attachments: list[str],
        request_origin: str,
        route_hints: dict[str, Any],
    ) -> dict[str, Any]:
        del session_id, attachments, request_origin, route_hints
        result = self._realtime_agent.execute(
            RealtimeLookupRequest(
                query=message,
                request_id=request_id,
            )
        )
        payload = result.to_dict()
        payload["_runtime_executor_path"] = "direct_realtime_executor"
        return payload

    def _stream_direct_realtime_execute(
        self,
        *,
        message: str,
        session_id: str,
        request_id: str,
        attachments: list[str],
        request_origin: str,
        route_hints: dict[str, Any],
        cancel_event: threading.Event | None = None,
    ) -> Iterable[dict[str, Any]]:
        if cancel_event is not None and cancel_event.is_set():
            yield {"kind": "error", "message": "cancelled"}
            return
        yield {"kind": "progress", "event_name": "progress: searching", "payload": {"message": "正在读取结构化实时数据源..."}}
        result = self._direct_realtime_execute(
            message=message,
            session_id=session_id,
            request_id=request_id,
            attachments=attachments,
            request_origin=request_origin,
            route_hints=route_hints,
        )
        if cancel_event is not None and cancel_event.is_set():
            yield {"kind": "error", "message": "cancelled"}
            return
        yield {"kind": "result", "payload": result}

    def _build_query_debug_snapshot(
        self,
        *,
        raw_query: str,
        resolved_query: str,
        effective_query: str,
        selected_capability: str,
        query_authority: str,
        query_mutation_reason: str,
        topic_carryover_applied: bool,
        topic_carryover_reason: str,
        source_constraint_applied: bool,
        memory_context_applied: bool = False,
        memory_usage_type: str = "none",
        db_context_applied: bool = False,
        page_context_available: bool = False,
        continuation_applied: bool = False,
        continuation_reason: str = "",
        continuation_strategy: str = "",
        continuation_of_request_id: str = "",
    ) -> dict[str, Any]:
        return {
            "raw_query": raw_query,
            "resolved_query": resolved_query,
            "effective_query": effective_query,
            "query_authority": query_authority,
            "query_mutation_reason": query_mutation_reason,
            "selected_capability": selected_capability,
            "topic_carryover_applied": topic_carryover_applied,
            "topic_carryover_reason": topic_carryover_reason,
            "source_constraint_applied": source_constraint_applied,
            "memory_context_applied": memory_context_applied,
            "memory_usage_type": memory_usage_type,
            "db_context_applied": db_context_applied,
            "page_context_available": page_context_available,
            "continuation_applied": continuation_applied,
            "continuation_reason": continuation_reason,
            "continuation_strategy": continuation_strategy,
            "continuation_of_request_id": continuation_of_request_id,
        }

    @staticmethod
    def _looks_like_browser_task_query(text: str) -> bool:
        lowered = str(text or "").strip().lower()
        browser_terms = (
            "打开",
            "官网",
            "点击",
            "站内搜索",
            "翻页",
            "下一页",
            "open",
            "click",
            "browser",
            "url",
            "http://",
            "https://",
        )
        return any(token in lowered for token in browser_terms)

    @staticmethod
    def _looks_like_visual_task_query(text: str) -> bool:
        lowered = str(text or "").strip().lower()
        visual_terms = (
            "这个网页",
            "这个页面",
            "网页顶部公告",
            "页面顶部公告",
            "最上面",
            "顶部公告",
            "top banner",
            "hero",
        )
        return any(token in lowered for token in visual_terms)

    @staticmethod
    def _looks_like_screen_task_query(text: str) -> bool:
        lowered = str(text or "").strip().lower()
        if not lowered:
            return False
        screen_terms = (
            "当前屏幕",
            "分析我的当前屏幕",
            "屏幕",
            "界面",
            "窗口",
            "截图",
            "screen",
            "current screen",
        )
        step_terms = (
            "下一步",
            "这步",
            "怎么继续",
            "要怎么继续",
            "现在要干什么",
            "接下来怎么做",
            "点哪里",
            "哪里点",
            "what should i do next",
            "what do i do next",
        )
        if any(token in lowered for token in screen_terms):
            return True
        if any(token in lowered for token in step_terms):
            web_tokens = ("官网", "网页", "网站", "browser", "url", "http://", "https://")
            return not any(token in lowered for token in web_tokens)
        if any(token.lower() in lowered for token in _SCREEN_TASK_TERMS):
            return True
        if any(token.lower() in lowered for token in _SCREEN_STEP_TERMS):
            web_tokens = ("官网", "网页", "网站", "browser", "url", "http://", "https://")
            return not any(token in lowered for token in web_tokens)
        return False

    @staticmethod
    def _parse_web_continuation_query(text: str) -> str:
        cleaned = str(text or "").strip()
        lowered = cleaned.lower()
        if not cleaned:
            return ""
        if cleaned in {"继续", "继续找", "接着找", "再继续找", "继续看", "接着看"}:
            return "continue_previous_web_plan"
        if "换个来源" in cleaned:
            return "expand_source"
        if lowered in {"continue", "keep going", "go on"}:
            return "continue_previous_web_plan"
        if cleaned in {"继续", "继续找", "接着找", "再找找", "继续看", "接着看"}:
            return "continue_previous_web_plan"
        if "换个来源" in cleaned:
            return "expand_source"
        if lowered in {"continue", "keep going", "go on"}:
            return "continue_previous_web_plan"
        return ""

    @staticmethod
    def _looks_like_news_updates_query(text: str) -> bool:
        lowered = str(text or "").strip().lower()
        if any(
            token in lowered
            for token in ("新闻", "新消息", "最新", "动态", "发布", "news", "latest", "what's new", "whats new", "updates")
        ):
            return True
        return any(
            token in lowered
            for token in (
                "新闻",
                "新消息",
                "最新",
                "动态",
                "发布",
                "news",
                "latest",
                "what's new",
                "whats new",
                "updates",
            )
        )

    @staticmethod
    def _is_short_web_followup(text: str, *, followup_target: str = "") -> bool:
        cleaned = str(text or "").strip()
        if not cleaned or len(cleaned) > 18:
            return False
        lowered = cleaned.lower()
        if followup_target and followup_target not in {"location", "weather"}:
            return True
        markers = ("那", "那么", "那边", "那里", "再看看", "继续", "顺便", "再查")
        if any(cleaned.startswith(marker) or lowered.startswith(marker.lower()) for marker in markers):
            return True
        return any(cleaned.startswith(marker) or lowered.startswith(marker.lower()) for marker in _TOPIC_SHORT_FOLLOWUP_MARKERS)

    @staticmethod
    def _looks_like_standalone_web_query(text: str) -> bool:
        cleaned = str(text or "").strip()
        if not cleaned:
            return False
        lowered = cleaned.lower()
        if FairyRuntimeService._parse_web_continuation_query(cleaned):
            return False
        if FairyRuntimeService._is_short_web_followup(cleaned):
            return False
        if re.search(r"https?://|www\.", lowered):
            return True
        standalone_terms = (
            "\u5b98\u7f51",
            "\u53c2\u6570",
            "\u89c4\u683c",
            "\u914d\u7f6e",
            "\u65b0\u95fb",
            "\u65b0\u6d88\u606f",
            "\u533a\u522b",
            "\u5bf9\u6bd4",
            "\u662f\u5e72\u561b\u7684",
            "\u4ec0\u4e48\u662f",
            "official",
            "website",
            "site",
            "specs",
            "spec",
            "technical specifications",
            "news",
            "latest",
            "newsroom",
            "what is",
            "about",
            "overview",
            "compare",
            "comparison",
            "versus",
            " vs ",
        )
        if any(term in lowered for term in standalone_terms):
            return True
        query_tokens = [
            token
            for token in re.split(r"[^a-z0-9\u4e00-\u9fff]+", lowered)
            if token and (len(token) >= 3 or token.isdigit())
        ]
        return len(cleaned) >= 8 and len(query_tokens) >= 2

    @staticmethod
    def _sanitize_web_resolution_slots(
        *,
        raw_query: str,
        resolved_capability: str,
        slots: dict[str, Any],
        slot_sources: dict[str, Any],
        query_debug: dict[str, Any],
    ) -> dict[str, Any]:
        sanitized = dict(slots or {})
        sources = {str(key): str(value or "").strip() for key, value in dict(slot_sources or {}).items()}
        raw = str(raw_query or "").strip()
        query_authority = str(query_debug.get("query_authority") or "raw_query").strip()
        topic_carryover_applied = bool(query_debug.get("topic_carryover_applied"))
        source_constraint_applied = bool(query_debug.get("source_constraint_applied"))
        standalone_query = FairyRuntimeService._looks_like_standalone_web_query(raw)

        topic_source = sources.get("topic", "")
        if (
            not topic_carryover_applied
            and topic_source in {"followup_context", "rule_override", "session_context", "carryover", "default"}
        ):
            sanitized.pop("topic", None)
        elif standalone_query and topic_source in {"followup_context", "rule_override", "session_context", "carryover", "default"}:
            sanitized.pop("topic", None)
        elif re.search(r"(屏幕|当前屏幕|分析.*屏幕|现在要干什么|这步要怎么继续|点哪里)", str(sanitized.get("topic") or "").strip(), flags=re.IGNORECASE):
            sanitized.pop("topic", None)

        if resolved_capability in {"generic_search", "news_lookup"}:
            location_source = sources.get("location", "")
            if location_source in {"followup_context", "rule_override", "session_last_location", "default"}:
                sanitized.pop("location", None)

        source_source = sources.get("source", "")
        if not source_constraint_applied and source_source in {"followup_context", "rule_override", "session_context", "default"}:
            sanitized.pop("source", None)

        if query_authority != "resolved_query_followup":
            for slot_name in ("topic", "source"):
                slot_value = str(sanitized.get(slot_name) or "").strip()
                slot_source = sources.get(slot_name, "")
                if not slot_value:
                    continue
                if slot_value in raw:
                    continue
                if slot_source in {"followup_context", "rule_override", "session_context", "carryover", "default"}:
                    sanitized.pop(slot_name, None)

        return sanitized

    def _resolve_effective_web_query(
        self,
        *,
        raw_query: str,
        resolved_query: str,
        resolved_capability: str,
        slots: dict[str, Any],
        context: dict[str, Any],
        route_hints: dict[str, Any],
    ) -> dict[str, Any]:
        def clear_page_context(target: dict[str, Any]) -> None:
            for key in ("last_url", "last_handle_id", "last_page_handle", "last_visual_region", "last_title"):
                target.pop(key, None)

        raw = str(raw_query or "").strip()
        resolved = str(resolved_query or raw).strip() or raw
        followup_target = str(route_hints.get("followup_target") or "").strip()
        explicit_source = str(slots.get("source") or "").strip()
        descriptor = resolve_source_descriptor(raw, explicit_source=explicit_source)
        explicit_url = extract_explicit_url(raw)
        page_context_available = bool(
            str(context.get("last_url") or "").strip()
            or str(context.get("last_page_handle") or context.get("last_handle_id") or "").strip()
            or str(context.get("last_visual_region") or "").strip()
        )
        has_topic_history = bool(str(context.get("last_topic") or "").strip())
        continuation_strategy = self._parse_web_continuation_query(raw)
        continuation_available = bool(
            continuation_strategy
            and (
                str(context.get("last_query_strategy") or "").strip()
                or str(context.get("last_failure_reason") or "").strip()
                or str(context.get("last_result_kind") or "").strip()
                or dict(context.get("last_retrieval_plan") or {})
            )
        )
        source_constraint_applied = bool(descriptor is not None or explicit_url or explicit_source)
        browser_task = self._looks_like_browser_task_query(raw)
        visual_task = self._looks_like_visual_task_query(raw)
        direct_location = self._extract_direct_location_target(raw)
        short_followup = self._is_short_web_followup(raw, followup_target=followup_target)
        standalone_query = self._looks_like_standalone_web_query(raw)

        query_authority = "raw_query"
        query_mutation_reason = "no_mutation_raw_query_retained"
        effective_query = raw
        topic_carryover_applied = False
        topic_carryover_reason = "no_carryover_raw_query_retained"
        continuation_applied = False
        continuation_reason = ""
        continuation_of_request_id = ""

        planning_context = dict(context)
        if continuation_available:
            continuation_applied = True
            continuation_reason = "explicit_web_continuation_phrase"
            continuation_of_request_id = str(context.get("last_request_id") or "").strip()
            query_mutation_reason = "no_mutation_raw_query_retained"
            topic_carryover_reason = "web_continuation_uses_previous_execution_state"
        elif source_constraint_applied:
            planning_context.pop("last_topic", None)
            clear_page_context(planning_context)
            query_authority = "source_constrained_override"
            query_mutation_reason = "explicit_source_overrides_history"
            topic_carryover_reason = "explicit_source_overrides_history"
        elif browser_task:
            planning_context.pop("last_topic", None)
            clear_page_context(planning_context)
            query_mutation_reason = "browser_task_blocks_topic_inheritance"
            topic_carryover_reason = "browser_task_blocks_topic_inheritance"
        elif visual_task:
            planning_context.pop("last_topic", None)
            query_authority = "page_context_followup" if page_context_available else "raw_query"
            query_mutation_reason = "visual_task_blocks_topic_inheritance"
            topic_carryover_reason = "visual_task_blocks_topic_inheritance"
        elif direct_location:
            planning_context.pop("last_topic", None)
            clear_page_context(planning_context)
            query_mutation_reason = "map_phrase_bypasses_strict_slot_parse"
            topic_carryover_reason = "map_query_blocks_topic_inheritance"
        elif standalone_query:
            planning_context.pop("last_topic", None)
            clear_page_context(planning_context)
            query_mutation_reason = "standalone_query_blocks_topic_inheritance"
            topic_carryover_reason = "standalone_query_blocks_topic_inheritance"
        elif short_followup and resolved and resolved != raw:
            effective_query = resolved
            query_authority = "resolved_query_followup"
            query_mutation_reason = "short_followup_inherit_topic"
            topic_carryover_applied = True
            topic_carryover_reason = "short_followup_inherit_topic"
        elif has_topic_history:
            planning_context.pop("last_topic", None)
            clear_page_context(planning_context)
            query_mutation_reason = "new_entity_breaks_carryover"
            topic_carryover_reason = "new_entity_breaks_carryover"
        else:
            clear_page_context(planning_context)

        return {
            "raw_query": raw,
            "resolved_query": resolved,
            "effective_query": effective_query,
            "query_authority": query_authority,
            "query_mutation_reason": query_mutation_reason,
            "topic_carryover_applied": topic_carryover_applied,
            "topic_carryover_reason": topic_carryover_reason,
            "source_constraint_applied": source_constraint_applied,
            "memory_context_applied": False,
            "memory_usage_type": "none",
            "db_context_applied": False,
            "page_context_available": page_context_available,
            "continuation_applied": continuation_applied,
            "continuation_reason": continuation_reason,
            "continuation_strategy": continuation_strategy if continuation_applied else "",
            "continuation_of_request_id": continuation_of_request_id,
            "planning_context": planning_context,
            "selected_capability": resolved_capability,
            "web_task_type": str(route_hints.get("web_task_type") or "").strip(),
            "force_web_browse": bool(route_hints.get("force_web_browse")),
        }

    def _get_or_create_web_query_inputs(
        self,
        *,
        message: str,
        session_id: str,
        resolved_capability: str,
        route_hints: dict[str, Any],
    ) -> dict[str, Any]:
        cached = route_hints.get("_web_query_inputs")
        if isinstance(cached, dict) and cached.get("effective_query"):
            return dict(cached)
        resolution = dict(route_hints.get("resolution") or {})
        raw_query = str(route_hints.get("raw_user_text") or message).strip() or message
        resolved_query = str(
            route_hints.get("resolved_query")
            or resolution.get("normalized_query")
            or raw_query
        ).strip() or raw_query
        slots = dict(
            resolution.get("validated_slots")
            or resolution.get("normalized_slots")
            or resolution.get("slots")
            or route_hints.get("resolved_slots")
            or {}
        )
        query_inputs = self._resolve_effective_web_query(
            raw_query=raw_query,
            resolved_query=resolved_query,
            resolved_capability=resolved_capability,
            slots=slots,
            context=self._get_session_web_context(session_id),
            route_hints=route_hints,
        )
        route_hints["_web_query_inputs"] = dict(query_inputs)
        return query_inputs

    def _execute_web_access_request(
        self,
        *,
        message: str,
        session_id: str,
        request_id: str,
        request_origin: str,
        route_hints: dict[str, Any],
        progress_callback: Callable[[str, dict[str, Any]], None] | None,
    ) -> dict[str, Any]:
        resolution = dict(route_hints.get("resolution") or {})
        resolution_route_hints = dict(resolution.get("route_hints") or {})
        force_web_browse = bool(route_hints.get("force_web_browse") or resolution_route_hints.get("force_web_browse"))
        resolved_capability = str(route_hints.get("perception_intent") or resolution.get("capability") or "generic_search").strip()
        if force_web_browse and resolved_capability not in {"generic_search", "news_lookup"}:
            resolved_capability = "generic_search"
        slots = dict(
            resolution.get("validated_slots")
            or resolution.get("normalized_slots")
            or resolution.get("slots")
            or route_hints.get("resolved_slots")
            or {}
        )
        slot_sources = dict(resolution.get("slot_sources") or {})
        query_inputs = self._get_or_create_web_query_inputs(
            message=message,
            session_id=session_id,
            resolved_capability=resolved_capability,
            route_hints=route_hints,
        )
        raw_query = str(query_inputs.get("raw_query") or message).strip() or message
        effective_query = str(query_inputs.get("effective_query") or raw_query).strip() or raw_query
        planning_context = dict(query_inputs.get("planning_context") or {})
        slots = self._sanitize_web_resolution_slots(
            raw_query=raw_query,
            resolved_capability=resolved_capability,
            slots=slots,
            slot_sources=slot_sources,
            query_debug=query_inputs,
        )
        decision = self._web_access_resolver.resolve(
            raw_query=effective_query,
            resolved_query=effective_query,
            resolved_capability=resolved_capability,
            slots=slots,
            context=planning_context,
            source_hint=str(slots.get("source") or ""),
            followup_info={
                "target": route_hints.get("followup_target") or "",
                "web_task_type": route_hints.get("web_task_type") or resolution_route_hints.get("web_task_type") or "",
            },
        )
        plan = self._retrieval_plan_builder.build(
            query=effective_query,
            decision=decision,
            slots=slots,
            context=planning_context,
        )
        execution = self._execution_ladder.execute(
            decision=decision,
            plan=plan,
            session_web_context=planning_context,
            progress_callback=progress_callback,
        )
        self._remember_web_access_context(session_id, execution, request_id=request_id)
        return self._format_web_access_payload(
            message=message,
            query_debug=query_inputs,
            session_id=session_id,
            request_id=request_id,
            request_origin=request_origin,
            resolved_capability=resolved_capability,
            slots=slots,
            execution=execution,
        )

    def _get_session_web_context(self, session_id: str) -> dict[str, Any]:
        with self._session_lock:
            return dict(self._session_web_context.get(session_id, {}))

    def _remember_web_access_context(self, session_id: str, execution: Any, *, request_id: str = "") -> None:
        browser_result = dict(execution.browser_result or {}) if execution is not None else {}
        pages = list(execution.opened_pages or []) if execution is not None else []
        plan = execution.retrieval_plan if execution is not None else None
        source_constraints = dict(plan.source_constraints or {}) if plan is not None else {}
        update: dict[str, Any] = {}
        visual_results = list(execution.visual_results or []) if execution is not None else []
        if browser_result:
            update = {
                "last_url": str(browser_result.get("final_url") or browser_result.get("url") or ""),
                "last_handle_id": str(browser_result.get("handle_id") or ""),
                "last_page_handle": str(browser_result.get("handle_id") or ""),
                "last_title": str(browser_result.get("title") or ""),
            }
        elif pages:
            first = dict(pages[0] or {})
            update = {
                "last_url": str(first.get("final_url") or first.get("url") or ""),
                "last_handle_id": str(first.get("handle_id") or ""),
                "last_page_handle": str(first.get("handle_id") or ""),
                "last_title": str(first.get("title") or ""),
            }
        if visual_results:
            first_visual = visual_results[0]
            update["last_visual_region"] = str(first_visual.region or "").strip()
        snapshot_title = str(update.get("last_title") or "").strip()
        snapshot_page_type = str(browser_result.get("final_page_type") or browser_result.get("page_type") or "").strip()
        snapshot_headings = [
            str(item).strip()
            for item in list(browser_result.get("headings") or [])[:3]
            if str(item).strip()
        ]
        snapshot_parts = [part for part in [snapshot_title, snapshot_page_type, " | ".join(snapshot_headings)] if part]
        if snapshot_parts:
            update["last_page_snapshot_summary"] = " :: ".join(snapshot_parts)
        if str(source_constraints.get("topic") or "").strip():
            update["last_topic"] = str(source_constraints.get("topic") or "").strip()
        if str(source_constraints.get("source_name") or "").strip():
            update["last_source_name"] = str(source_constraints.get("source_name") or "").strip()
        if str(source_constraints.get("source_domain") or "").strip():
            update["last_source_domain"] = str(source_constraints.get("source_domain") or "").strip()
        query_strategy = str(source_constraints.get("query_strategy") or "").strip()
        if query_strategy:
            update["last_query_strategy"] = query_strategy
        if execution is not None and execution.decision is not None:
            update["last_decision_intent"] = str(execution.decision.intent_type or "").strip()
        if execution is not None:
            update["last_failure_reason"] = str(execution.failure_reason or "").strip()
        if plan is not None:
            update["last_retrieval_plan"] = plan.to_dict()
            update["last_target_urls"] = list(plan.target_urls or [])
            update["last_source_constraint_applied"] = bool(source_constraints.get("source_constraint_applied"))
        if browser_result.get("browse_mode_used"):
            update["last_browse_mode_used"] = str(source_constraints.get("browse_strategy") or "source_constrained_browse").strip()
            update["last_task_type"] = str(browser_result.get("task_type") or source_constraints.get("task_type") or "").strip()
            update["last_navigation_hops"] = int(browser_result.get("navigation_hops") or 0)
            update["last_selected_links"] = list(browser_result.get("selected_links") or [])
            update["last_final_page_type"] = str(browser_result.get("final_page_type") or "").strip()
            update["last_final_page_url"] = str(
                browser_result.get("final_page_url")
                or browser_result.get("final_url")
                or browser_result.get("url")
                or ""
            ).strip()
            update["last_stop_reason"] = str(browser_result.get("stop_reason") or "").strip()
            update["last_sources"] = list(browser_result.get("source_candidates") or [])
            update["last_current_source_index"] = int(browser_result.get("current_source_index") or 0)
            update["last_attempted_source_indices"] = list(browser_result.get("attempted_source_indices") or [])
            update["last_links"] = list(browser_result.get("selected_links") or [])
            update["last_page"] = dict(browser_result)
            update["last_query"] = str(
                source_constraints.get("original_user_query")
                or source_constraints.get("user_query")
                or ""
            ).strip()
            update["last_search_again_used"] = bool(browser_result.get("search_again_used"))
            update["last_revised_query"] = str(browser_result.get("revised_query") or "").strip()
            update["last_search_queries_attempted"] = list(browser_result.get("search_queries_attempted") or [])
            update["last_raw_search_results_count"] = int(browser_result.get("raw_search_results_count") or 0)
            update["last_filtered_search_results_count"] = int(browser_result.get("filtered_search_results_count") or 0)
            update["last_filtered_out_reasons"] = list(browser_result.get("filtered_out_reasons") or [])
            update["last_raw_search_results"] = list(browser_result.get("raw_search_results") or [])
            update["last_filtered_search_results"] = list(browser_result.get("filtered_search_results") or [])
            update["last_filtered_out_items_with_reasons"] = list(browser_result.get("filtered_out_items_with_reasons") or [])
            update["last_search_provider_diagnostics"] = list(browser_result.get("search_provider_diagnostics") or [])
            update["last_provider_name"] = str(browser_result.get("provider_name") or "").strip()
            update["last_search_provider_failed"] = bool(browser_result.get("search_provider_failed"))
            update["last_strict_domain_filter_failed"] = bool(browser_result.get("strict_domain_filter_failed"))
            update["last_relaxed_domain_retry_used"] = bool(browser_result.get("relaxed_domain_retry_used"))
            update["last_winning_query_variant"] = str(browser_result.get("winning_query_variant") or "").strip()
            update["last_chosen_source_candidates"] = list(browser_result.get("chosen_source_candidates") or [])
            update["last_chosen_entry_url"] = str(browser_result.get("chosen_entry_url") or "").strip()
            update["last_preferred_domains_applied"] = bool(browser_result.get("preferred_domains_applied"))
        if request_id:
            update["last_request_id"] = request_id
        if execution is not None:
            if not execution.success:
                update["last_result_kind"] = "web_failure"
            elif execution.visual_results:
                update["last_result_kind"] = "visual_read"
            elif query_strategy.startswith("generic_news") or query_strategy == "source_constrained":
                update["last_result_kind"] = "news_card"
            elif execution.browser_result or execution.opened_pages:
                update["last_result_kind"] = "page_result"
            else:
                update["last_result_kind"] = "web_result"
        named_entities = [
            str(source_constraints.get("topic") or "").strip(),
            str(source_constraints.get("source_name") or "").strip(),
            str(source_constraints.get("source_domain") or "").strip(),
            str(update.get("last_title") or "").strip(),
        ]
        update["last_named_entities"] = [item for item in named_entities if item]
        if not update:
            return
        with self._session_lock:
            existing = dict(self._session_web_context.get(session_id, {}))
            existing.update({key: value for key, value in update.items() if value or isinstance(value, list)})
            self._session_web_context[session_id] = existing

    def _format_web_access_payload(
        self,
        *,
        message: str,
        query_debug: dict[str, Any],
        session_id: str,
        request_id: str,
        request_origin: str,
        resolved_capability: str,
        slots: dict[str, Any],
        execution: Any,
    ) -> dict[str, Any]:
        bundle_name = "web-research"
        decision = execution.decision
        plan = execution.retrieval_plan
        retrieval_plan = plan.to_dict() if plan is not None else {}
        browser_availability = execution.browser_availability
        source_constraints = dict(plan.source_constraints or {}) if plan is not None else {}
        browser_result = dict(execution.browser_result or {})
        raw_query = str(query_debug.get("raw_query") or message).strip() or message
        resolved_query = str(query_debug.get("resolved_query") or raw_query).strip() or raw_query
        effective_query = str(query_debug.get("effective_query") or raw_query).strip() or raw_query
        debug_meta = {
            "original_query": raw_query,
            "resolved_query": resolved_query,
            "effective_query": effective_query,
            "query_authority": str(query_debug.get("query_authority") or "raw_query"),
            "query_mutation_reason": str(query_debug.get("query_mutation_reason") or "no_mutation_raw_query_retained"),
            "selected_capability": str(query_debug.get("selected_capability") or resolved_capability or "").strip(),
            "selected_access_mode": decision.access_mode if decision is not None else "",
            "intent_type": decision.intent_type if decision is not None else "",
            "source_name": decision.source_name if decision is not None else "",
            "source_domain": decision.source_domain if decision is not None else "",
            "preferred_domains": list((decision.preferred_domains if decision is not None else []) or []),
            "query_strategy": str(source_constraints.get("query_strategy") or "").strip(),
            "browse_strategy": str(source_constraints.get("browse_strategy") or "").strip(),
            "force_web_browse": bool(source_constraints.get("force_web_browse")),
            "source_constraint_applied": bool(query_debug.get("source_constraint_applied") or source_constraints.get("source_constraint_applied")),
            "topic_carryover_applied": bool(query_debug.get("topic_carryover_applied") or source_constraints.get("topic_carryover_applied")),
            "topic_carryover_reason": str(query_debug.get("topic_carryover_reason") or source_constraints.get("topic_carryover_reason") or "").strip(),
            "memory_context_applied": bool(query_debug.get("memory_context_applied")),
            "memory_usage_type": str(query_debug.get("memory_usage_type") or "none"),
            "db_context_applied": bool(query_debug.get("db_context_applied")),
            "continuation_applied": bool(query_debug.get("continuation_applied")),
            "continuation_reason": str(query_debug.get("continuation_reason") or "").strip(),
            "continuation_strategy": str(query_debug.get("continuation_strategy") or "").strip(),
            "continuation_of_request_id": str(query_debug.get("continuation_of_request_id") or "").strip(),
            "retrieval_plan": retrieval_plan,
            "retrieval_plan_summary": {
                "primary_queries": list(retrieval_plan.get("primary_queries") or [])[:2],
                "target_urls": list(retrieval_plan.get("target_urls") or [])[:2],
                "browser_actions": [str(item.get("type") or "") for item in list(retrieval_plan.get("browser_actions") or [])[:4]],
                "visual_targets": [str(item.get("region") or "") for item in list(retrieval_plan.get("visual_targets") or [])[:4]],
            },
            "execution_level_reached": execution.execution_level,
            "browser_interaction_used": execution.browser_interaction_used,
            "visual_read_used": execution.visual_read_used,
            "fallback_stage": execution.fallback_stage,
            "failure_reason": execution.failure_reason,
            "matched_rules": list((decision.matched_rules if decision is not None else []) or []),
            "browser_available": bool(browser_availability.available) if browser_availability is not None else False,
            "browser_availability_level": browser_availability.level if browser_availability is not None else "",
            "browser_availability_reason": browser_availability.reason if browser_availability is not None else "",
            "browser_fallback_mode": browser_availability.fallback_mode if browser_availability is not None else "",
            "browser_executable_path": browser_availability.executable_path if browser_availability is not None else "",
            "browser_type": browser_availability.browser_type if browser_availability is not None else "",
            "browser_backend": str((browser_availability.diagnostics or {}).get("backend") or "") if browser_availability is not None else "",
            "browser_attach_origin": str((browser_availability.diagnostics or {}).get("cdp_attach_origin") or "") if browser_availability is not None else "",
            "browser_last_smoke_result": str((browser_availability.diagnostics or {}).get("last_smoke_result") or "") if browser_availability is not None else "",
            "browser_last_launch_error": str((browser_availability.diagnostics or {}).get("last_launch_error") or "") if browser_availability is not None else "",
            "post_open_extract_attempted": bool(browser_result.get("post_open_extract_attempted")),
            "post_open_extract_result": str(browser_result.get("post_open_extract_result") or "").strip(),
            "post_open_extract_failure_reason": str(browser_result.get("post_open_extract_failure_reason") or "").strip(),
            "page_context_available": bool(
                query_debug.get("page_context_available")
                or browser_result.get("page_context_available")
                or browser_result.get("final_url")
                or browser_result.get("url")
            ),
            "screenshot_taken": bool(str(browser_result.get("screenshot_path") or "").strip()),
            "visual_target_region": str(browser_result.get("visual_target_region") or "").strip(),
            "visual_failure_reason": execution.failure_reason if str(decision.access_mode if decision is not None else "") == "visual_read" else "",
            "browse_mode_used": bool(browser_result.get("browse_mode_used")),
            "task_type": str(browser_result.get("task_type") or source_constraints.get("task_type") or "").strip(),
            "navigation_hops": int(browser_result.get("navigation_hops") or 0),
            "selected_links": list(browser_result.get("selected_links") or []),
            "score_reasons": list(browser_result.get("score_reasons") or []),
            "final_page_type": str(browser_result.get("final_page_type") or "").strip(),
            "stop_reason": str(browser_result.get("stop_reason") or "").strip(),
            "final_page_url": str(browser_result.get("final_page_url") or browser_result.get("final_url") or browser_result.get("url") or "").strip(),
            "source_candidates": list(browser_result.get("source_candidates") or []),
            "current_source_index": int(browser_result.get("current_source_index") or 0),
            "attempted_source_indices": list(browser_result.get("attempted_source_indices") or []),
            "search_again_used": bool(browser_result.get("search_again_used")),
            "revised_query": str(browser_result.get("revised_query") or "").strip(),
            "source_discovery_completed": bool(browser_result.get("source_discovery_completed")),
            "page_open_attempted": bool(browser_result.get("page_open_attempted")),
            "model_decision_emitted": bool(browser_result.get("model_decision_emitted")),
            "search_queries_attempted": list(browser_result.get("search_queries_attempted") or []),
            "raw_search_results_count": int(browser_result.get("raw_search_results_count") or 0),
            "filtered_search_results_count": int(browser_result.get("filtered_search_results_count") or 0),
            "filtered_out_reasons": list(browser_result.get("filtered_out_reasons") or []),
            "raw_search_results": list(browser_result.get("raw_search_results") or []),
            "filtered_search_results": list(browser_result.get("filtered_search_results") or []),
            "filtered_out_items_with_reasons": list(browser_result.get("filtered_out_items_with_reasons") or []),
            "search_provider_diagnostics": list(browser_result.get("search_provider_diagnostics") or []),
            "provider_name": str(browser_result.get("provider_name") or "").strip(),
            "search_provider_failed": bool(browser_result.get("search_provider_failed")),
            "strict_domain_filter_failed": bool(browser_result.get("strict_domain_filter_failed")),
            "relaxed_domain_retry_used": bool(browser_result.get("relaxed_domain_retry_used")),
            "winning_query_variant": str(browser_result.get("winning_query_variant") or "").strip(),
            "chosen_source_candidates": list(browser_result.get("chosen_source_candidates") or []),
            "chosen_entry_url": str(browser_result.get("chosen_entry_url") or "").strip(),
            "preferred_domains_applied": bool(browser_result.get("preferred_domains_applied")),
            "target_urls": list(retrieval_plan.get("target_urls") or []),
            "success": bool(execution.success),
            "final_action": str(browser_result.get("final_action") or browser_result.get("next_action") or "").strip(),
            "serp_detected": bool(browser_result.get("serp_detected")),
            "serp_links_extracted_count": int(browser_result.get("serp_links_extracted_count") or 0),
            "ranked_candidates": list(browser_result.get("ranked_candidates") or []),
            "top_ranked_candidate": dict(browser_result.get("top_ranked_candidate") or {}),
            "top_ranked_score": float(browser_result.get("top_ranked_score") or 0.0),
            "top_ranked_score_breakdown": dict(browser_result.get("top_ranked_score_breakdown") or {}),
            "serp_selection_reason": str(browser_result.get("serp_selection_reason") or "").strip(),
            "model_selected_candidate": dict(browser_result.get("model_selected_candidate") or {}),
            "final_selected_candidate": dict(browser_result.get("final_selected_candidate") or {}),
            "selection_overridden_by_ranker": bool(browser_result.get("selection_overridden_by_ranker")),
        }
        query_strategy = str(debug_meta.get("query_strategy") or "").strip()
        news_bundle = self._extract_news_bundle_from_web_access(
            execution,
            query_strategy=query_strategy,
            raw_query=raw_query,
        )
        debug_meta["extraction_profile"] = str(news_bundle.get("extraction_profile") or "").strip()
        debug_meta["rendered_item_count"] = int(news_bundle.get("rendered_item_count") or 0)
        activity_meta = self._build_web_activity_payload(
            raw_query=raw_query,
            execution=execution,
            debug_meta=debug_meta,
            news_bundle=news_bundle,
        )

        if not execution.success:
            answer = self._web_access_failure_message_clean(message=raw_query, execution=execution)
            return {
                "assistant_text": answer,
                "assistant_html": "",
                "summary": answer,
                "sources": [],
                "warnings": [execution.failure_reason or "web_access_failed"],
                "structured": {
                    "bundle_name": bundle_name,
                    "card_type": "generic_info",
                    "title": "Web access failed",
                    "summary": answer,
                    "fields": [
                        {"label": "original_query", "value": raw_query},
                        {"label": "effective_query", "value": effective_query},
                        {"label": "access_mode", "value": decision.access_mode if decision is not None else ""},
                        {"label": "fallback_stage", "value": execution.fallback_stage or "none"},
                        {"label": "failure_reason", "value": execution.failure_reason or "unknown"},
                    ],
                    "web_access": debug_meta,
                },
                "skill_name": bundle_name,
                "success": False,
                "changed_files": [],
                "commands_run": [],
                "validations": [],
                "cancelled": False,
                "request_origin": request_origin,
                "request_id": request_id,
                "session_id": session_id,
                "_runtime_executor_path": "web_access_framework",
                "_runtime_web_access": debug_meta,
                "_runtime_activity": activity_meta,
                "_runtime_query_debug": self._build_query_debug_snapshot(**{
                    "raw_query": raw_query,
                    "resolved_query": resolved_query,
                    "effective_query": effective_query,
                    "selected_capability": str(query_debug.get("selected_capability") or resolved_capability or "").strip(),
                    "query_authority": str(query_debug.get("query_authority") or "raw_query"),
                    "query_mutation_reason": str(query_debug.get("query_mutation_reason") or "no_mutation_raw_query_retained"),
                    "topic_carryover_applied": bool(query_debug.get("topic_carryover_applied")),
                    "topic_carryover_reason": str(query_debug.get("topic_carryover_reason") or ""),
                    "source_constraint_applied": bool(query_debug.get("source_constraint_applied")),
                    "memory_context_applied": bool(query_debug.get("memory_context_applied")),
                    "memory_usage_type": str(query_debug.get("memory_usage_type") or "none"),
                    "db_context_applied": bool(query_debug.get("db_context_applied")),
                    "page_context_available": bool(query_debug.get("page_context_available")),
                    "continuation_applied": bool(query_debug.get("continuation_applied")),
                    "continuation_reason": str(query_debug.get("continuation_reason") or ""),
                    "continuation_strategy": str(query_debug.get("continuation_strategy") or ""),
                    "continuation_of_request_id": str(query_debug.get("continuation_of_request_id") or ""),
                }),
            }

        should_render_news_card = bool(
            resolved_capability == "news_lookup"
            or query_strategy.startswith("generic_news")
            or (
                str(decision.intent_type if decision is not None else "") == "source_constrained_lookup"
                and self._looks_like_news_updates_query(raw_query)
            )
        )
        if should_render_news_card and news_bundle.get("items"):
            items = list(news_bundle.get("items") or [])
            topic_title = str(source_constraints.get("topic") or slots.get("topic") or "").strip()
            title = f"{topic_title or (decision.source_name if decision is not None else '') or '新闻'}简报"
            answer = self._news_summary_from_items_clean(items)
            structured = {
                "bundle_name": bundle_name,
                "card_type": "news_card",
                "title": title,
                "items": items,
                "web_access": debug_meta,
            }
            return {
                "assistant_text": answer,
                "assistant_html": "",
                "summary": answer,
                "sources": [item.get("url", "") for item in items if item.get("url")],
                "warnings": [],
                "structured": structured,
                "skill_name": bundle_name,
                "success": True,
                "changed_files": [],
                "commands_run": [],
                "validations": [],
                "cancelled": False,
                "request_origin": request_origin,
                "request_id": request_id,
                "session_id": session_id,
                "_runtime_executor_path": "web_access_framework",
                "_runtime_web_access": debug_meta,
                "_runtime_activity": activity_meta,
                "_runtime_query_debug": self._build_query_debug_snapshot(**{
                    "raw_query": raw_query,
                    "resolved_query": resolved_query,
                    "effective_query": effective_query,
                    "selected_capability": str(query_debug.get("selected_capability") or resolved_capability or "").strip(),
                    "query_authority": str(query_debug.get("query_authority") or "raw_query"),
                    "query_mutation_reason": str(query_debug.get("query_mutation_reason") or "no_mutation_raw_query_retained"),
                    "topic_carryover_applied": bool(query_debug.get("topic_carryover_applied")),
                    "topic_carryover_reason": str(query_debug.get("topic_carryover_reason") or ""),
                    "source_constraint_applied": bool(query_debug.get("source_constraint_applied")),
                    "memory_context_applied": bool(query_debug.get("memory_context_applied")),
                    "memory_usage_type": str(query_debug.get("memory_usage_type") or "none"),
                    "db_context_applied": bool(query_debug.get("db_context_applied")),
                    "page_context_available": bool(query_debug.get("page_context_available")),
                    "continuation_applied": bool(query_debug.get("continuation_applied")),
                    "continuation_reason": str(query_debug.get("continuation_reason") or ""),
                    "continuation_strategy": str(query_debug.get("continuation_strategy") or ""),
                    "continuation_of_request_id": str(query_debug.get("continuation_of_request_id") or ""),
                }),
            }

        if execution.visual_results:
            first_visual = execution.visual_results[0]
            assistant_text = str(first_visual.summary or "").strip() or self._generic_web_answer_from_execution_clean(execution)
            source_url = str(first_visual.source_url or "").strip()
            structured = {
                "bundle_name": bundle_name,
                "card_type": "visual_read",
                "title": self._visual_region_label_clean(str(first_visual.region or "").strip()),
                "region": str(first_visual.region or "").strip(),
                "summary": assistant_text,
                "confidence": first_visual.confidence,
                "source_url": source_url,
                "visual_type": str(first_visual.visual_type or "unknown").strip() or "unknown",
                "screenshot_path": str(first_visual.screenshot_path or "").strip(),
                "web_access": debug_meta,
            }
            return {
                "assistant_text": assistant_text,
                "assistant_html": "",
                "summary": assistant_text,
                "sources": [source_url] if source_url else [],
                "warnings": [],
                "structured": structured,
                "skill_name": bundle_name,
                "success": True,
                "changed_files": [],
                "commands_run": [],
                "validations": [],
                "cancelled": False,
                "request_origin": request_origin,
                "request_id": request_id,
                "session_id": session_id,
                "_runtime_executor_path": "web_access_framework",
                "_runtime_web_access": debug_meta,
                "_runtime_activity": activity_meta,
                "_runtime_query_debug": self._build_query_debug_snapshot(**{
                    "raw_query": raw_query,
                    "resolved_query": resolved_query,
                    "effective_query": effective_query,
                    "selected_capability": str(query_debug.get("selected_capability") or resolved_capability or "").strip(),
                    "query_authority": str(query_debug.get("query_authority") or "raw_query"),
                    "query_mutation_reason": str(query_debug.get("query_mutation_reason") or "no_mutation_raw_query_retained"),
                    "topic_carryover_applied": bool(query_debug.get("topic_carryover_applied")),
                    "topic_carryover_reason": str(query_debug.get("topic_carryover_reason") or ""),
                    "source_constraint_applied": bool(query_debug.get("source_constraint_applied")),
                    "memory_context_applied": bool(query_debug.get("memory_context_applied")),
                    "memory_usage_type": str(query_debug.get("memory_usage_type") or "none"),
                    "db_context_applied": bool(query_debug.get("db_context_applied")),
                    "page_context_available": bool(query_debug.get("page_context_available")),
                    "continuation_applied": bool(query_debug.get("continuation_applied")),
                    "continuation_reason": str(query_debug.get("continuation_reason") or ""),
                    "continuation_strategy": str(query_debug.get("continuation_strategy") or ""),
                    "continuation_of_request_id": str(query_debug.get("continuation_of_request_id") or ""),
                }),
            }

        spoken = self._generic_web_answer_from_execution_clean(execution)
        structured = {
            "bundle_name": bundle_name,
            "card_type": "generic_info",
            "title": str(browser_result.get("title") or "Web access result"),
            "summary": spoken,
            "fields": self._generic_web_fields(execution),
            "web_access": debug_meta,
        }
        return {
            "assistant_text": spoken,
            "assistant_html": "",
            "summary": spoken,
            "sources": [
                str(item.get("url") or item.get("final_url") or "").strip()
                for item in list(execution.search_results or []) + list(execution.opened_pages or [])
                if str(item.get("url") or item.get("final_url") or "").strip()
            ],
            "warnings": [],
            "structured": structured,
            "skill_name": bundle_name,
            "success": True,
            "changed_files": [],
            "commands_run": [],
            "validations": [],
            "cancelled": False,
            "request_origin": request_origin,
            "request_id": request_id,
            "session_id": session_id,
            "_runtime_executor_path": "web_access_framework",
            "_runtime_web_access": debug_meta,
            "_runtime_activity": activity_meta,
            "_runtime_query_debug": self._build_query_debug_snapshot(**{
                "raw_query": raw_query,
                "resolved_query": resolved_query,
                "effective_query": effective_query,
                "selected_capability": str(query_debug.get("selected_capability") or resolved_capability or "").strip(),
                "query_authority": str(query_debug.get("query_authority") or "raw_query"),
                "query_mutation_reason": str(query_debug.get("query_mutation_reason") or "no_mutation_raw_query_retained"),
                "topic_carryover_applied": bool(query_debug.get("topic_carryover_applied")),
                "topic_carryover_reason": str(query_debug.get("topic_carryover_reason") or ""),
                "source_constraint_applied": bool(query_debug.get("source_constraint_applied")),
                "memory_context_applied": bool(query_debug.get("memory_context_applied")),
                "memory_usage_type": str(query_debug.get("memory_usage_type") or "none"),
                "db_context_applied": bool(query_debug.get("db_context_applied")),
                "page_context_available": bool(query_debug.get("page_context_available")),
                "continuation_applied": bool(query_debug.get("continuation_applied")),
                "continuation_reason": str(query_debug.get("continuation_reason") or ""),
                "continuation_strategy": str(query_debug.get("continuation_strategy") or ""),
                "continuation_of_request_id": str(query_debug.get("continuation_of_request_id") or ""),
            }),
        }

    def _build_web_activity_payload(
        self,
        *,
        raw_query: str,
        execution: Any,
        debug_meta: dict[str, Any],
        news_bundle: dict[str, Any],
    ) -> dict[str, Any]:
        browser_result = dict(execution.browser_result or {})
        source_label = (
            str(debug_meta.get("source_name") or "").strip()
            or str(debug_meta.get("source_domain") or "").strip()
            or self._source_name_from_url_clean(str(browser_result.get("final_page_url") or browser_result.get("final_url") or browser_result.get("url") or ""))
        )
        task_type = str(debug_meta.get("task_type") or browser_result.get("task_type") or "").strip()
        entries: list[dict[str, str]] = [
            self._activity_entry("understanding_request", "理解请求", f"正在判断“{raw_query}”更像参数、发布、新闻还是一般信息。"),
        ]
        if bool(debug_meta.get("source_discovery_completed")):
            source_count = len(list(debug_meta.get("source_candidates") or []))
            entries.append(
                self._activity_entry(
                    "source_discovery_completed",
                    "鎵惧埌鏉ユ簮",
                    f"已为“{raw_query}”选出 {source_count or 1} 个候选网页来源。",
                )
            )
        if bool(debug_meta.get("continuation_applied")):
            strategy = str(debug_meta.get("continuation_strategy") or "").strip()
            entries.append(
                self._activity_entry(
                    "navigating_deeper",
                    "继续查找",
                    "正在沿上一轮网页路径继续找。" if strategy != "expand_source" else "正在放弃当前来源，改从更广的网页来源继续找。",
                )
            )

        if execution.search_results:
            entries.append(
                self._activity_entry(
                    "searching_web",
                    "查找网页",
                    f"正在查找与“{raw_query}”最相关的网页来源。",
                )
            )

        if bool(debug_meta.get("page_open_attempted")):
            entries.append(
                self._activity_entry(
                    "page_open_attempted",
                    "打开页面",
                    "已经至少打开一个候选来源，正在判断是否继续深入。",
                )
            )

        target_url = str(browser_result.get("final_page_url") or browser_result.get("final_url") or browser_result.get("url") or "").strip()
        if not target_url:
            plan = getattr(execution, "retrieval_plan", None)
            if plan is not None:
                target_url = str((list(getattr(plan, "target_urls", []) or [""]) or [""])[0] or "").strip()
        if target_url:
            label = source_label or self._source_name_from_url_clean(target_url) or "网页入口"
            entries.append(
                self._activity_entry("opening_page", "打开页面", f"正在打开 {label} 的入口页。")
            )

        if browser_result:
            page_label = str(browser_result.get("title") or browser_result.get("final_page_type") or "当前页面").strip()
            entries.append(
                self._activity_entry("understanding_page", "理解页面", f"正在判断当前页面属于 {page_label}。")
            )

        if bool(debug_meta.get("model_decision_emitted")):
            entries.append(
                self._activity_entry("model_decision_emitted", "决定下一步", "模型已经对当前页面给出了下一步决策。")
            )
        selected_links = list(browser_result.get("selected_links") or [])
        if selected_links:
            link_text = " / ".join(
                str(item.get("text") or item.get("url") or "").strip()
                for item in selected_links[:2]
                if str(item.get("text") or item.get("url") or "").strip()
            )
            entries.append(
                self._activity_entry("ranking_links", "比较候选链接", f"正在比较页面里的候选入口：{link_text or '候选链接'}。")
            )

        if bool(browser_result.get("browse_mode_used")) and int(browser_result.get("navigation_hops") or 0) > 0:
            entries.append(
                self._activity_entry(
                    "navigating_deeper",
                    "继续深入",
                    f"已在站内继续跳转 {int(browser_result.get('navigation_hops') or 0)} 次，定位更相关的页面。",
                )
            )

        if browser_result or news_bundle.get("items"):
            entries.append(
                self._activity_entry(
                    "extracting_answer",
                    "提取答案",
                    "正在从最终页面提取可用内容，并整理成回答。",
                )
            )

        if news_bundle.get("items"):
            entries.append(
                self._activity_entry(
                    "summarizing_result",
                    "整理结果",
                    f"已整理出 {len(list(news_bundle.get('items') or []))} 条结构化结果。",
                )
            )
        elif execution.failure_reason:
            entries.append(
                self._activity_entry(
                    "summarizing_result",
                    "整理结果",
                    str(self._web_access_failure_message_clean(message=raw_query, execution=execution) or "").strip(),
                )
            )

        return {
            "summary": "已思考",
            "duration_ms": 0,
            "entries": entries,
            "sources": self._activity_sources_from_execution(execution, news_bundle=news_bundle),
        }

    @staticmethod
    def _activity_entry(stage: str, title: str, detail: str) -> dict[str, str]:
        return {
            "stage": str(stage or "").strip(),
            "title": str(title or "").strip(),
            "detail": str(detail or "").strip(),
        }

    def _activity_sources_from_execution(self, execution: Any, *, news_bundle: dict[str, Any]) -> list[dict[str, str]]:
        candidates: list[tuple[str, str]] = []
        browser_result = dict(execution.browser_result or {})
        final_url = str(browser_result.get("final_page_url") or browser_result.get("final_url") or browser_result.get("url") or "").strip()
        if final_url:
            candidates.append((self._source_name_from_url_clean(final_url) or final_url, final_url))
        for item in list(browser_result.get("selected_links") or [])[:6]:
            url = str(item.get("url") or "").strip()
            if url:
                candidates.append((str(item.get("text") or self._source_name_from_url_clean(url) or url).strip(), url))
        for item in list(news_bundle.get("items") or [])[:8]:
            url = str(item.get("url") or "").strip()
            if url:
                candidates.append((str(item.get("source") or self._source_name_from_url_clean(url) or url).strip(), url))
        for item in list(execution.search_results or [])[:6]:
            url = str(item.get("url") or "").strip()
            if url:
                candidates.append((str(item.get("title") or self._source_name_from_url_clean(url) or url).strip(), url))
        deduped: list[dict[str, str]] = []
        seen: set[str] = set()
        for label, url in candidates:
            lowered = url.lower()
            if not url or lowered in seen:
                continue
            seen.add(lowered)
            deduped.append({"label": label or url, "url": url})
            if len(deduped) >= 12:
                break
        return deduped

    @staticmethod
    def _looks_like_deal_request(query: str) -> bool:
        text = str(query or "").strip().lower()
        return any(token in text for token in ("优惠", "折扣", "便宜", "低价", "探底", "值得买", "deal", "sale"))

    @staticmethod
    def _is_promotional_or_deal_headline(title: str) -> bool:
        text = str(title or "").strip().lower()
        if not text:
            return False
        extra_markers = (
            "元",
            "探底",
            "近期新低",
            "新低",
            "闭眼囤",
            "尝鲜发车",
            "发车",
            "商超",
            "日常",
            "到手",
            "券后",
            "补贴",
            "包邮",
            "毛/",
            "条",
            "袋",
            "瓶",
            "枚",
            "套装",
            "开口蛋",
            "酱牛肉",
            "工具",
            "盐焗",
        )
        if any(marker.lower() in text for marker in extra_markers):
            return True
        markers = (
            "元",
            "探底",
            "新低",
            "闭眼囤",
            "尝鲜发车",
            "商超",
            "毛 /",
            "9.9",
            "12.8",
            "15.6",
            "开口蛋",
            "酱牛肉",
            "工具",
            "盐焗",
            "套装",
        )
        return any(marker.lower() in text for marker in markers)

    @staticmethod
    def _is_probable_news_heading(title: str) -> bool:
        cleaned = str(title or "").strip()
        lowered = cleaned.lower()
        if not cleaned:
            return False
        if lowered in {
            "all",
            "global",
            "source",
            "news",
            "latest news",
            "latest",
            "home",
            "stories",
            "microsoft source",
        }:
            return False
        if len(cleaned) <= 3:
            return False
        if re.fullmatch(r"[A-Za-z ]{1,18}", cleaned) and len(cleaned.split()) <= 2:
            return False
        return True

    def _filter_news_items(
        self,
        items: list[dict[str, Any]],
        *,
        query_strategy: str,
        raw_query: str,
    ) -> list[dict[str, Any]]:
        if not items:
            return []
        allow_promotional = self._looks_like_deal_request(raw_query)
        filtered: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in items:
            headline = str(item.get("headline") or "").strip()
            if not headline:
                continue
            normalized = headline.lower()
            if normalized in seen:
                continue
            if (
                (query_strategy.startswith("generic_news") or query_strategy == "source_constrained")
                and not allow_promotional
                and self._is_promotional_or_deal_headline(headline)
            ):
                continue
            seen.add(normalized)
            filtered.append(item)
            if len(filtered) >= 8:
                break
        return filtered

    def _extract_news_bundle_from_web_access(
        self,
        execution: Any,
        *,
        query_strategy: str,
        raw_query: str,
    ) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        extraction_profile = ""
        source_name = str(execution.decision.source_name or "") if execution.decision is not None else ""
        browser_result = dict(execution.browser_result or {})

        search_items: list[dict[str, Any]] = []
        for result in list(execution.search_results or [])[:8]:
            title = str(result.get("title") or "").strip()
            url = str(result.get("url") or "").strip()
            if not title or not url:
                continue
            search_items.append(
                {
                    "headline": title,
                    "source": self._source_name_from_url_clean(url),
                    "published_at": "",
                    "summary": str(result.get("snippet") or "").strip(),
                    "image_path": "",
                    "url": url,
                    "tags": [],
                }
            )
        search_items = self._filter_news_items(search_items, query_strategy=query_strategy, raw_query=raw_query)
        if search_items:
            return {
                "items": search_items[:8],
                "extraction_profile": "search_results",
                "rendered_item_count": len(search_items[:8]),
            }

        if browser_result:
            visible_text = str(browser_result.get("visible_text") or "").strip()
            fallback_url = str(browser_result.get("final_url") or browser_result.get("url") or "").strip()
            parsed_items = self._news_items_from_visible_text(visible_text, source_name=source_name, fallback_url=fallback_url)
            parsed_items = self._filter_news_items(parsed_items, query_strategy=query_strategy, raw_query=raw_query)
            if parsed_items:
                return {
                    "items": parsed_items[:8],
                    "extraction_profile": "visible_text_news_items",
                    "rendered_item_count": len(parsed_items[:8]),
                }

            heading_items: list[dict[str, Any]] = []
            for heading in list(browser_result.get("headings") or [])[:20]:
                title = str(heading or "").strip()
                if not self._is_probable_news_heading(title):
                    continue
                heading_items.append(
                    {
                        "headline": title,
                        "source": source_name or self._source_name_from_url_clean(fallback_url),
                        "published_at": "",
                        "summary": "",
                        "image_path": "",
                        "url": fallback_url,
                        "tags": [],
                    }
                )
            heading_items = self._filter_news_items(heading_items, query_strategy=query_strategy, raw_query=raw_query)
            if heading_items:
                return {
                    "items": heading_items[:8],
                    "extraction_profile": "newsroom_headings",
                    "rendered_item_count": len(heading_items[:8]),
                }

            for link in list(browser_result.get("links") or [])[:20]:
                title = str(link.get("text") or "").strip()
                url = str(link.get("url") or browser_result.get("final_url") or "").strip()
                if not self._is_probable_news_heading(title) or not url:
                    continue
                items.append(
                    {
                        "headline": title,
                        "source": self._source_name_from_url_clean(url) or source_name,
                        "published_at": "",
                        "summary": "",
                        "image_path": "",
                        "url": url,
                        "tags": [],
                    }
                )
            items = self._filter_news_items(items, query_strategy=query_strategy, raw_query=raw_query)
            if items:
                extraction_profile = "news_links"

        return {
            "items": items[:8],
            "extraction_profile": extraction_profile,
            "rendered_item_count": len(items[:8]),
        }

    def _news_items_from_web_access(self, execution: Any) -> list[dict[str, Any]]:
        query_strategy = str((execution.retrieval_plan.source_constraints or {}).get("query_strategy") or "").strip() if getattr(execution, "retrieval_plan", None) is not None else ""
        bundle = self._extract_news_bundle_from_web_access(
            execution,
            query_strategy=query_strategy,
            raw_query="",
        )
        return list(bundle.get("items") or [])

    def _news_items_from_visible_text(self, visible_text: str, *, source_name: str = "", fallback_url: str = "") -> list[dict[str, Any]]:
        if not visible_text:
            return []
        lines = [line.strip() for line in visible_text.splitlines() if line.strip()]
        items: list[dict[str, Any]] = []
        pending_label = ""
        seen_headlines: set[str] = set()
        timestamp_re = re.compile(r"(?:\d{4}-\d{2}-\d{2}|\d{1,2}月\d{1,2}日)\s+\d{2}:\d{2}(?::\d{2})?$")
        for index, line in enumerate(lines):
            if line.startswith("[") and line.endswith("]"):
                pending_label = line.strip("[]")
                continue
            inline_match = timestamp_re.search(line)
            if inline_match and inline_match.start() > 0:
                timestamp = inline_match.group(0)
                headline = line[: inline_match.start()].strip(" -—|")
                if headline.startswith("[") and "]" in headline:
                    label, _, remainder = headline.partition("]")
                    pending_label = label.strip("[]")
                    headline = remainder.strip()
                normalized = headline.lower()
                if headline and normalized not in seen_headlines:
                    seen_headlines.add(normalized)
                    items.append(
                        {
                            "headline": headline,
                            "source": source_name or pending_label or "网页",
                            "published_at": timestamp,
                            "summary": "",
                            "image_path": "",
                            "url": fallback_url,
                            "tags": [pending_label] if pending_label else [],
                        }
                    )
                if len(items) >= 8:
                    break
                continue
            if not timestamp_re.match(line):
                continue
            headline = ""
            back = index - 1
            while back >= 0:
                candidate = lines[back]
                if not candidate or timestamp_re.match(candidate):
                    back -= 1
                    continue
                if candidate.startswith("[") and candidate.endswith("]"):
                    pending_label = candidate.strip("[]")
                    back -= 1
                    continue
                headline = candidate
                break
            if not headline:
                continue
            normalized = headline.lower()
            if normalized in seen_headlines:
                continue
            seen_headlines.add(normalized)
            items.append(
                {
                    "headline": headline,
                    "source": source_name or pending_label or "网页",
                    "published_at": line,
                    "summary": "",
                    "image_path": "",
                    "url": fallback_url,
                    "tags": [pending_label] if pending_label else [],
                }
            )
            if len(items) >= 8:
                break
        return items

    def _news_summary_from_items(self, items: list[dict[str, Any]]) -> str:
        if not items:
            return "暂时还没有整理出可靠的新闻结果。"
        headlines = [str(item.get("headline") or "").strip() for item in items[:3] if str(item.get("headline") or "").strip()]
        if not headlines:
            return "暂时还没有整理出可靠的新闻结果。"
        return "今天值得关注的新闻有：" + "；".join(headlines)

    def _generic_web_answer_from_execution(self, execution: Any) -> str:
        if execution.visual_results:
            return " ".join(str(item.summary or "").strip() for item in execution.visual_results if str(item.summary or "").strip()).strip()
        browser_result = dict(execution.browser_result or {})
        if browser_result:
            snippets = [str(item).strip() for item in list(browser_result.get("headings") or [])[:3] if str(item).strip()]
            if snippets:
                return "我在页面上看到：" + "；".join(snippets)
            visible = str(browser_result.get("visible_text") or "").strip()
            if visible:
                return visible.splitlines()[0][:260]
        for result in list(execution.search_results or [])[:3]:
            title = str(result.get("title") or "").strip()
            snippet = str(result.get("snippet") or "").strip()
            if title and snippet:
                return f"{title}. {snippet}"[:320]
            if title:
                return title
        return "我已经打开了网页，但还没提取到可靠内容。"

    def _web_access_failure_message(self, *, message: str, execution: Any) -> str:
        decision = execution.decision
        browser_status = execution.browser_availability
        source_name = str(decision.source_name or "").strip() if decision is not None else ""
        if (
            decision is not None
            and decision.access_mode == "browser_interaction"
            and browser_status is not None
            and not browser_status.available
        ):
            if browser_status.reason == "browser_launch_failed":
                return "??????????????????????????????????????"
            if browser_status.reason == "browser_binary_missing":
                return "???????????????????????????????????????"
            if browser_status.reason == "package_missing":
                return "???????????????????????????????????????"
            return "????????????????????????????????"
        if decision is not None and decision.intent_type == "source_constrained_lookup" and source_name:
            return f"我在{source_name}没找到这次要的可靠结果，要不要我扩大到全网？"
        if decision is not None and decision.access_mode == "visual_read":
            return "这个页面需要结合页面区域来阅读，但当前还没拿到可靠的视觉内容。"
        if execution.fallback_stage:
            return "我已经按照备选网页访问路径进行了降级，但暂时仍没有找到可靠结果。"
        return "已尝试多种网页访问方式，但暂未找到可靠结果。"

    def _source_name_from_url(self, url: str) -> str:
        text = str(url or "").strip().lower()
        if "ithome.com" in text:
            return "IT之家"
        if "openai.com" in text:
            return "OpenAI"
        return text.split("/")[2] if text.startswith(("http://", "https://")) else ""

    @staticmethod
    def _visual_region_label(region: str) -> str:
        normalized = str(region or "").strip().lower()
        if normalized == "top_banner":
            return "页面顶部公告"
        if normalized == "hero_section":
            return "首屏主区域"
        if normalized == "results_panel":
            return "结果区域"
        return "页面视觉阅读"

    def _generic_web_fields(self, execution: Any) -> list[dict[str, str]]:
        fields: list[dict[str, str]] = []
        if execution.decision is not None:
            fields.append({"label": "access_mode", "value": execution.decision.access_mode})
            fields.append({"label": "intent_type", "value": execution.decision.intent_type})
            if execution.decision.source_domain:
                fields.append({"label": "source_domain", "value": execution.decision.source_domain})
        browser_result = dict(execution.browser_result or {})
        if browser_result and browser_result.get("final_url"):
            fields.append({"label": "url", "value": str(browser_result.get("final_url") or "")})
        elif execution.opened_pages:
            first = dict(execution.opened_pages[0] or {})
            url = str(first.get("final_url") or first.get("url") or "").strip()
            if url:
                fields.append({"label": "url", "value": url})
        if browser_result.get("browse_mode_used"):
            fields.append({"label": "browse_mode", "value": "source_constrained_browse"})
            task_type = str(browser_result.get("task_type") or "").strip()
            if task_type:
                fields.append({"label": "task_type", "value": task_type})
            final_page_type = str(browser_result.get("final_page_type") or "").strip()
            if final_page_type:
                fields.append({"label": "final_page_type", "value": final_page_type})
            stop_reason = str(browser_result.get("stop_reason") or "").strip()
            if stop_reason:
                fields.append({"label": "stop_reason", "value": stop_reason})
        return fields

    def _source_name_from_url(self, url: str) -> str:
        text = str(url or "").strip().lower()
        if "ithome.com" in text:
            return "IT??"
        if "openai.com" in text:
            return "OpenAI"
        return text.split("/")[2] if text.startswith(("http://", "https://")) else ""

    def _news_summary_from_items_clean(self, items: list[dict[str, Any]]) -> str:
        if not items:
            return "暂时还没有整理出可靠的新闻结果。"
        headlines = [str(item.get("headline") or "").strip() for item in items[:3] if str(item.get("headline") or "").strip()]
        if not headlines:
            return "暂时还没有整理出可靠的新闻结果。"
        return "今天值得关注的新闻有：" + "；".join(headlines)

    def _generic_web_answer_from_execution_clean(self, execution: Any) -> str:
        if execution.visual_results:
            return " ".join(str(item.summary or "").strip() for item in execution.visual_results if str(item.summary or "").strip()).strip()
        browser_result = dict(execution.browser_result or {})
        if browser_result.get("browse_mode_used"):
            task_type = str(browser_result.get("task_type") or "").strip()
            title = str(browser_result.get("title") or "").strip()
            final_url = str(browser_result.get("final_page_url") or browser_result.get("final_url") or browser_result.get("url") or "").strip()
            headings = [str(item).strip() for item in list(browser_result.get("headings") or []) if str(item).strip()]
            visible = str(browser_result.get("visible_text") or "").strip()
            key_values = [str(item).strip() for item in list(browser_result.get("key_values") or []) if str(item).strip()]
            table_rows = [str(item).strip() for item in list(browser_result.get("table_rows") or []) if str(item).strip()]

            if task_type == "specs":
                evidence = key_values[:5] or table_rows[:5]
                if evidence:
                    return f"我已经定位到规格页面：{title or final_url}。页面里提到了：{'；'.join(evidence[:4])}"
                spec_lines = self._spec_signal_lines(visible)
                if spec_lines:
                    return f"我已经定位到规格相关页面：{title or final_url}。目前页面上能看到：{'；'.join(spec_lines[:4])}"
                if title or final_url:
                    return f"我已经进入参数相关页面：{title or final_url}。"

            if task_type == "release":
                if headings:
                    return f"我已经沿着官网继续查找发布信息，目前定位到：{headings[0]}。"
                if title or final_url:
                    return f"我已经沿着官网继续查找发布信息，目前停在：{title or final_url}。"

            if task_type == "product_lookup":
                if title or headings:
                    return f"我已经进入相关产品页面：{title or headings[0]}。"

            if task_type == "news" and headings:
                return "我在官网新闻页看到：" + "；".join(headings[:3])

        if browser_result:
            snippets = [str(item).strip() for item in list(browser_result.get("headings") or [])[:3] if str(item).strip()]
            if snippets:
                return "我在页面上看到：" + "；".join(snippets)
            visible = str(browser_result.get("visible_text") or "").strip()
            if visible:
                return visible.splitlines()[0][:260]
        for result in list(execution.search_results or [])[:3]:
            title = str(result.get("title") or "").strip()
            snippet = str(result.get("snippet") or "").strip()
            if title and snippet:
                return f"{title}. {snippet}"[:320]
            if title:
                return title
        return "我已经打开了网页，但还没提取到可靠内容。"

    @staticmethod
    def _spec_signal_lines(visible_text: str) -> list[str]:
        lines = [line.strip() for line in str(visible_text or "").splitlines() if line.strip()]
        keywords = ("芯片", "显示", "存储", "相机", "电池", "尺寸", "chip", "display", "storage", "camera", "battery", "dimensions")
        matches: list[str] = []
        for line in lines:
            lowered = line.lower()
            if any(keyword.lower() in lowered for keyword in keywords):
                matches.append(line[:120])
            if len(matches) >= 5:
                break
        return matches

    def _web_access_failure_message_clean(self, *, message: str, execution: Any) -> str:
        decision = execution.decision
        browser_status = execution.browser_availability
        source_name = str(decision.source_name or "").strip() if decision is not None else ""
        source_constraints = dict(execution.retrieval_plan.source_constraints or {}) if getattr(execution, "retrieval_plan", None) is not None else {}
        query_strategy = str(source_constraints.get("query_strategy") or "").strip()
        continuation_strategy = str(source_constraints.get("continuation_strategy") or "").strip()
        browser_result = dict(execution.browser_result or {})
        task_type = str(browser_result.get("task_type") or source_constraints.get("task_type") or "").strip()
        final_page = str(browser_result.get("final_page_url") or browser_result.get("final_url") or browser_result.get("url") or "").strip()
        if (
            decision is not None
            and decision.access_mode == "browser_interaction"
            and browser_status is not None
            and not browser_status.available
        ):
            if browser_status.reason == "browser_launch_failed":
                return "这个网站需要浏览器访问，但当前浏览器启动失败，我已经尝试了替代方式。"
            if browser_status.reason == "browser_binary_missing":
                return "这个网站需要浏览器访问，但当前环境缺少可用浏览器。"
            if browser_status.reason == "package_missing":
                return "这个网站需要浏览器访问，但当前浏览器自动化依赖还没有准备好。"
            return "这个网站需要浏览器访问，但当前环境还不能稳定执行浏览器交互。"
        if execution.failure_reason == "human_verification_interstitial_detected":
            return "这个网站打开后出现了人机验证或等待页，我暂时还没拿到可读取的正文内容。"
        if execution.failure_reason == "page_not_found":
            return "这个网页地址当前返回了 404 或未找到页面，所以我没法从这里提取可靠内容。"
        if execution.failure_reason == "page_loaded_but_main_region_empty":
            return "网页已经打开，但主内容区域暂时没有可稳定提取的文本。"
        if execution.failure_reason == "post_open_extract_failed":
            return "网页已经打开，但后续内容提取没有拿到可靠结果。"
        if execution.failure_reason == "browse_entry_open_failed":
            if final_page:
                return f"我已经开始沿着官网继续找，但入口页没有成功打开：{final_page}"
            return "我已经开始沿着官网继续找，但入口页没有成功打开。"
        if execution.failure_reason == "browse_source_discovery_failed":
            attempted = list(browser_result.get("search_queries_attempted") or [])
            filtered = int(browser_result.get("filtered_search_results_count") or 0)
            return (
                "我已经进入了网页来源发现阶段，"
                f"但这一轮没拿到可用的候选来源。"
                f"已尝试 {max(1, len(attempted))} 条 query，"
                f"筛出 {filtered} 条可用结果。"
            )
            return "鎴戝凡缁忓紑濮嬬綉椤垫祻瑙堬紝浣嗚繖涓€杞繕娌℃湁鎷垮埌鍙敤鐨勫€欓€夋潵婧愩€?"
        if execution.failure_reason == "browse_no_candidate_links":
            if task_type == "specs":
                return "我已经打开了官网入口页，但没在页面里找到足够明确的规格入口或产品链接。"
            if task_type == "release":
                return "我已经打开了官网入口页，但没在页面里找到足够明确的发布公告或新闻稿入口。"
            return "我已经打开了官网入口页，但没在页面里找到足够明确的下一步链接。"
        if execution.failure_reason == "browse_navigation_exhausted":
            return "我已经沿着官网继续找了两层页面，但这一轮没有继续定位到更明确的目标页。"
        if execution.failure_reason == "browse_target_not_reached":
            if task_type == "specs":
                return "我已经进入官网并继续查找，但这一轮还没有走到明确的规格页。"
            if task_type == "release":
                return "我已经进入官网并继续查找，但这一轮还没有走到明确的发布公告或新闻稿页。"
            return "我已经进入官网并继续查找，但这一轮还没有走到明确的目标页。"
        if execution.failure_reason == "browse_target_reached_but_extract_failed":
            if final_page:
                return f"我已经走到了目标页面，但还没能稳定提取内容：{final_page}"
            return "我已经走到了目标页面，但还没能稳定提取内容。"
        if execution.failure_reason == "no_search_results" and query_strategy == "generic_web_release":
            return "我查了当前可访问的网页来源，但还没有找到可靠的公开发布时间或正式发布信号。"
        if execution.failure_reason == "no_search_results" and query_strategy == "source_constrained_release":
            return f"我查了{source_name or '当前官方来源'}，但还没有找到可靠的公开发布时间或正式发布信号。"
        if execution.failure_reason == "no_search_results" and query_strategy == "source_constrained_specs":
            return f"我查了{source_name or '当前官方来源'}，但还没有找到可靠的官方参数或规格页。"
        if execution.failure_reason == "no_search_results" and query_strategy == "generic_news":
            return "我暂时没在当前可访问的新闻源里找到今天这类新闻，要不要我换个来源继续找？"
        if execution.failure_reason == "no_search_results" and continuation_strategy == "expand_source":
            return "我已经换了来源继续找，但暂时还没有拿到可靠结果。"
        if execution.failure_reason == "no_search_results" and continuation_strategy == "continue_previous_web_plan":
            return "我已经沿着上一轮网页检索继续找了，但暂时还没有找到可靠结果。"
        if execution.failure_reason == "no_search_results" and query_strategy == "generic_web":
            return "我查了当前可访问的网页来源，但暂时没有找到可靠结果。"
        if decision is not None and decision.intent_type == "source_constrained_lookup" and source_name:
            return f"我在{source_name}没找到这次要的可靠结果，要不要我扩大到全网？"
        if decision is not None and decision.access_mode == "visual_read":
            if execution.failure_reason == "no_active_page_context":
                return "我还没有拿到当前网页上下文，先打开一个网页后我才能读取顶部公告。"
            if execution.failure_reason == "screenshot_failed":
                return "当前网页已经打开，但截图没有成功，所以还没法读取指定区域。"
            if execution.failure_reason == "target_region_not_detected":
                return "我已经拿到页面截图，但还没稳定定位到你要看的区域。"
            return "这个页面需要结合页面区域来阅读，但当前还没拿到可靠的视觉内容。"
        if execution.fallback_stage:
            return "我已经按照备选网页访问路径进行了降级，但暂时仍没有找到可靠结果。"
        return "已尝试多种网页访问方式，但暂未找到可靠结果。"

    def _source_name_from_url_clean(self, url: str) -> str:
        text = str(url or "").strip().lower()
        if "ithome.com" in text:
            return "IT之家"
        if "openai.com" in text:
            return "OpenAI"
        if "microsoft.com" in text:
            return "Microsoft"
        if "apple.com" in text:
            return "Apple"
        return text.split("/")[2] if text.startswith(("http://", "https://")) else ""

    @staticmethod
    def _visual_region_label_clean(region: str) -> str:
        normalized = str(region or "").strip().lower()
        if normalized == "top_banner":
            return "页面顶部公告"
        if normalized == "hero_section":
            return "首屏主区域"
        if normalized == "results_panel":
            return "结果区域"
        return "页面视觉阅读"

    def _direct_location_execute(
        self,
        *,
        message: str,
        session_id: str,
        request_id: str,
        attachments: list[str],
        request_origin: str,
        route_hints: dict[str, Any],
    ) -> dict[str, Any]:
        _ = attachments
        return self._structured_location_execute(
            message=message,
            session_id=session_id,
            request_id=request_id,
            request_origin=request_origin,
            route_hints=route_hints,
        )

    def _stream_direct_location_execute(
        self,
        *,
        message: str,
        session_id: str,
        request_id: str,
        attachments: list[str],
        request_origin: str,
        route_hints: dict[str, Any],
        cancel_event: threading.Event | None = None,
    ) -> Iterable[dict[str, Any]]:
        _ = attachments
        if cancel_event is not None and cancel_event.is_set():
            yield {"kind": "error", "message": "cancelled"}
            return
        yield {
            "kind": "progress",
            "event_name": "structured_tool_progress",
            "payload": {"phase": "query_analyzed", "subtype": "location_lookup", "query": message[:60]},
        }
        if cancel_event is not None and cancel_event.is_set():
            yield {"kind": "error", "message": "cancelled"}
            return
        yield {
            "kind": "progress",
            "event_name": "structured_tool_progress",
            "payload": {"phase": "search_plan_created", "subtype": "location_lookup", "search_depth": 1},
        }
        result_payload = self._structured_location_execute(
            message=message,
            session_id=session_id,
            request_id=request_id,
            request_origin=request_origin,
            route_hints=route_hints,
        )
        if cancel_event is not None and cancel_event.is_set():
            yield {"kind": "error", "message": "cancelled"}
            return
        yield {
            "kind": "progress",
            "event_name": "structured_tool_progress",
            "payload": {
                "phase": "result_ready",
                "subtype": "location_lookup",
                "success": bool(result_payload.get("success", False)),
            },
        }
        yield {"kind": "result", "payload": result_payload}

    def _bundle_execute(
        self,
        *,
        message: str,
        session_id: str,
        request_id: str,
        attachments: list[str],
        request_origin: str,
        route_hints: dict[str, Any],
    ) -> dict[str, Any]:
        route_context = self._build_route_context(session_id, route_hints)
        core = FairyCore(self.llm, web_runtime=self._web_runtime)
        result = core.handle_request(
            message,
            attachment_paths=attachments,
            route_context=route_context,
            request_origin=request_origin,
            request_id=request_id,
        )
        return self._normalize_skill_result(
            result,
            message=message,
            session_id=session_id,
            request_id=request_id,
            request_origin=request_origin,
        )

    def _streaming_bundle_execute(
        self,
        *,
        message: str,
        session_id: str,
        request_id: str,
        attachments: list[str],
        request_origin: str,
        route_hints: dict[str, Any],
        cancel_event: threading.Event | None = None,
    ) -> Iterable[dict[str, Any]]:
        event_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        done_sentinel = {"kind": "done"}

        def emit_progress(event_name: str, payload: dict[str, Any]) -> None:
            if cancel_event is not None and cancel_event.is_set():
                return
            event_queue.put({"kind": "progress", "event_name": event_name, "payload": dict(payload or {})})

        def emit_chunk(token: str) -> None:
            if token and not (cancel_event is not None and cancel_event.is_set()):
                event_queue.put({"kind": "text_delta", "text": token})

        def worker() -> None:
            try:
                route_context = self._build_route_context(session_id, route_hints)
                core = FairyCore(
                    self.llm,
                    event_callback=emit_progress,
                    response_chunk_callback=emit_chunk,
                    web_runtime=self._web_runtime,
                )
                result = core.handle_request(
                    message,
                    attachment_paths=attachments,
                    route_context=route_context,
                    request_origin=request_origin,
                    request_id=request_id,
                )
                event_queue.put(
                    {
                        "kind": "result",
                        "payload": self._normalize_skill_result(
                            result,
                            message=message,
                            session_id=session_id,
                            request_id=request_id,
                            request_origin=request_origin,
                        ),
                    }
                )
            except Exception as exc:
                logger.exception("api_streaming_bundle_execute_failed request_id=%s", request_id)
                event_queue.put({"kind": "error", "message": str(exc)})
            finally:
                event_queue.put(done_sentinel)

        thread = threading.Thread(target=worker, name=f"fairy-stream-{request_id[:8]}", daemon=True)
        thread.start()

        while True:
            try:
                item = event_queue.get(timeout=0.1)
            except queue.Empty:
                if cancel_event is not None and cancel_event.is_set():
                    break
                continue
            if item is done_sentinel:
                break
            if cancel_event is not None and cancel_event.is_set():
                break
            yield item

    def _build_route_context(self, session_id: str, route_hints: dict[str, Any]) -> RouteContext:
        previous_structured = self._get_previous_structured(session_id)
        previous_skill = str(
            previous_structured.get("previous_skill")
            or previous_structured.get("bundle_name")
            or previous_structured.get("skill_name")
            or ""
        ).strip()
        screen_followup_remaining = 0
        if previous_skill == "screen-understanding":
            screen_followup_remaining = int(previous_structured.get("screen_followup_remaining") or 3)
        return RouteContext(
            previous_skill=previous_skill,
            previous_structured=previous_structured,
            screen_followup_remaining=screen_followup_remaining,
            session_id=session_id,
            perception_intent=str(route_hints.get("perception_intent") or ""),
            forced_bundle=str(route_hints.get("forced_bundle") or "").strip(),
            web_task_type=str(route_hints.get("web_task_type") or "").strip(),
            web_intent_plan=dict(route_hints.get("web_intent_plan") or {}),
            web_context=dict(previous_structured.get("web_context") or {}),
            preferred_routes=list(route_hints.get("preferred_routes") or []),
            preferred_modalities=list(route_hints.get("preferred_modalities") or []),
            active_focus=dict(route_hints.get("active_focus") or {}),
        )

    def _normalize_skill_result(
        self,
        result: SkillResult,
        *,
        message: str,
        session_id: str,
        request_id: str,
        request_origin: str,
    ) -> dict[str, Any]:
        response_text = (result.response_text or result.summary or "").strip()
        structured = dict(result.structured or {})
        if result.skill_name and not structured.get("bundle_name"):
            structured["bundle_name"] = result.skill_name
        return {
            "user_text": message,
            "assistant_text": response_text,
            "assistant_html": "",
            "skill_name": result.skill_name,
            "success": result.success,
            "summary": result.summary,
            "sources": result.sources,
            "warnings": result.warnings,
            "structured": structured,
            "changed_files": result.changed_files,
            "commands_run": result.commands_run,
            "validations": result.validations,
            "cancelled": False,
            "request_origin": request_origin,
            "request_id": request_id,
            "session_id": session_id,
        }

    @staticmethod
    def _runtime_text_system_prompt(intent: str) -> str:
        normalized = str(intent or "generic_search").strip().lower()
        if normalized == "explanation":
            return (
                "You are Fairy, a resident desktop system AI. "
                "Use a cool, precise, task-first voice with slight dry humor. "
                "Do not call yourself a technical partner, do not quote or impersonate any source character, "
                "and do not open with a long self-introduction. "
                "Answer explanatory questions clearly and directly. "
                "Prefer a structured explanation with short sections, but do not invent cards or UI metadata. "
                "Keep the answer practical and readable."
            )
        return (
            "You are Fairy, a resident desktop system AI. "
            "Use a cool, precise, task-first voice with slight dry humor. "
            "Do not call yourself a technical partner, do not quote or impersonate any source character, "
            "and do not open with a long self-introduction. "
            "Answer the user's question directly when it does not require a structured tool path. "
            "Stay concise, grounded, and practical. "
            "Do not invent UI structures, cards, or metadata."
        )

    def _structured_location_execute(
        self,
        *,
        message: str,
        session_id: str,
        request_id: str,
        request_origin: str,
        route_hints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        previous_structured = self._get_previous_structured(session_id)
        runtime_context = {"previous_structured": previous_structured}
        route_data = dict(route_hints or {})
        resolved_slots = dict(route_data.get("resolved_slots") or {})
        resolved_location = self._clean_location_label(resolved_slots.get("location"))
        context_location = self._structured_location(previous_structured)
        display_map = str(route_data.get("perception_intent") or "").strip().lower() == "display_information" or self._looks_like_map_display_request(
            str(route_data.get("raw_user_text") or message)
        )
        direct_result = self._execute_location_intent_without_browser(
            message=message,
            session_id=session_id,
            request_id=request_id,
            request_origin=request_origin,
            route_hints=route_hints,
            runtime_context=runtime_context,
        )
        if direct_result is not None:
            return direct_result
        should_skip_browser_fallback = bool(display_map or resolved_location or context_location)
        if should_skip_browser_fallback:
            failure = SkillResult(
                skill_name=self._location_skill.SPEC.name,
                success=False,
                summary="Unable to build a map result without browser fallback.",
                structured={"intent": {"intent_type": "location_lookup"}},
                recommendation="Please tell me which place to map, or continue with a clearer location.",
                sources=[],
                warnings=["location_lookup_unresolved_without_browser"],
                response_text="Request received, but I could not build a map result without the browser path. Please tell me which place to map.",
            )
            payload = self._normalize_skill_result(
                failure,
                message=message,
                session_id=session_id,
                request_id=request_id,
                request_origin=request_origin,
            )
            payload["_runtime_query_debug"] = self._build_query_debug_snapshot(
                raw_query=str(route_data.get("raw_user_text") or message).strip() or message,
                resolved_query=str(route_data.get("resolved_query") or route_data.get("raw_user_text") or message).strip() or message,
                effective_query=str(route_data.get("raw_user_text") or message).strip() or message,
                selected_capability="location_lookup",
                query_authority="raw_query",
                query_mutation_reason="map_phrase_bypasses_strict_slot_parse" if display_map else "no_mutation_raw_query_retained",
                topic_carryover_applied=False,
                topic_carryover_reason="map_query_blocks_topic_inheritance",
                source_constraint_applied=False,
                memory_context_applied=False,
                memory_usage_type="none",
                db_context_applied=False,
                page_context_available=False,
            )
            return payload
        result = self._location_skill.execute(
            message,
            ["search_web", "fetch_page"],
            runtime_context=runtime_context,
        )
        return self._normalize_skill_result(
            result,
            message=message,
            session_id=session_id,
            request_id=request_id,
            request_origin=request_origin,
        )

    def _execute_location_intent_without_browser(
        self,
        *,
        message: str,
        session_id: str,
        request_id: str,
        request_origin: str,
        route_hints: dict[str, Any] | None,
        runtime_context: dict[str, Any],
    ) -> dict[str, Any] | None:
        previous_structured = runtime_context.get("previous_structured") if isinstance(runtime_context, dict) else {}
        route_hints = dict(route_hints or {})
        raw_query = str(route_hints.get("raw_user_text") or message).strip() or message
        resolved_query = str(route_hints.get("resolved_query") or raw_query).strip() or raw_query
        resolved_slots = dict(route_hints.get("resolved_slots") or {})
        resolved_location = self._clean_location_label(resolved_slots.get("location"))
        direct_location = self._extract_direct_location_target(raw_query) or self._extract_direct_location_target(resolved_query)
        perception_intent = str(route_hints.get("perception_intent") or "").strip().lower()
        display_map = perception_intent == "display_information" or self._looks_like_map_display_request(raw_query)
        has_explicit_location = bool(resolved_location or direct_location)
        context_location = ""
        if not has_explicit_location:
            context_location = self._structured_location(previous_structured if isinstance(previous_structured, dict) else {})
        nearby = False
        effective_location = resolved_location or direct_location or context_location
        query_mutation_reason = "map_phrase_bypasses_strict_slot_parse" if direct_location else "no_mutation_raw_query_retained"
        if not effective_location:
            try:
                classified = self._location_skill._classify_intent(raw_query, runtime_context=runtime_context)
                effective_location = self._clean_location_label(
                    classified.constraints.get("location_query") or classified.target_entity or ""
                )
                nearby = bool(classified.constraints.get("nearby"))
            except Exception:
                logger.debug("structured_location_intent_classification_failed", exc_info=True)

        query_debug = self._build_query_debug_snapshot(
            raw_query=raw_query,
            resolved_query=resolved_query,
            effective_query=raw_query,
            selected_capability="location_lookup",
            query_authority="raw_query",
            query_mutation_reason=query_mutation_reason,
            topic_carryover_applied=False,
            topic_carryover_reason="map_query_blocks_topic_inheritance",
            source_constraint_applied=False,
            memory_context_applied=False,
            memory_usage_type="none",
            db_context_applied=False,
            page_context_available=False,
        )

        if not effective_location:
            return None

        intent = WebResearchIntent(
            intent_type="location_lookup",
            normalized_query=message,
            target_entity=effective_location,
            constraints={
                "location_query": effective_location,
                "nearby": nearby,
                "display_map_from_context": display_map and not has_explicit_location,
            },
            allow_distance_fallback=False,
        )
        location_result = None
        if not has_explicit_location:
            location_result = self._location_skill._location_result_from_context(intent, runtime_context)
        if location_result is None and effective_location:
            location_result = self._location_skill._try_location_lookup(intent)
        if location_result is None:
            if self._location_skill.browser is None:
                failure = SkillResult(
                    skill_name=self._location_skill.SPEC.name,
                    success=False,
                    summary="未能解析要显示的地点地图。",
                    structured={"intent": {"intent_type": "location_lookup"}},
                    recommendation="请直接告诉我要查看哪个地点，例如“显示成都地图”。",
                    sources=[],
                    warnings=["location_lookup_unresolved_without_browser"],
                    response_text="请求已接收，但我还没能解析出要显示的地点。请直接告诉我要查看哪个地点，例如“显示成都地图”。",
                )
                payload = self._normalize_skill_result(
                    failure,
                    message=message,
                    session_id=session_id,
                    request_id=request_id,
                    request_origin=request_origin,
                )
                payload["_runtime_query_debug"] = query_debug
                return payload
            return None

        result = SkillResult(
            skill_name=self._location_skill.SPEC.name,
            success=True,
            summary=str(location_result.get("summary") or "").strip(),
            structured={
                "intent": {"intent_type": "location_lookup"},
                **location_result,
            },
            recommendation=str(location_result.get("recommendation") or "").strip(),
            sources=list(location_result.get("sources") or []),
            response_text=str(location_result.get("response_text") or "").strip(),
        )
        payload = self._normalize_skill_result(
            result,
            message=message,
            session_id=session_id,
            request_id=request_id,
            request_origin=request_origin,
        )
        payload["_runtime_query_debug"] = query_debug
        return payload

    def _get_previous_structured(self, session_id: str) -> dict[str, Any]:
        with self._session_lock:
            return dict(self._session_structured.get(session_id, {}))

    def _remember_session_contract(self, session_id: str, contract: dict[str, Any]) -> None:
        structured = self._contract_to_previous_structured(contract)
        if not structured:
            return
        with self._session_lock:
            self._session_structured[session_id] = structured

    def _contract_to_previous_structured(self, contract: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(contract, dict):
            return {}
        if contract.get("errors") and not contract.get("cards"):
            return {}
        structured = dict(contract.get("structured") or {})
        meta = dict(contract.get("meta") or {})
        bundle_name = str(meta.get("bundle_name") or structured.get("bundle_name") or contract.get("skill_name") or "").strip()
        intent = str(meta.get("intent") or "").strip().lower()
        cards = list(contract.get("cards") or [])
        first_card = cards[0] if cards and isinstance(cards[0], dict) else {}
        card_type = str(first_card.get("type") or intent or "").strip().lower()
        card_data = dict(first_card.get("data") or {})
        intent_type = self._normalized_intent_key(card_type or intent)
        merged: dict[str, Any] = dict(structured)
        if card_type:
            merged["type"] = card_type
        if intent_type:
            merged["intent"] = {"intent_type": intent_type}
            merged["routing"] = {"primary_intent": intent_type}
        if bundle_name:
            merged["bundle_name"] = bundle_name
            merged["skill_name"] = bundle_name
            merged["previous_skill"] = bundle_name
            if bundle_name == "screen-understanding":
                merged["screen_followup_remaining"] = 3
        if card_type and card_data:
            merged[card_type] = dict(card_data)
            for key, value in card_data.items():
                merged.setdefault(key, value)
        return merged

    @staticmethod
    def _normalized_intent_key(value: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized == "weather":
            return "weather_lookup"
        if normalized in {"location", "map_preview"}:
            return "location_lookup"
        if normalized == "news_list":
            return "news_lookup"
        return normalized

    def _rewrite_message(self, message: str, previous_structured: dict[str, Any]) -> str:
        raw = str(message or "").strip()
        if not raw:
            return raw
        previous_intent = self._normalized_intent_key(
            str((previous_structured.get("intent") or {}).get("intent_type") or previous_structured.get("type") or "")
        )
        subject = self._extract_short_followup_subject(raw)
        location = self._structured_location(previous_structured)
        weather_location = self._structured_weather_location(previous_structured) or location

        if self._looks_like_map_display_request(raw) and location:
            return f"{location}\u5728\u54ea\u91cc"

        if previous_intent == "weather_lookup" and subject:
            return f"weather in {subject}"

        if weather_location and self._looks_like_weather_followup(raw, previous_intent):
            return f"weather in {weather_location}"

        return raw

    @staticmethod
    def _structured_location(previous_structured: dict[str, Any]) -> str:
        for key in ("place_name", "title", "city", "weather_location", "address", "location"):
            value = FairyRuntimeService._clean_location_label(previous_structured.get(key))
            if value:
                return value
        nested_location = previous_structured.get("location") if isinstance(previous_structured.get("location"), dict) else {}
        for key in ("place_name", "title", "city", "address", "location"):
            value = FairyRuntimeService._clean_location_label(nested_location.get(key))
            if value:
                return value
        return ""

    @staticmethod
    def _structured_weather_location(previous_structured: dict[str, Any]) -> str:
        nested_weather = previous_structured.get("weather") if isinstance(previous_structured.get("weather"), dict) else {}
        for key in ("city", "weather_location", "title", "address"):
            value = FairyRuntimeService._clean_location_label(nested_weather.get(key))
            if value:
                return value
        return FairyRuntimeService._structured_location(previous_structured)

    @staticmethod
    def _clean_location_label(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        text = re.split(r"[\uFF0C,\uFF08(]", text, maxsplit=1)[0].strip()
        text = re.sub(r"\s+", " ", text)
        if len(text) > 2 and text.endswith("\u5E02"):
            text = text[:-1].strip()
        text = re.sub(r"\bcity\b", "", text, flags=re.IGNORECASE).strip()
        return text

    @staticmethod
    def _looks_like_map_display_request(text: str) -> bool:
        lowered = str(text or "").strip().lower()
        return any(term in lowered for term in _MAP_DISPLAY_TERMS) or lowered in {"\u5730\u56FE", "map"}

    @staticmethod
    def _extract_direct_location_target(text: str) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        for pattern in _MAP_DIRECT_PATTERNS:
            match = pattern.fullmatch(cleaned)
            if not match:
                continue
            location = FairyRuntimeService._clean_location_label(match.group("location"))
            if location:
                return location
        return ""

    @staticmethod
    def _looks_like_weather_followup(text: str, previous_intent: str) -> bool:
        lowered = str(text or "").strip().lower()
        if any(term in lowered for term in _WEATHER_TERMS):
            return True
        if lowered in {"\u90A3\u5929\u6C14\u5462", "\u5929\u6C14\u5462", "weather there", "how about the weather"}:
            return True
        return previous_intent in {"weather_lookup", "location_lookup"} and not FairyRuntimeService._looks_like_map_display_request(text)

    @staticmethod
    def _extract_short_followup_subject(text: str) -> str:
        cleaned = str(text or "").strip()
        if not cleaned or len(cleaned) > 30:
            return ""
        match = _SHORT_FOLLOWUP_RE.fullmatch(cleaned)
        if not match:
            return ""
        subject = str(match.group("subject") or "").strip()
        if subject.lower() in {"weather", "map"} or subject in {"天气", "地图", "这里", "那里", "这个地方", "那个地方"}:
            return ""
        return subject


_runtime_service_lock = threading.Lock()
_runtime_service: FairyRuntimeService | None = None


def initialize_runtime_service() -> FairyRuntimeService:
    global _runtime_service
    with _runtime_service_lock:
        if _runtime_service is None:
            _runtime_service = FairyRuntimeService()
            logger.info("api_runtime_service_initialized")
            try:
                from app.companion import get_passive_screen_watcher

                get_passive_screen_watcher().start()
                logger.info("companion_passive_screen_watcher_started")
            except Exception:
                logger.exception("companion_passive_screen_watcher_start_failed")
        return _runtime_service


def shutdown_runtime_service() -> None:
    global _runtime_service
    with _runtime_service_lock:
        service = _runtime_service
        _runtime_service = None
    try:
        from app.companion import get_passive_screen_watcher

        get_passive_screen_watcher().stop()
    except Exception:
        logger.exception("companion_passive_screen_watcher_stop_failed")
    if service is not None:
        service.shutdown()
        logger.info("api_runtime_service_shutdown")


def get_runtime_service() -> FairyRuntimeService:
    return initialize_runtime_service()
