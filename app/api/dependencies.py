from __future__ import annotations

import logging
import queue
import re
import threading
from pathlib import Path
from typing import Any, Iterable

from app.ai.llm_client import LLMClient
from app.app_preferences import load_app_preferences
from app.fairy_core import FairyCore
from app.models.skill_result import SkillResult
from app.response import ResponsePipeline
from app.runtime import FairyRuntimeV2
from app.skill_router import RouteContext
from app.skills.web_research_skill import WebResearchSkill
from app.services.assets import get_asset_resolver
from skills.crawl_webpage import crawl_webpage
from skills.search_web import search_web_detailed


logger = logging.getLogger(__name__)

SERVICE_NAME = "fairy-runtime-api"
DEFAULT_SESSION_ID = "default"
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


def build_error_contract(
    *,
    request_id: str = "",
    session_id: str = DEFAULT_SESSION_ID,
    code: str = "runtime_error",
    message: str = "Unknown runtime error.",
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "session_id": session_id,
        "text": "",
        "cards": [],
        "meta": dict(meta or {}),
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
            legacy_executor=self._legacy_execute,
            streaming_legacy_executor=self._streaming_legacy_execute,
            capability_executors={
                "location_lookup": self._direct_location_execute,
                "generic_search": self._direct_text_execute,
                "explanation": self._direct_text_execute,
            },
            streaming_capability_executors={
                "location_lookup": self._stream_direct_location_execute,
                "generic_search": self._stream_direct_text_execute,
                "explanation": self._stream_direct_text_execute,
            },
        )
        self._session_structured: dict[str, dict[str, Any]] = {}
        self._session_lock = threading.Lock()
        self._location_skill = WebResearchSkill(browser=None, llm_helper=self.llm)
        self.runtime.system_bridge.set_backend_status("ready", message="Runtime API service initialized.")

    def invoke(self, *, message: str, session_id: str, attachments: list[str] | None = None) -> dict[str, Any]:
        effective_session = session_id or DEFAULT_SESSION_ID
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
        previous_structured = self._get_previous_structured(effective_session)
        for event in self.runtime.stream_invoke(
            message=message,
            session_id=effective_session,
            attachments=list(attachments or []),
            request_origin="api_stream",
            streaming_legacy_executor=self._streaming_legacy_execute,
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

    def system_state(self) -> dict[str, Any]:
        return self.runtime.get_system_state()

    def system_events(self, *, limit: int = 25) -> list[dict[str, Any]]:
        return self.runtime.get_system_events(limit=limit)

    def perform_system_action(self, action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.runtime.execute_system_action(action, payload)

    def shutdown(self) -> None:
        self.runtime.system_bridge.set_backend_status("stopped", message="Runtime API service shutdown.")
        try:
            self.llm.shutdown()
        except Exception:
            logger.debug("runtime_service shutdown failed", exc_info=True)

    def _search_web(
        self,
        query: str,
        *,
        max_results: int = 5,
        preferred_domains: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        payload = search_web_detailed(
            query,
            max_results=max_results,
            timeout_sec=8,
            preferred_domains=preferred_domains or [],
        )
        results = payload.get("results") if isinstance(payload, dict) else []
        return list(results or [])

    def _fetch_page(self, url: str) -> str:
        page = crawl_webpage(url, timeout_sec=8)
        return page.html

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
        _ = attachments, route_hints
        return self._structured_location_execute(
            message=message,
            session_id=session_id,
            request_id=request_id,
            request_origin=request_origin,
        )

    def _direct_text_execute(
        self,
        *,
        message: str,
        session_id: str,
        request_id: str,
        attachments: list[str],
        request_origin: str,
        route_hints: dict[str, Any],
    ) -> dict[str, Any]:
        _ = session_id, request_origin
        intent = str(route_hints.get("perception_intent") or "generic_search").strip().lower()
        response = self.llm.execute_task(
            self._runtime_text_system_prompt(intent),
            message,
            attachment_paths=attachments,
            temperature=0.2 if intent == "explanation" else 0.1,
            instruction_label="Runtime direct execution",
        )
        answer = str(response.text or "").strip()
        return {
            "assistant_text": answer,
            "assistant_html": "",
            "summary": answer,
            "sources": [],
            "warnings": [],
            "structured": {},
            "skill_name": intent,
            "success": bool(answer),
            "changed_files": [],
            "commands_run": [],
            "validations": [],
            "cancelled": False,
            "request_origin": request_origin,
            "request_id": request_id,
            "session_id": session_id,
        }

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
        _ = attachments, route_hints
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

    def _stream_direct_text_execute(
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
        _ = session_id, request_id, request_origin
        intent = str(route_hints.get("perception_intent") or "generic_search").strip().lower()
        yield {
            "kind": "progress",
            "event_name": "structured_tool_progress",
            "payload": {"phase": "planning", "subtype": intent, "query": message[:80]},
        }
        if cancel_event is not None and cancel_event.is_set():
            yield {"kind": "error", "message": "cancelled"}
            return
        chunks: list[str] = []
        try:
            for token in self.llm.stream_task(
                self._runtime_text_system_prompt(intent),
                message,
                attachment_paths=attachments,
                temperature=0.2 if intent == "explanation" else 0.1,
                instruction_label="Runtime direct execution",
            ):
                if cancel_event is not None and cancel_event.is_set():
                    yield {"kind": "error", "message": "cancelled"}
                    return
                if not token:
                    continue
                chunks.append(token)
                yield {"kind": "text_delta", "text": token}
        except Exception as exc:  # noqa: BLE001
            yield {"kind": "error", "message": str(exc)}
            return
        answer = "".join(chunks).strip()
        yield {
            "kind": "result",
            "payload": {
                "assistant_text": answer,
                "assistant_html": "",
                "summary": answer,
                "sources": [],
                "warnings": [],
                "structured": {},
                "skill_name": intent,
                "success": bool(answer),
                "changed_files": [],
                "commands_run": [],
                "validations": [],
                "cancelled": False,
                "request_origin": request_origin,
                "request_id": request_id,
                "session_id": session_id,
            },
        }

    def _legacy_execute(
        self,
        *,
        message: str,
        session_id: str,
        request_id: str,
        attachments: list[str],
        request_origin: str,
        route_hints: dict[str, Any],
    ) -> dict[str, Any]:
        perception_intent = str(route_hints.get("perception_intent") or "").strip().lower()
        route_context = self._build_route_context(session_id, route_hints)
        core = FairyCore(self.llm)
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

    def _streaming_legacy_execute(
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
                perception_intent = str(route_hints.get("perception_intent") or "").strip().lower()
                route_context = self._build_route_context(session_id, route_hints)
                core = FairyCore(self.llm, event_callback=emit_progress, response_chunk_callback=emit_chunk)
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
                logger.exception("api_streaming_legacy_execute_failed request_id=%s", request_id)
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
        return RouteContext(
            session_id=session_id,
            perception_intent=str(route_hints.get("perception_intent") or ""),
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
        return {
            "user_text": message,
            "assistant_text": response_text,
            "assistant_html": "",
            "skill_name": result.skill_name,
            "success": result.success,
            "summary": result.summary,
            "sources": result.sources,
            "warnings": result.warnings,
            "structured": result.structured,
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
                "You are Fairy, a desktop AI partner. "
                "Answer explanatory questions clearly and directly. "
                "Prefer a structured explanation with short sections, but do not invent cards or UI metadata. "
                "Keep the answer practical and readable."
            )
        return (
            "You are Fairy, a desktop AI partner. "
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
    ) -> dict[str, Any]:
        runtime_context = {"previous_structured": self._get_previous_structured(session_id)}
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
        meta = dict(contract.get("meta") or {})
        intent = str(meta.get("intent") or "").strip().lower()
        cards = list(contract.get("cards") or [])
        first_card = cards[0] if cards and isinstance(cards[0], dict) else {}
        card_type = str(first_card.get("type") or intent or "").strip().lower()
        card_data = dict(first_card.get("data") or {})
        intent_type = self._normalized_intent_key(card_type or intent)
        structured: dict[str, Any] = {}
        if card_type:
            structured["type"] = card_type
        if intent_type:
            structured["intent"] = {"intent_type": intent_type}
            structured["routing"] = {"primary_intent": intent_type}
        if card_type and card_data:
            structured[card_type] = dict(card_data)
            for key, value in card_data.items():
                structured.setdefault(key, value)
        return structured

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
        return _runtime_service


def shutdown_runtime_service() -> None:
    global _runtime_service
    with _runtime_service_lock:
        service = _runtime_service
        _runtime_service = None
    if service is not None:
        service.shutdown()
        logger.info("api_runtime_service_shutdown")


def get_runtime_service() -> FairyRuntimeService:
    return initialize_runtime_service()
