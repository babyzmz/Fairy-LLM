from __future__ import annotations

# LEGACY_ENTRYPOINT
# This Qt controller remains only as a compatibility shell/consumer while the desktop app owns the main UX.
# The default desktop startup path is the Tauri shell.

import html
import json
import logging
import uuid
from typing import Any, Callable, Dict, List

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal

from app.app_preferences import apply_app_preferences, load_app_preferences, save_app_preferences
from app.ai.fairy_init import FairyInitializer
from app.ai.llm_client import LLMClient, Message
from app.ai.llm_client_file_processor import IMAGE_SUFFIXES
from app.ai.voice import FairyVoice
from app.config import system_config, voice_config
from app.fairy_core import FairyCore
from app.models.action_event import ActionEvent, build_action_event
from app.response import ResponsePipeline
from app.runtime import FairyRuntimeV2
from app.rag import get_rag_manager, get_reindex_manager, save_rag_settings
from app.route_context import RouteContext
from app.settings import SecretStore, save_game_mode_settings
from app.storage.repositories import MessageRepo, SessionRepo
from app.system_notifications import NotificationEngine, NotificationScheduler
from app.system_notifications.notification_presenter import is_active_notification, notification_counts
from app.ui.app_mode_dialog import choose_app_settings
from app.ui.components.chat import ChatMessage, build_chat_messages_from_response
from app.ui.components.fairy_presence_window import FairyPresenceWindow
from app.ui.desktop_pet import DesktopPetWindow, FairyAvatar
from app.ui.i18n import tr


logger = logging.getLogger(__name__)


class ChatWorker(QObject):
    finished = Signal(object)
    progress = Signal(object)

    def __init__(
        self,
        llm: LLMClient,
        runtime: FairyRuntimeV2,
        voice: FairyVoice | None,
        user_text: str,
        attachment_paths: List[str],
        user_log_text: str,
        route_context: RouteContext,
        request_origin: str = "main_chat",
        request_id: str = "",
        allow_voice_streaming: bool = False,
    ) -> None:
        super().__init__()
        self.llm = llm
        self.runtime = runtime
        self.voice = voice
        self.user_text = user_text
        self.attachment_paths = attachment_paths
        self.user_log_text = user_log_text
        self.route_context = route_context
        self.request_origin = request_origin
        self.request_id = request_id or uuid.uuid4().hex
        self.allow_voice_streaming = bool(allow_voice_streaming)
        self.stream_message_id = uuid.uuid4().hex
        self._streamed_voice_tokens = False
        self._streamed_ui_tokens = False

    def _is_cancelling(self) -> bool:
        return QThread.currentThread().isInterruptionRequested()

    def _voice_can_stream(self) -> bool:
        return (
            self.voice is not None
            and voice_config.enabled
            and voice_config.speak_responses
            and voice_config.stream_responses
            and self.allow_voice_streaming
        )

    def _ui_can_stream(self) -> bool:
        return self.request_origin == "main_chat"

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
        _ = session_id, route_hints
        core = FairyCore(
            self.llm,
            event_callback=self._emit_core_event,
            response_chunk_callback=self._on_response_chunk if (self._ui_can_stream() or self._voice_can_stream()) else None,
        )
        result = core.handle_request(
            message,
            attachment_paths=attachments,
            route_context=self.route_context,
            request_origin=request_origin,
            request_id=request_id,
        )
        response_text = (result.response_text or result.summary or "").strip()
        return {
            "user_text": message,
            "assistant_text": response_text,
            "user_log_text": self.user_log_text,
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
        }

    def _on_response_chunk(self, token: str) -> None:
        if not token or self._is_cancelling():
            return
        if self._ui_can_stream():
            self._streamed_ui_tokens = True
            self.progress.emit(
                build_action_event(
                    "assistant_response_chunk",
                    {
                        "request_id": self.request_id,
                        "message_id": self.stream_message_id,
                        "token": token,
                    },
                )
            )
        if self._voice_can_stream() and self.voice is not None:
            self._streamed_voice_tokens = True
            self.voice.feed_token(token)

    def _emit_core_event(self, event: str, payload: dict) -> None:
        enriched_payload = dict(payload)
        enriched_payload.setdefault("request_id", self.request_id)
        enriched_payload.setdefault("request_origin", self.request_origin)
        action_event = build_action_event(event, enriched_payload)
        self.progress.emit(action_event)
        if self.voice is None:
            return

        tool_name = str(enriched_payload.get("tool_name", ""))
        if event == "tool_call_start" and tool_name == "search_web":
            self.voice.system_line("searching")
        elif event == "tool_call_start" and tool_name in {"open_url", "extract_page_text"}:
            self.voice.system_line("reading_webpage")
        elif event == "tool_call_done" and tool_name == "compare_structured_results":
            self.voice.system_line("information_found")

    def run(self) -> None:
        try:
            if self._is_cancelling():
                if self._voice_can_stream():
                    self.voice.cancel_stream()
                self.finished.emit(
                    {
                        "user_text": self.user_text,
                        "assistant_text": "",
                        "user_log_text": self.user_log_text,
                        "cancelled": True,
                        "request_origin": self.request_origin,
                        "request_id": self.request_id,
                        "stream_message_id": "",
                        "voice_streamed": False,
                    }
                )
                return

            if self._voice_can_stream():
                self.voice.start_stream()

            result_payload = self.runtime.invoke(
                self.user_text,
                session_id=self.route_context.session_id,
                attachments=self.attachment_paths,
                request_id=self.request_id,
                previous_structured=dict(self.route_context.previous_structured),
                bundle_executor=self._bundle_execute,
                request_origin=self.request_origin,
                # Qt shell is the only remaining compat consumer of deprecated bridge fields.
                include_compat_fields=True,
            )
            response_text = str(result_payload.get("assistant_text") or result_payload.get("text") or "").strip()

            if self._streamed_voice_tokens and self.voice is not None and not self._is_cancelling():
                self.voice.finish_stream()
            elif self._voice_can_stream():
                self.voice.cancel_stream()

            result_payload.update(
                {
                    "user_text": self.user_text,
                    "assistant_text": response_text,
                    "user_log_text": self.user_log_text,
                    "cancelled": False,
                    "request_origin": self.request_origin,
                    "request_id": self.request_id,
                    "stream_message_id": self.stream_message_id if self._streamed_ui_tokens else "",
                    "voice_streamed": self._streamed_voice_tokens,
                }
            )
            self.finished.emit(result_payload)
        except Exception as exc:  # noqa: BLE001
            if self._is_cancelling():
                if self._voice_can_stream():
                    self.voice.cancel_stream()
                self.finished.emit(
                    {
                        "user_text": self.user_text,
                        "assistant_text": "",
                        "user_log_text": self.user_log_text,
                        "cancelled": True,
                        "request_origin": self.request_origin,
                        "request_id": self.request_id,
                        "stream_message_id": "",
                        "voice_streamed": False,
                    }
                )
                return
            logger.exception("Assistant chat worker failed")
            if self.voice is not None:
                self.voice.interrupt()
            self.finished.emit(
                {
                    "user_text": self.user_text,
                    "assistant_text": f"Request failed: {exc}",
                    "user_log_text": self.user_log_text,
                    "cancelled": False,
                    "success": False,
                    "changed_files": [],
                    "commands_run": [],
                    "validations": [],
                    "request_origin": self.request_origin,
                    "request_id": self.request_id,
                    "stream_message_id": self.stream_message_id if self._streamed_ui_tokens else "",
                    "voice_streamed": False,
                }
            )


class AssistantModeController(QObject):
    voice_playback_changed = Signal(bool)
    voice_progress_changed = Signal(dict)

    def __init__(self) -> None:
        super().__init__()
        self.llm = LLMClient()
        self.mode_manager = self.llm.provider_router.mode_manager
        self.secret_store = SecretStore()
        self.session_repo = SessionRepo()
        self.message_repo = MessageRepo()
        self.rag_manager = get_rag_manager()
        self.reindex_manager = get_reindex_manager()
        self.notification_engine = NotificationEngine(rag_manager=self.rag_manager, reindex_manager=self.reindex_manager)
        self.notification_scheduler = NotificationScheduler(
            interval_seconds=self.rag_manager.load_settings().notification_scan_interval_seconds
        )
        self.preferences = load_app_preferences()
        apply_app_preferences(self.preferences)
        self.voice = FairyVoice(enabled=voice_config.enabled)
        self.initializer = FairyInitializer(self.voice)
        self._response_pipeline = ResponsePipeline(language=self.preferences.ui_language)
        self._runtime_v2 = FairyRuntimeV2(
            language=self.preferences.ui_language,
            response_pipeline=self._response_pipeline,
            semantic_arbitration_callback=self._semantic_llm_arbitrate,
            web_browse_decision_callback=self._semantic_web_browse_decide,
        )
        self.history: List[Message] = []
        self._threads: list[QThread] = []
        self._workers: Dict[QThread, ChatWorker] = {}
        self._shutting_down = False
        self._boot_started = False
        self._welcome_played = False
        self._startup_status_index = 0
        self._last_startup_status_spoken = ""
        self._voice_warmup_scheduled = False
        self._last_skill_name = ""
        self._last_structured: dict = {}
        self._screen_followup_remaining = 0
        self._last_pending_count = -1
        self._last_reindex_job_key = ""
        self._last_active_fingerprint = ""
        self._presence_processing = False
        self._presence_unread_reply = False
        self._presence_notification_items: list[dict[str, object]] = []
        self._presence_jobs: list[dict[str, object]] = []
        self._presence_latest_job: dict[str, object] | None = None
        self._streaming_message_text: dict[str, str] = {}
        self._stream_message_aliases: dict[str, str] = {}
        self._request_plans: dict[str, object] = {}
        self._progress_message_ids: dict[str, str] = {}
        self._progress_message_texts: dict[str, str] = {}
        self._presence_task_summary = tr("presence_tooltip_idle_summary", self.preferences.ui_language)
        self._current_task = ""
        self._system_state = "booting"
        self.on_mode_switch_requested: Callable[[str], None] | None = None
        self.on_quit_requested: Callable[[], None] | None = None

        self.window = DesktopPetWindow(avatar=FairyAvatar())
        self.presence_window = FairyPresenceWindow(
            avatar=FairyAvatar(),
            avatar_size=self.preferences.presence_avatar_size,
            quiet_mode=self.preferences.presence_quiet_mode,
        )
        self.window.set_ui_language(self.preferences.ui_language)
        self.presence_window.set_ui_language(self.preferences.ui_language)
        self.window.on_user_message = self.on_user_message
        self.window.on_close_request = self._handle_console_close_request
        self.window.on_open_settings = self._open_settings_dialog
        self.window.on_pending_decision_action = self._handle_pending_decision_action
        self.window.on_reindex_job_action = self._handle_reindex_job_action
        self.window.on_reindex_requested = self._handle_reindex_request
        self.window.on_notification_action = self._handle_notification_action
        self.presence_window.on_send = self.on_user_message
        self.presence_window.on_open_console = self.show_console
        self.presence_window.on_open_page = self.show_console_page
        self.presence_window.on_exit_requested = self.shutdown_and_quit
        self.presence_window.on_position_changed = self._handle_presence_position_changed
        self.presence_window.on_quiet_mode_changed = self._handle_presence_quiet_mode_changed
        self.presence_window.on_ignore_notifications = self._handle_presence_ignore_notifications
        self.voice.on_playback_state_change = self.voice_playback_changed.emit
        self.voice.on_playback_progress = self.voice_progress_changed.emit
        self.voice_playback_changed.connect(self.window.set_voice_active)
        self.voice_progress_changed.connect(self.window.set_voice_progress)
        self.window.set_system_state("booting", system_config.startup_status_lines[0])
        self.window.set_agent_route("", "")
        self.window.set_tool_status("", "")
        self.window.set_current_task("")
        snapshot = self.llm.get_runtime_snapshot()
        self._session_id = self.session_repo.create_session(
            title="Fairy Session",
            mode=str(snapshot.get("active_mode") or "normal_mode"),
            provider=str(snapshot.get("provider_id") or "local_server"),
            model=str(snapshot.get("model") or self.llm.config.model),
        )

        self._startup_timer = QTimer(self)
        self._startup_timer.setInterval(1500)
        self._startup_timer.timeout.connect(self._refresh_startup_state)
        self._mode_timer = QTimer(self)
        self._mode_timer.setInterval(2500)
        self._mode_timer.timeout.connect(self._refresh_game_mode_state)
        self._knowledge_timer = QTimer(self)
        self._knowledge_timer.setInterval(1800)
        self._knowledge_timer.timeout.connect(self._refresh_knowledge_surfaces)
        self._notification_timer = QTimer(self)
        self._notification_timer.setInterval(60000)
        self._notification_timer.timeout.connect(self._refresh_system_notifications)

        self._sync_runtime_snapshot()
        self.window.set_runtime_status(self.llm.get_runtime_status())
        self.presence_window.move_to_saved_or_default(self._presence_saved_point())
        self._refresh_game_mode_state()
        self._refresh_knowledge_surfaces()
        self._refresh_system_notifications(force=True)
        self._refresh_presence_surface()

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
            "- For short follow-ups, keep the prior intent only if the utterance mainly overrides a slot value.\n"
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
            logger.debug("assistant_mode_semantic_arbitration_failed", exc_info=True)
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
            logger.debug("assistant_mode_semantic_arbitration_parse_failed text=%r", text)
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
            logger.debug("assistant_mode_semantic_web_browse_decide_failed", exc_info=True)
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
            logger.debug("assistant_mode_semantic_web_browse_decide_parse_failed text=%r", text)
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

    def run(self) -> None:
        self.presence_window.show()
        self._mode_timer.start()
        self._knowledge_timer.start()
        self._notification_timer.start()
        QTimer.singleShot(240, self._run_boot_sequence)

    def _open_settings_dialog(self) -> None:
        settings = choose_app_settings(self.window, "assistant", load_app_preferences())
        if settings is None:
            return

        save_app_preferences(settings.preferences)
        apply_app_preferences(settings.preferences)
        self.preferences = settings.preferences
        self.window.set_ui_language(self.preferences.ui_language)
        self.presence_window.set_ui_language(self.preferences.ui_language)
        self._runtime_v2.set_language(self.preferences.ui_language)
        save_game_mode_settings(settings.game_mode_settings)
        if settings.rag_settings is not None:
            save_rag_settings(settings.rag_settings)
            self.notification_scheduler.update_interval(settings.rag_settings.notification_scan_interval_seconds)
        for secret_ref in settings.cleared_secret_refs:
            self.secret_store.clear(secret_ref)
        for secret_ref, secret_value in settings.secret_updates.items():
            if secret_value.strip():
                self.secret_store.set(secret_ref, secret_value.strip())
        self.llm.provider_router.reload_settings()
        self.voice.interrupt()
        self._refresh_game_mode_state()
        self._sync_runtime_snapshot()
        self._refresh_knowledge_surfaces()
        self._refresh_system_notifications(force=True)
        self.window.set_runtime_status(self.llm.get_runtime_status())
        self.presence_window.set_quiet_mode(self.preferences.presence_quiet_mode)
        self._refresh_presence_surface()

        if settings.mode != "assistant":
            callback = self.on_mode_switch_requested
            if callback is not None:
                callback(settings.mode)

    def close_for_mode_switch(self) -> None:
        self.window.on_close_request = None
        self.window.on_open_settings = None
        self.presence_window.prepare_for_shutdown()
        self.shutdown()
        self.window.close()
        self.presence_window.close()

    def _sync_runtime_snapshot(self) -> None:
        snapshot = self.llm.get_runtime_snapshot()
        boot_state = self.initializer.build_boot_result(snapshot)
        snapshot["network_online"] = boot_state["network_online"]
        self.window.set_runtime_snapshot(snapshot)
        self._refresh_presence_surface()

    def _presence_saved_point(self):
        if self.preferences.presence_position_x is None or self.preferences.presence_position_y is None:
            return None
        from PySide6.QtCore import QPoint

        return QPoint(self.preferences.presence_position_x, self.preferences.presence_position_y)

    def _handle_presence_position_changed(self, point) -> None:
        self.preferences.presence_position_x = int(point.x())
        self.preferences.presence_position_y = int(point.y())
        save_app_preferences(self.preferences)

    def _handle_presence_quiet_mode_changed(self, enabled: bool) -> None:
        self.preferences.presence_quiet_mode = bool(enabled)
        save_app_preferences(self.preferences)
        self._refresh_presence_surface()

    def _handle_console_close_request(self) -> bool:
        self.window.hide()
        self._presence_unread_reply = False
        self._refresh_presence_surface()
        return False

    def show_console(self) -> None:
        self.show_console_page("chat")

    def show_console_page(self, page: str) -> None:
        self._presence_unread_reply = False
        self.presence_window.hide_reply_bubble(manual=False)
        self.window.open_page(page)
        if self.window.isMinimized():
            self.window.showNormal()
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        self._refresh_presence_surface()

    def _refresh_game_mode_state(self) -> None:
        active_mode = self.mode_manager.refresh()
        game_settings = self.llm.provider_router.get_game_settings()
        if not game_settings.enabled:
            badge = "NORMAL"
        elif game_settings.auto_switch_when_game_detected:
            badge = "GAME AUTO" if active_mode == "game_mode" else "AUTO WAIT"
        else:
            badge = "GAME MODE" if active_mode == "game_mode" else "NORMAL"
        self.window.set_mode_badge(badge)
        self._sync_runtime_snapshot()
        self.window.set_runtime_status(self.llm.get_runtime_status())
        snapshot = self.llm.get_runtime_snapshot()
        self.session_repo.update_runtime(
            self._session_id,
            mode=str(snapshot.get("active_mode") or active_mode),
            provider=str(snapshot.get("provider_id") or "local_server"),
            model=str(snapshot.get("model") or self.llm.config.model),
        )
        self._refresh_presence_surface()

    def _refresh_knowledge_surfaces(self) -> None:
        try:
            pending_items = self.rag_manager.list_pending_decisions(limit=10)
            self.window.set_pending_decisions(pending_items)
            if len(pending_items) != self._last_pending_count:
                self._last_pending_count = len(pending_items)
                self.window.append_action_event(
                    build_action_event(
                        "pending_decision_surface_rendered",
                        {"reason": "refresh", "progress": f"{len(pending_items)} pending"},
                    )
                )
        except Exception:
            logger.exception("Failed to refresh pending decisions surface")

        rag_settings = self.rag_manager.load_settings()
        try:
            decision_cards = self.rag_manager.memory_repo.list_recent(memory_type="decision_card", limit=40)
            confirmed_decisions = [item for item in decision_cards if str(item.get("decision_status", "") or "confirmed") == "confirmed"][:20]
            rejected_decisions = [item for item in decision_cards if str(item.get("decision_status", "") or "") == "rejected"][:20]
            self.window.set_decision_library(confirmed=confirmed_decisions, rejected=rejected_decisions)

            session_summaries = self.rag_manager.memory_repo.list_recent(memory_type="session_summary", limit=12)
            recent_chunks = self.rag_manager.document_repo.list_chunks(limit=10)
            knowledge_items = (session_summaries[:8] + confirmed_decisions[:8] + recent_chunks[:8])[:20]
            self.window.set_knowledge_snapshot(
                {
                    "items": knowledge_items,
                    "total": len(knowledge_items),
                    "vector_backend": self.rag_manager.vector_backend_name,
                    "active_fingerprint": rag_settings.active_embedding_fingerprint.strip() or self.rag_manager.embedding_fingerprint,
                }
            )
            self.window.set_settings_summary(self._build_settings_summary(rag_settings))
        except Exception:
            logger.exception("Failed to refresh knowledge and settings surfaces")

        try:
            latest_job = self.reindex_manager.get_latest_job()
            jobs = [job.to_dict() for job in self.reindex_manager.list_jobs(limit=8)]
            promoted_jobs = [job.to_dict() for job in self.reindex_manager.list_promoted_jobs(limit=12)]
            latest_payload = latest_job.to_dict() if latest_job is not None else None
            active_fingerprint = rag_settings.active_embedding_fingerprint.strip() or self.rag_manager.embedding_fingerprint
            if isinstance(latest_payload, dict):
                latest_payload["is_active"] = active_fingerprint == str(latest_payload.get("target_fingerprint", "") or "")
                latest_payload["failed_chunk_category_counts"] = self.reindex_manager.get_failed_chunk_category_counts(str(latest_payload.get("job_id", "") or ""))
            for job_payload in jobs:
                job_payload["is_active"] = active_fingerprint == str(job_payload.get("target_fingerprint", "") or "")
                job_payload["failed_chunk_category_counts"] = self.reindex_manager.get_failed_chunk_category_counts(str(job_payload.get("job_id", "") or ""))
            for job_payload in promoted_jobs:
                job_payload["is_active"] = active_fingerprint == str(job_payload.get("target_fingerprint", "") or "")
                job_payload["failed_chunk_category_counts"] = self.reindex_manager.get_failed_chunk_category_counts(str(job_payload.get("job_id", "") or ""))
            self._presence_jobs = list(jobs)
            self._presence_latest_job = dict(latest_payload) if isinstance(latest_payload, dict) else None
            self.window.set_reindex_jobs(jobs)
            self.window.set_promoted_reindex_jobs(promoted_jobs)
            self.window.set_reindex_status(latest_payload)
            self._emit_reindex_progress_events(latest_payload)
        except Exception:
            logger.exception("Failed to refresh reindex status")
        self._refresh_system_notifications()
        self._refresh_presence_surface()

    def _build_settings_summary(self, rag_settings) -> dict[str, object]:
        snapshot = self.llm.get_runtime_snapshot()
        preferences = load_app_preferences()
        persona_mode = preferences.persona_mode if preferences.persona_enabled else "off"
        return {
            "mode": str(snapshot.get("active_mode") or "normal_mode"),
            "provider": str(snapshot.get("provider_id") or "local_server"),
            "model": str(snapshot.get("model") or self.llm.config.model),
            "vector_backend": self.rag_manager.vector_backend_name,
            "fingerprint": rag_settings.active_embedding_fingerprint.strip() or self.rag_manager.embedding_fingerprint,
            "rag_enabled": rag_settings.rag_enabled,
            "persona_mode": persona_mode,
        }

    def _refresh_system_notifications(self, force: bool = False) -> None:
        try:
            if not self.notification_scheduler.should_run(force=force):
                return
            self.notification_engine.scan_all()
            notifications = [item.to_dict() for item in self.notification_engine.list_recent(limit=80)]
            self.notification_scheduler.mark_run()
            self._presence_notification_items = notifications
            self.window.set_system_notifications(notifications)
            self._refresh_presence_surface()
        except Exception:
            logger.exception("Failed to refresh system notifications")

    def _refresh_presence_surface(self) -> None:
        stats = notification_counts(self._presence_notification_items)
        ignorable_ids = self._presence_ignorable_notification_ids()
        running_jobs = sum(
            1 for item in self._presence_jobs if str(item.get("status", "") or "") in {"queued", "running", "cancel_requested"}
        )
        quiet = self.preferences.presence_quiet_mode
        state = "idle"
        badge_count = 0
        title = tr("presence_tooltip_idle_title", self.preferences.ui_language)
        summary = self._presence_task_summary or tr("presence_tooltip_idle_summary", self.preferences.ui_language)

        if self._system_state == "sleeping":
            state = "sleep"
            title = tr("presence_tooltip_sleep_title", self.preferences.ui_language)
            summary = tr("presence_tooltip_sleep_summary", self.preferences.ui_language)
        elif self._presence_processing:
            state = "thinking"
            title = tr("presence_tooltip_thinking_title", self.preferences.ui_language)
            summary = self._current_task.strip() or tr("presence_tooltip_thinking_summary", self.preferences.ui_language)
        elif stats.get("critical", 0):
            state = "critical"
            badge_count = stats["critical"]
            title = tr("presence_tooltip_critical_title", self.preferences.ui_language)
            summary = self._first_notification_title(
                "critical",
                fallback=tr("presence_tooltip_critical_summary", self.preferences.ui_language),
            )
        elif not quiet and stats.get("action_required", 0):
            state = "action_required"
            badge_count = stats["action_required"]
            title = tr("presence_tooltip_action_title", self.preferences.ui_language)
            summary = self._first_notification_title(
                "action_required",
                fallback=tr("presence_tooltip_action_summary", self.preferences.ui_language),
            )
        elif running_jobs:
            state = "busy"
            title = tr("presence_tooltip_busy_title", self.preferences.ui_language)
            summary = tr("presence_tooltip_busy_summary", self.preferences.ui_language, count=running_jobs)
        elif not quiet and self._presence_unread_reply:
            state = "notify"
            badge_count = 1
            title = tr("presence_tooltip_reply_title", self.preferences.ui_language)
            summary = tr("presence_tooltip_reply_summary", self.preferences.ui_language)
        elif not quiet and stats.get("active", 0):
            state = "notify"
            badge_count = stats["active"]
            title = tr("presence_tooltip_notify_title", self.preferences.ui_language)
            summary = self._first_notification_title(
                "active",
                fallback=tr("presence_tooltip_notify_summary", self.preferences.ui_language),
            )

        if quiet and state in {"notify", "action_required"}:
            state = "idle"
            badge_count = 0
            title = tr("presence_tooltip_idle_title", self.preferences.ui_language)
            summary = tr("presence_quiet_summary", self.preferences.ui_language)

        self.presence_window.set_ignore_action(bool(ignorable_ids), count=len(ignorable_ids))
        self.presence_window.set_presence_state(
            state,
            badge_count=badge_count,
            title=title,
            summary=summary,
        )

    def _presence_ignorable_notification_ids(self) -> list[str]:
        notification_ids: list[str] = []
        for item in self._presence_notification_items:
            if not is_active_notification(item):
                continue
            level = str(item.get("level", "") or "")
            if level == "critical":
                continue
            notification_id = str(item.get("id", "") or "")
            if notification_id:
                notification_ids.append(notification_id)
        return notification_ids

    def _handle_presence_ignore_notifications(self) -> None:
        notification_ids = self._presence_ignorable_notification_ids()
        if not notification_ids:
            return
        try:
            self.notification_engine.dismiss_many(notification_ids)
            self._presence_task_summary = tr("presence_notifications_ignored", self.preferences.ui_language)
            self._refresh_system_notifications(force=True)
        except Exception:
            logger.exception("Failed to ignore presence notifications")

    def _first_notification_title(self, filter_name: str, *, fallback: str) -> str:
        for item in self._presence_notification_items:
            level = str(item.get("level", "") or "")
            if filter_name == "critical" and level == "critical":
                return str(item.get("title", "") or fallback)
            if filter_name == "action_required" and level == "action_required":
                return str(item.get("title", "") or fallback)
            if filter_name == "active" and not bool(item.get("is_completed")) and not bool(item.get("is_dismissed")):
                return str(item.get("title", "") or fallback)
        return fallback

    def _emit_reindex_progress_events(self, payload: dict | None) -> None:
        if not payload:
            return
        metadata = payload.get("metadata", {}) if isinstance(payload.get("metadata"), dict) else {}
        job_id = str(payload.get("job_id", "") or "")
        status = str(payload.get("status", "") or "")
        processed = int(payload.get("processed_chunks", 0) or 0)
        failed = int(payload.get("failed_chunks", 0) or 0)
        total = int(payload.get("total_chunks", 0) or 0)
        fingerprint = str(payload.get("target_fingerprint", "") or "")
        provider = str(payload.get("target_provider", "") or "")
        promotion = str(payload.get("promotion_status", "") or "")
        key = f"{job_id}:{status}:{promotion}:{processed}:{failed}"
        if key == self._last_reindex_job_key:
            return
        self._last_reindex_job_key = key
        progress = f"{processed}/{total}"
        if status == "queued":
            self.window.append_action_event(
                build_action_event("reindex_job_created", {"job_id": job_id, "target_fingerprint": fingerprint, "target_provider": provider, "collection_name": str(metadata.get("target_collection_name", "") or ""), "progress": progress})
            )
        elif status == "running":
            self.window.append_action_event(
                build_action_event("reindex_job_started", {"job_id": job_id, "target_fingerprint": fingerprint, "target_provider": provider, "collection_name": str(metadata.get("target_collection_name", "") or ""), "progress": progress})
            )
            if processed > 0:
                self.window.append_action_event(
                    build_action_event("reindex_chunk_processed", {"job_id": job_id, "target_fingerprint": fingerprint, "target_provider": provider, "collection_name": str(metadata.get("target_collection_name", "") or ""), "progress": progress})
                )
            if failed > 0:
                self.window.append_action_event(
                    build_action_event("reindex_chunk_failed", {"job_id": job_id, "target_fingerprint": fingerprint, "target_provider": provider, "collection_name": str(metadata.get("target_collection_name", "") or ""), "progress": f"failed={failed}"})
                )
        elif status == "cancel_requested":
            self.window.append_action_event(
                build_action_event("reindex_job_cancel_requested", {"job_id": job_id, "target_fingerprint": fingerprint, "target_provider": provider, "collection_name": str(payload.get("output_collection_name", "") or ""), "reason": str(payload.get("cancel_reason", "") or "")})
            )
        elif status == "cancelled":
            self.window.append_action_event(
                build_action_event("reindex_job_cancelled", {"job_id": job_id, "target_fingerprint": fingerprint, "target_provider": provider, "collection_name": str(payload.get("output_collection_name", "") or ""), "reason": str(payload.get("completion_reason", "") or "")})
            )
        elif status in {"completed", "completed_with_errors"}:
            self.window.append_action_event(
                build_action_event("reindex_job_completed", {"job_id": job_id, "target_fingerprint": fingerprint, "target_provider": provider, "collection_name": str(payload.get("output_collection_name", "") or metadata.get("target_collection_name", "") or ""), "progress": f"{progress} · {promotion}"})
            )
        elif status == "failed":
            self.window.append_action_event(
                build_action_event("reindex_job_failed", {"job_id": job_id, "target_fingerprint": fingerprint, "target_provider": provider, "collection_name": str(payload.get("output_collection_name", "") or metadata.get("target_collection_name", "") or ""), "reason": str(payload.get("last_error", "") or payload.get("completion_reason", "") or "")})
            )

        if promotion == "promoted":
            self.window.append_action_event(
                build_action_event(
                    "reindex_job_promoted",
                    {
                        "job_id": job_id,
                        "target_fingerprint": fingerprint,
                        "target_provider": provider,
                        "collection_name": str(payload.get("output_collection_name", "") or ""),
                        "progress": str(payload.get("completion_reason", "") or "promoted"),
                    },
                )
            )

        active_fingerprint = self.rag_manager.load_settings().active_embedding_fingerprint.strip() or self.rag_manager.embedding_fingerprint
        if active_fingerprint != self._last_active_fingerprint:
            self._last_active_fingerprint = active_fingerprint
            self.window.append_action_event(
                build_action_event(
                    "active_collection_switched",
                    {
                        "fingerprint": active_fingerprint,
                        "collection_name": str(metadata.get("target_collection_name", "") or ""),
                        "provider": provider,
                    },
                )
            )

    def _handle_pending_decision_action(self, action: str, item_id: str) -> None:
        try:
            if action == "confirm":
                result = self.rag_manager.confirm_decision_candidate(item_id)
                self.window.append_action_event(
                    build_action_event(
                        "decision_confirmed",
                        {
                            "item_id": str(result.get("item_id", "") or item_id),
                            "reason": str(result.get("reason", "") or ""),
                        },
                    )
                )
            elif action == "reject":
                result = self.rag_manager.reject_decision_candidate(item_id)
                self.window.append_action_event(
                    build_action_event(
                        "decision_rejected",
                        {
                            "item_id": str(result.get("item_id", "") or item_id),
                            "reason": str(result.get("reason", "") or "manual"),
                        },
                    )
                )
            self._refresh_knowledge_surfaces()
            self._refresh_system_notifications(force=True)
        except Exception:
            logger.exception("Failed to process pending decision action=%s item_id=%s", action, item_id)

    def _handle_reindex_request(self) -> None:
        try:
            settings = self.rag_manager.load_settings()
            active_fingerprint = settings.active_embedding_fingerprint.strip() or self.rag_manager.embedding_fingerprint
            source_fingerprint = active_fingerprint or self.rag_manager.embedding_fingerprint
            job = self.reindex_manager.start_reindex(
                settings=settings,
                source_fingerprint=source_fingerprint,
                scope_type="full",
                reason="manual_ui",
            )
            self.window.append_action_event(
                build_action_event(
                    "reindex_job_created",
                    {
                        "job_id": job.job_id,
                        "target_fingerprint": job.target_fingerprint,
                        "target_provider": job.target_provider,
                        "collection_name": str(job.metadata.get("target_collection_name", "") if isinstance(job.metadata, dict) else ""),
                        "progress": f"0/{job.total_chunks}",
                    },
                )
            )
            self._refresh_knowledge_surfaces()
            self._refresh_system_notifications(force=True)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to start reindex job")
            self.window.flash_error_state(f"重建向量失败：{exc}")

    def _handle_reindex_job_action(self, action: str, job_id: str) -> None:
        try:
            job = None
            retry_reason = ""
            progress_reason = ""
            if action == "cancel":
                if self.reindex_manager.request_cancel(job_id, reason="ui_cancel"):
                    self.window.append_action_event(
                        build_action_event(
                            "reindex_job_cancel_requested",
                            {"job_id": job_id, "reason": "ui_cancel"},
                        )
                    )
            elif action == "promote":
                result = self.reindex_manager.promote_job(job_id)
                if result.get("ok"):
                    self.window.append_action_event(
                        build_action_event(
                            "reindex_job_promoted",
                            {
                                "job_id": job_id,
                                "target_fingerprint": str(result.get("fingerprint", "") or ""),
                                "progress": "active switched",
                            },
                        )
                    )
                else:
                    self.window.flash_error_state(f"索引切换失败：{result.get('reason', 'unknown')}")
            elif action == "retry_failed":
                job = self.reindex_manager.retry_failed_chunks(job_id, reason="ui_retry_failed_chunks")
                retry_reason = "all"
                progress_reason = f"retry:{job.parent_job_id or job_id}"
            elif action == "retry_failed_embedding":
                job = self.reindex_manager.retry_failed_chunks(
                    job_id,
                    reason="ui_retry_failed_embedding_errors",
                    reason_filter="embedding_provider_error",
                )
                retry_reason = "embedding_provider_error"
                progress_reason = f"retry:embedding:{job.parent_job_id or job_id}"
            elif action == "retry_failed_backend":
                job = self.reindex_manager.retry_failed_chunks(
                    job_id,
                    reason="ui_retry_failed_backend_errors",
                    reason_filter="backend_error",
                )
                retry_reason = "backend_error"
                progress_reason = f"retry:backend:{job.parent_job_id or job_id}"
            if job is not None:
                self.window.append_action_event(
                    build_action_event(
                        "reindex_job_created",
                        {
                            "job_id": job.job_id,
                            "target_fingerprint": job.target_fingerprint,
                            "target_provider": job.target_provider,
                            "collection_name": str(job.metadata.get("target_collection_name", "") if isinstance(job.metadata, dict) else ""),
                            "progress": f"0/{job.total_chunks}",
                            "reason": progress_reason,
                            "retry_reason_filter": retry_reason,
                        },
                    )
                )
            self._refresh_knowledge_surfaces()
            self._refresh_system_notifications(force=True)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to process reindex action=%s job_id=%s", action, job_id)
            self.window.flash_error_state(f"索引操作失败：{exc}")

    def _handle_notification_action(self, action: str, notification_id: str) -> None:
        try:
            notification_ids = [item.strip() for item in notification_id.split(",") if item.strip()]
            target_id = notification_ids[0] if notification_ids else notification_id
            if action == "dismiss":
                self.notification_engine.dismiss(target_id)
            elif action == "complete":
                self.notification_engine.mark_completed(target_id)
            elif action == "snooze":
                self.notification_engine.snooze(target_id, hours=4)
            elif action == "dismiss_many":
                self.notification_engine.dismiss_many(notification_ids)
            elif action == "complete_many":
                self.notification_engine.mark_completed_many(notification_ids)
            elif action == "snooze_many_1h":
                self.notification_engine.snooze_many(notification_ids, hours=1)
            elif action == "snooze_many_1d":
                self.notification_engine.snooze_many(notification_ids, hours=24)
            elif action == "snooze_many_tomorrow":
                self.notification_engine.snooze_until_tomorrow(notification_ids)
            self._refresh_system_notifications(force=True)
        except Exception:
            logger.exception("Failed to process notification action=%s notification_id=%s", action, notification_id)

    def _apply_boot_state(self, state: str, status_text: str) -> None:
        self._system_state = state
        self._presence_task_summary = status_text.strip() or self._presence_task_summary
        self.window.set_system_state(state, status_text)
        self._refresh_presence_surface()

    def _schedule_voice_warmup(self) -> None:
        if self._voice_warmup_scheduled or not voice_config.enabled or not voice_config.warmup_on_start:
            return
        if not (voice_config.speak_responses or voice_config.stream_responses or voice_config.speak_system):
            return
        self._voice_warmup_scheduled = True
        QTimer.singleShot(8000, self.voice.start_background_warmup)

    def _maybe_play_startup_status_audio(self, status_text: str) -> None:
        if not voice_config.enabled:
            return
        cleaned = status_text.strip()
        if not cleaned or cleaned == self._last_startup_status_spoken:
            return
        if cleaned == system_config.startup_status_lines[-1] and self.initializer.is_first_launch_today():
            return
        self.voice.speak_system_text(cleaned, priority=5)
        self._last_startup_status_spoken = cleaned

    def _next_startup_status_text(self) -> str:
        lines = system_config.startup_status_lines
        if not lines:
            return "系统启动中。"
        index = min(self._startup_status_index, len(lines) - 1)
        text = lines[index]
        if self._startup_status_index < len(lines) - 1:
            self._startup_status_index += 1
        return text

    def _effective_boot_state(self, boot_state: dict[str, object]) -> tuple[str, str]:
        state = str(boot_state["state"])
        status_text = str(boot_state["status_text"])
        if state == "warming_up":
            return state, self._next_startup_status_text()
        if state == "idle":
            self._startup_status_index = len(system_config.startup_status_lines) - 1
            return state, system_config.startup_status_lines[-1]
        return state, status_text

    def _maybe_play_welcome_audio(self, state: str) -> None:
        if self._welcome_played or not self.initializer.has_pending_welcome():
            return
        if state != "idle":
            return
        self.voice.speak_system_text(system_config.welcome_voice_text, priority=1)
        self.initializer.mark_welcome_played()
        self._welcome_played = True

    def _run_boot_sequence(self) -> None:
        if self._boot_started or self._shutting_down:
            return
        self._boot_started = True
        result = self.initializer.boot(self.llm.get_runtime_snapshot())
        self._sync_runtime_snapshot()
        effective_state, status_text = self._effective_boot_state(
            {
                "state": result.state,
                "status_text": result.status_text,
                "model_online": result.model_online,
                "network_online": result.network_online,
            }
        )
        self._apply_boot_state(effective_state, status_text)
        self._maybe_play_startup_status_audio(status_text)
        self._maybe_play_welcome_audio(effective_state)
        if effective_state == "idle":
            self._schedule_voice_warmup()
        if effective_state == "warming_up":
            self._startup_timer.start()

    def _refresh_startup_state(self) -> None:
        if self._shutting_down:
            return
        snapshot = self.llm.get_runtime_snapshot()
        boot_state = self.initializer.build_boot_result(snapshot)
        snapshot["network_online"] = boot_state["network_online"]
        self.window.set_runtime_snapshot(snapshot)
        effective_state, status_text = self._effective_boot_state(boot_state)
        self._apply_boot_state(effective_state, status_text)
        self._maybe_play_startup_status_audio(status_text)
        self._maybe_play_welcome_audio(effective_state)
        if effective_state == "idle":
            self._schedule_voice_warmup()
        self.window.set_runtime_status(self.llm.get_runtime_status())
        if effective_state != "warming_up":
            self._startup_timer.stop()

    def _is_error_reply(self, text: str) -> bool:
        lowered = text.strip().lower()
        return lowered.startswith("request failed:") or lowered.startswith("本地模型请求失败") or lowered.startswith("模型服务未就绪")

    def _build_user_log_text(self, user_text: str, attachment_paths: List[str]) -> str:
        text = user_text.strip()
        if attachment_paths:
            names = [p.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for p in attachment_paths]
            attach_line = "[attachments] " + ", ".join(names)
            return f"{text}\n{attach_line}".strip()
        return text

    def _request_has_image(self, attachment_paths: List[str]) -> bool:
        return any(path.lower().endswith(tuple(IMAGE_SUFFIXES)) for path in attachment_paths)

    def _play_request_voice(self, attachment_paths: List[str]) -> None:
        if not voice_config.enabled:
            return
        if self._request_has_image(attachment_paths):
            self.voice.system_line("analyzing_image")
        elif attachment_paths:
            self.voice.system_line("processing")

    def on_user_message(self, user_text: str, attachment_paths: List[str] | None = None, request_origin: str = "main_chat", request_id: str = "") -> None:
        if self._shutting_down:
            return

        attachments = list(attachment_paths or [])
        request_id = request_id or uuid.uuid4().hex
        user_log_text = self._build_user_log_text(user_text, attachments)
        interruption = self._runtime_v2.concurrent_tasks.begin_request(request_id)
        if interruption.interrupt_speech:
            self.voice.interrupt()
        if interruption.previous_request_id:
            self._mark_request_interrupted(interruption.previous_request_id)
        request_plan = self._runtime_v2.plan_request(
            user_text,
            previous_structured=dict(self._last_structured),
            session_id=self._session_id,
            request_id=request_id,
        )
        route_hints = self._runtime_v2.get_route_hints(request_id)
        self._request_plans[request_id] = request_plan
        logger.info(
            "request_plan_selected request_id=%s intent=%s planner_confidence=%.2f modality=%s speech_mode=%s force_card=%s",
            request_id,
            getattr(request_plan, "intent", ""),
            float(getattr(request_plan, "planner_confidence", 0.0) or 0.0),
            getattr(request_plan, "modality", ""),
            getattr(request_plan, "speech_mode", ""),
            getattr(request_plan, "force_card_type", ""),
        )
        self._current_task = user_text.strip()
        self._presence_processing = True
        self._presence_unread_reply = False

        self._sync_runtime_snapshot()
        self.window.clear_session_trace()
        self.window.set_current_task(user_text)
        self.window.set_agent_route("", "")
        self.window.set_tool_status("", "")
        self.window.start_processing(analyzing=bool(attachments))
        self._play_request_voice(attachments)

        worker = ChatWorker(
            self.llm,
            self._runtime_v2,
            self.voice,
            user_text,
            attachments,
            user_log_text,
            RouteContext(
                previous_skill=self._last_skill_name,
                previous_structured=dict(self._last_structured),
                screen_followup_remaining=self._screen_followup_remaining,
                session_id=self._session_id,
                perception_intent=str(route_hints.get("perception_intent", "") or ""),
                forced_bundle=str(route_hints.get("forced_bundle", "") or ""),
                web_task_type=str(route_hints.get("web_task_type", "") or ""),
                web_intent_plan=dict(route_hints.get("web_intent_plan") or {}),
                web_context=dict((self._last_structured or {}).get("web_context") or {}),
                preferred_routes=list(route_hints.get("preferred_routes") or []),
                preferred_modalities=list(route_hints.get("preferred_modalities") or []),
                active_focus=dict(route_hints.get("active_focus") or {}),
            ),
            request_origin=request_origin,
            request_id=request_id,
            allow_voice_streaming=request_plan.allow_voice_streaming,
        )
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        # UI updates must always hop back to the main Qt thread.
        worker.progress.connect(self._on_worker_progress, Qt.QueuedConnection)
        worker.finished.connect(self._on_assistant_reply, Qt.QueuedConnection)
        worker.finished.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._cleanup_thread_refs(thread))

        self._workers[thread] = worker
        self._threads.append(thread)
        thread.start()
        self._refresh_presence_surface()

    def _active_main_chat_request_id(self) -> str:
        return str(self._runtime_v2.concurrent_tasks.active_request_id or "").strip()

    def _is_stale_main_chat_request(self, request_id: str, request_origin: str = "main_chat") -> bool:
        if request_origin != "main_chat" or not request_id:
            return False
        active_request_id = self._active_main_chat_request_id()
        return bool(active_request_id and active_request_id != request_id)

    def _mark_request_interrupted(self, request_id: str) -> None:
        if not request_id:
            return
        interrupted_text = tr("presence_request_cancelled", self.preferences.ui_language)
        progress_message_id = self._progress_message_ids.get(request_id, "")
        if progress_message_id:
            self._progress_message_texts[request_id] = interrupted_text
            self.window.update_chat_message(
                progress_message_id,
                text=interrupted_text,
                payload={"speaker": "Fairy", "rich_text": False},
            )
        self._request_plans.pop(request_id, None)

    def _on_worker_progress(self, event_obj: object) -> None:
        if self._shutting_down or not isinstance(event_obj, ActionEvent):
            return

        request_id = str(event_obj.payload.get("request_id", "") or "").strip()
        request_origin = str(event_obj.payload.get("request_origin", "main_chat") or "main_chat").strip()
        if self._is_stale_main_chat_request(request_id, request_origin):
            logger.info("stale_progress_ignored request_id=%s active_request_id=%s event=%s", request_id, self._active_main_chat_request_id(), event_obj.name)
            return

        if event_obj.name == "assistant_response_chunk":
            self._handle_assistant_stream_chunk(event_obj.payload)
            return

        self.window.append_action_event(event_obj)
        self._handle_progress_update(event_obj)

        if event_obj.name == "rag_retrieval_visualized":
            self.window.set_retrieval_debug_snapshot(event_obj.payload)
            return

        if event_obj.name == "skill_routed":
            self._runtime_v2.record_tool_selection(
                request_id,
                str(event_obj.payload.get("chosen_skill", "") or ""),
                str(event_obj.payload.get("reason", "") or ""),
            )
            self.window.set_agent_route(
                str(event_obj.payload.get("chosen_skill", "")),
                str(event_obj.payload.get("reason", "")),
            )
            return

        if event_obj.name == "tool_call_start":
            self.window.set_tool_status(str(event_obj.payload.get("tool_name", "")), "running")
            return

        if event_obj.name == "tool_call_done":
            self.window.set_tool_status(str(event_obj.payload.get("tool_name", "")), "done")
            return

        if event_obj.name == "tool_call_failed":
            self.window.set_tool_status(str(event_obj.payload.get("tool_name", "")), "failed")
            return

        if event_obj.name == "command_stdout":
            self.window.append_terminal_output("stdout", event_obj.detail)
            return

        if event_obj.name == "command_stderr":
            self.window.append_terminal_output("stderr", event_obj.detail)
            return

        if event_obj.name == "skill_result_ready":
            self.window.set_tool_status("", "")

    def _handle_assistant_stream_chunk(self, payload: dict[str, object]) -> None:
        original_message_id = str(payload.get("message_id", "") or "").strip()
        request_id = str(payload.get("request_id", "") or "").strip()
        if self._is_stale_main_chat_request(request_id):
            logger.info("stale_stream_chunk_ignored request_id=%s active_request_id=%s", request_id, self._active_main_chat_request_id())
            return
        message_id = self._stream_message_aliases.get(original_message_id, original_message_id)
        if request_id and message_id == original_message_id:
            existing_progress_id = self._progress_message_ids.pop(request_id, "")
            if existing_progress_id:
                self._progress_message_texts.pop(request_id, None)
                self._stream_message_aliases[original_message_id] = existing_progress_id
                message_id = existing_progress_id
        token = str(payload.get("token", "") or "")
        if not message_id or not token:
            return

        current_text = self._streaming_message_text.get(message_id, "")
        next_text = current_text + token
        is_new_message = message_id not in self._streaming_message_text
        reusing_existing_message = message_id != original_message_id
        self._streaming_message_text[message_id] = next_text

        if is_new_message:
            if reusing_existing_message and self.window.update_chat_message(
                message_id,
                text=next_text,
                payload={"rich_text": False, "speaker": "Fairy"},
            ):
                return
            self.window.show_assistant_chat_message(
                ChatMessage.from_text_payload("Fairy", next_text, rich_text=False, message_id=message_id)
            )
            return

        self.window.update_chat_message(
            message_id,
            text=next_text,
            payload={"rich_text": False, "speaker": "Fairy"},
        )

    def _handle_progress_update(self, event_obj: ActionEvent) -> None:
        request_id = str(event_obj.payload.get("request_id", "") or "").strip()
        request_origin = str(event_obj.payload.get("request_origin", "main_chat") or "main_chat").strip()
        if request_origin != "main_chat" or not request_id:
            return
        if self._is_stale_main_chat_request(request_id, request_origin):
            return

        request_plan = self._request_plans.get(request_id)
        if request_plan is None:
            return
        if getattr(request_plan, "allow_voice_streaming", False) and event_obj.name == "skill_routed":
            return

        progress_event = self._runtime_v2.build_progress_event(
            event_obj.name,
            event_obj.payload,
            user_text=self._current_task,
            request_id=request_id,
        )
        if progress_event is None:
            return

        message_id = self._progress_message_ids.get(request_id)
        progress_text = progress_event.text.strip()
        if not progress_text:
            return

        if not message_id:
            progress_message = ChatMessage.from_text_payload("Fairy", progress_text, rich_text=False)
            self._progress_message_ids[request_id] = progress_message.id
            self._progress_message_texts[request_id] = progress_text
            self.window.show_assistant_chat_message(progress_message)
            return

        if self._progress_message_texts.get(request_id) == progress_text:
            return
        self._progress_message_texts[request_id] = progress_text
        self.window.update_chat_message(
            message_id,
            text=progress_text,
            payload={"speaker": "Fairy", "rich_text": False},
        )

    def _consume_progress_message_id(self, request_id: str) -> str:
        if not request_id:
            return ""
        self._progress_message_texts.pop(request_id, None)
        return self._progress_message_ids.pop(request_id, "")

    def _upsert_assistant_message(self, message: ChatMessage) -> None:
        payload_update = dict(message.payload)
        text = payload_update.pop("text", None)
        updated = self.window.update_chat_message(
            message.id,
            text=str(text) if text is not None else None,
            payload=payload_update,
        )
        if not updated:
            self.window.show_assistant_chat_message(message)

    def _select_presence_message(self, messages: list[ChatMessage]) -> ChatMessage | None:
        if not messages:
            return None
        for message in messages:
            if message.type != "text":
                return message
        return messages[0]

    def _deliver_voice_response(self, normalized_response, *, payload: dict, user_log_text: str) -> None:
        if not voice_config.enabled or payload.get("success") is False:
            return

        if bool(payload.get("voice_streamed")):
            return

        speech_payload = getattr(normalized_response, "speech_payload", None)
        if speech_payload is not None:
            speech_text = str(speech_payload.text or "").strip()
        else:
            speech_meta = dict((payload.get("meta") or {}).get("speech") or {})
            speech_text = str(speech_meta.get("text") or "").strip()
        if speech_text and len(speech_text) >= voice_config.speak_min_chars:
            self.voice.speak(speech_text, priority=20)
            return

        if "[attachments]" in user_log_text:
            self.voice.system_line("analysis_complete", priority=20)
        else:
            self.voice.system_line("complete", priority=20)

    def _render_assistant_text(self, payload: dict) -> str:
        assistant_text = str(payload.get("assistant_text", "") or "").strip()
        structured = payload.get("structured") or {}
        if not assistant_text:
            assistant_text = str(payload.get("summary", "") or "").strip()
        if isinstance(structured, dict):
            screen_summary = str(structured.get("screen_summary", "") or "").strip()
            suggested_next_step = str(structured.get("suggested_next_step", "") or "").strip()
            if not assistant_text:
                if screen_summary:
                    assistant_text = screen_summary
                elif structured.get("summary"):
                    assistant_text = str(structured.get("summary", "")).strip()
                elif suggested_next_step:
                    assistant_text = suggested_next_step
            elif payload.get("skill_name") == "screen-understanding" and screen_summary and len(assistant_text) < 48:
                extra_lines = [assistant_text, screen_summary]
                if suggested_next_step:
                    extra_lines.append(f"\u5efa\u8bae\uff1a{suggested_next_step}")
                assistant_text = "\n".join(line for line in extra_lines if line.strip())
        return assistant_text.strip()

    def _render_assistant_html(self, assistant_text: str, payload: dict) -> str:
        body = html.escape(assistant_text).replace("\n", "<br>")
        parts: list[str] = []
        structured = payload.get("structured") if isinstance(payload.get("structured"), dict) else {}
        badges: list[str] = []
        if structured:
            if structured.get("used_screen_capture"):
                badges.append("已使用当前屏幕截图")
            provider_route = str(structured.get("provider_route", "") or "").strip()
            if provider_route == "cloud_vision":
                badges.append("使用云端视觉模型分析")
            elif provider_route == "cloud":
                badges.append("使用云端模型回答")
            elif provider_route.startswith("local_fallback"):
                badges.append("云端失败，已回退本地模型")
        if badges:
            badge_html = "".join(
                f"<span style='display:inline-block; margin:0 6px 6px 0; color:#CFE3FF; "
                f"background:rgba(18,31,54,0.92); border:1px solid rgba(84,120,168,0.6); "
                f"border-radius:10px; padding:3px 8px; font-size:11px;'>{html.escape(item)}</span>"
                for item in badges
            )
            parts.append(f"<div style='margin-bottom:4px;'>{badge_html}</div>")
        if body:
            parts.append(body)
        sources = payload.get("sources") or []
        if sources:
            source_lines: list[str] = []
            for item in list(sources)[:3]:
                if not isinstance(item, dict):
                    continue
                title = html.escape(str(item.get("title", "") or "").strip())
                url = str(item.get("url", "") or "").strip()
                if title and url:
                    safe_url = html.escape(url, quote=True)
                    source_lines.append(
                        f'<li><a href="{safe_url}" style="color:#8FC2FF; text-decoration:none;">{title}</a></li>'
                    )
            if source_lines:
                parts.append(
                    "<div style='margin-top:8px; color:#AFC2DB;'><b>\u6765\u6e90</b>"
                    "<ul style='margin:6px 0 0 18px; padding:0;'>"
                    + "".join(source_lines)
                    + "</ul></div>"
                )
        return "".join(parts)

    def _presence_reply_excerpt(self, text: str) -> str:
        compact = " ".join((text or "").split())
        if len(compact) <= 220:
            return compact
        return compact[:217].rstrip() + "..."

    def _remember_structured_context(self, payload: dict, normalized_response=None) -> dict:
        remembered = dict(payload.get("structured")) if isinstance(payload.get("structured"), dict) else {}
        if normalized_response is None:
            return remembered

        if isinstance(normalized_response, dict):
            raw_cards = list(normalized_response.get("cards") or [])
            cards = []
            for item in raw_cards:
                if not isinstance(item, dict):
                    continue
                cards.append(
                    type(
                        "_CardRef",
                        (),
                        {
                            "type": str(item.get("type") or "generic_info"),
                            "data": dict(item.get("data") or {}),
                        },
                    )()
                )
            normalized_text_reply = str(normalized_response.get("text") or "").strip()
        else:
            cards = list(getattr(normalized_response, "card_payloads", []) or [])
            normalized_text_reply = str(getattr(normalized_response, "text_reply", "") or "").strip()
        primary_card = next((card for card in cards if card.type in {"weather", "location", "news_list"}), None)
        if primary_card is None:
            primary_card = cards[0] if cards else None
        if primary_card is None:
            return remembered

        intent_payload = remembered.get("intent") if isinstance(remembered.get("intent"), dict) else {}
        intent_map = {
            "weather": "weather_lookup",
            "location": "location_lookup",
            "news_list": "news_lookup",
            "generic_info": "generic_info",
        }
        intent_type = intent_map.get(primary_card.type, primary_card.type)
        intent_payload.setdefault("intent_type", intent_type)
        remembered["intent"] = intent_payload
        remembered.setdefault("type", primary_card.type)
        remembered.setdefault("card_type", primary_card.type)

        card_data = dict(primary_card.data)
        if primary_card.type == "weather":
            remembered.setdefault("weather", dict(card_data))
            remembered.setdefault("city", card_data.get("city"))
            remembered.setdefault("country", card_data.get("country"))
            remembered.setdefault("condition", card_data.get("condition"))
            remembered.setdefault("temp", card_data.get("temperature_c"))
            remembered.setdefault("high", card_data.get("high_c"))
            remembered.setdefault("low", card_data.get("low_c"))
            remembered.setdefault("feels_like", card_data.get("feels_like_c"))
            remembered.setdefault("humidity", card_data.get("humidity_percent"))
            remembered.setdefault("wind_kmh", card_data.get("wind_kmh"))
            remembered.setdefault("weather_location", card_data.get("city"))
            remembered.setdefault("summary", card_data.get("summary"))
        elif primary_card.type == "location":
            remembered.setdefault("location", dict(card_data))
            remembered.setdefault("title", card_data.get("title"))
            remembered.setdefault("place_name", card_data.get("title"))
            remembered.setdefault("address", card_data.get("address"))
            remembered.setdefault("city", card_data.get("city"))
            remembered.setdefault("region", card_data.get("region"))
            remembered.setdefault("country", card_data.get("country"))
            remembered.setdefault("lat", card_data.get("lat"))
            remembered.setdefault("lon", card_data.get("lon"))
            remembered.setdefault("distance", card_data.get("distance_text"))
            remembered.setdefault("image_url", card_data.get("map_preview_url"))
            remembered.setdefault("map_preview_url", card_data.get("map_preview_url"))
            remembered.setdefault("map_url", card_data.get("external_map_url"))
            remembered.setdefault("external_map_url", card_data.get("external_map_url"))
            remembered.setdefault("summary", card_data.get("summary"))
        elif primary_card.type == "news_list":
            remembered.setdefault("news_items", list(card_data.get("items") or []))
            remembered.setdefault("summary", normalized_text_reply)

        return remembered

    def _clear_streaming_message(self, message_id: str) -> None:
        if not message_id:
            return
        self._streaming_message_text.pop(message_id, None)
        stale_aliases = [source for source, target in self._stream_message_aliases.items() if target == message_id or source == message_id]
        for alias in stale_aliases:
            self._stream_message_aliases.pop(alias, None)

    def _on_assistant_reply(self, payload: object) -> None:
        if self._shutting_down or not isinstance(payload, dict):
            return

        request_origin = payload.get("request_origin", "main_chat")
        if request_origin != "main_chat":
            logger.info("assistant_reply_filtered_by_origin origin=%s expected=main_chat", request_origin)
            return

        request_id = str(payload.get("request_id", "") or "").strip()
        if self._is_stale_main_chat_request(request_id):
            logger.info("stale_reply_ignored request_id=%s active_request_id=%s", request_id, self._active_main_chat_request_id())
            stale_stream_message_id = self._stream_message_aliases.pop(
                str(payload.get("stream_message_id", "") or "").strip(),
                str(payload.get("stream_message_id", "") or "").strip(),
            )
            self._clear_streaming_message(stale_stream_message_id)
            self._consume_progress_message_id(request_id)
            self._request_plans.pop(request_id, None)
            self._runtime_v2.concurrent_tasks.finish_request(request_id)
            return

        self.window.stop_thinking()
        self.window.set_tool_status("", "")
        self._presence_processing = False
        stream_message_id = self._stream_message_aliases.pop(
            str(payload.get("stream_message_id", "") or "").strip(),
            str(payload.get("stream_message_id", "") or "").strip(),
        )
        progress_message_id = self._consume_progress_message_id(request_id)
        text_message_id = stream_message_id or progress_message_id or None

        if payload.get("cancelled"):
            cancelled_text = tr("presence_request_cancelled", self.preferences.ui_language)
            cancelled_message = ChatMessage.from_text_payload(
                "Fairy",
                cancelled_text,
                rich_text=False,
                message_id=text_message_id,
            )
            self._upsert_assistant_message(cancelled_message)
            self._clear_streaming_message(stream_message_id)
            if not self.window.isVisible():
                self.presence_window.show_reply_message(cancelled_message, error=False)
            self._sync_runtime_snapshot()
            self.window.set_runtime_status(self.llm.get_runtime_status())
            self._presence_task_summary = tr("presence_request_cancelled_summary", self.preferences.ui_language)
            self._refresh_presence_surface()
            self._request_plans.pop(request_id, None)
            self._runtime_v2.concurrent_tasks.finish_request(request_id)
            return

        user_log_text = str(payload.get("user_log_text", "") or payload.get("user_text", ""))
        assistant_text = self._render_assistant_text(payload)
        assistant_html = self._render_assistant_html(assistant_text, payload)
        logger.info(
            "assistant_reply_ready skill=%s len=%s success=%s",
            payload.get("skill_name", ""),
            len(assistant_text),
            payload.get("success", True),
        )

        if not assistant_text:
            assistant_text = "\u8bf7\u6c42\u5df2\u63a5\u6536\uff0c\u4f46\u5f53\u524d\u6ca1\u6709\u53ef\u8fd4\u56de\u7684\u7ed3\u679c\u3002"

        if self._is_error_reply(assistant_text) or payload.get("success") is False:
            self.voice.interrupt()
            error_message = ChatMessage.from_text_payload(
                "Fairy",
                assistant_html or assistant_text,
                rich_text=bool(assistant_html),
                message_id=text_message_id,
            )
            self._upsert_assistant_message(error_message)
            self.window.flash_error_state()
            self._presence_task_summary = tr("presence_request_failed_summary", self.preferences.ui_language)
            if not self.window.isVisible():
                self.presence_window.show_reply_message(error_message, error=True)
        else:
            request_plan = self._request_plans.get(request_id)
            if isinstance(payload.get("cards"), list) and isinstance(payload.get("meta"), dict):
                normalized_response = payload
            else:
                logger.warning("assistant_mode_non_contract_payload request_id=%s", request_id)
                normalized_response = {
                    "request_id": request_id,
                    "session_id": self._session_id,
                    "text": assistant_text,
                    "cards": [],
                    "meta": {
                        "intent": getattr(request_plan, "intent", ""),
                        "modality": "text_only",
                        "speech": {"mode": "detailed_explainer", "text": assistant_text, "allow_streaming": False},
                    },
                    "errors": list(payload.get("errors") or []),
                }
            modality = str((normalized_response.get("meta") or {}).get("modality") or "")
            speech_mode = str((((normalized_response.get("meta") or {}).get("speech") or {}).get("mode")) or "")
            card_names = ",".join(str(card.get("type") or "") for card in list(normalized_response.get("cards") or []))
            trace_bundle = self._runtime_v2.get_trace_bundle(request_id)
            logger.info(
                "response_modality_decided request_id=%s intent=%s planner_confidence=%.2f modality=%s speech_mode=%s cards=%s",
                request_id,
                getattr(request_plan, "intent", ""),
                float(getattr(request_plan, "planner_confidence", 0.0) or 0.0),
                modality,
                speech_mode,
                card_names,
            )
            logger.info(
                "runtime_trace_summary request_id=%s perception=%s tool_chain=%s selected_tool=%s schema_cards=%s renderers=%s speech_mode=%s states=%s fallback=%s",
                request_id,
                trace_bundle.get("response_trace", {}).get("perception", {}).get("intent", ""),
                ",".join(trace_bundle.get("tool_trace", {}).get("tool_chain", []) or []),
                trace_bundle.get("tool_trace", {}).get("selected_tool", ""),
                ",".join(trace_bundle.get("schema_trace", {}).get("card_types", []) or []),
                ",".join(trace_bundle.get("renderer_trace", {}).get("renderers", []) or []),
                trace_bundle.get("speech_trace", {}).get("mode", ""),
                "->".join(item.get("to_state", "") for item in trace_bundle.get("state_trace", []) if item.get("to_state")),
                trace_bundle.get("schema_trace", {}).get("fallback_reason", "") or trace_bundle.get("renderer_trace", {}).get("fallback_reason", ""),
            )
            messages = build_chat_messages_from_response(
                normalized_response,
                text_message_id=text_message_id,
            )
            for message in messages:
                self._upsert_assistant_message(message)
            self._deliver_voice_response(
                normalized_response,
                payload=payload,
                user_log_text=user_log_text,
            )
            self._presence_task_summary = tr("presence_request_complete_summary", self.preferences.ui_language)
            if not self.window.isVisible():
                presence_message = self._select_presence_message(messages)
                if presence_message is not None:
                    self.presence_window.show_reply_message(presence_message, error=False)
                self._presence_unread_reply = False
        self._clear_streaming_message(stream_message_id)
        self._request_plans.pop(request_id, None)
        self._runtime_v2.concurrent_tasks.finish_request(request_id)

        self.window.show_session_artifacts(
            payload.get("changed_files") or [],
            payload.get("commands_run") or [],
            payload.get("validations") or [],
        )

        self._sync_runtime_snapshot()
        self.window.set_runtime_status(self.llm.get_runtime_status())
        self._refresh_presence_surface()

        self.session_repo.rename_if_default(self._session_id, user_log_text[:64])
        user_message_id = self.message_repo.add_message(
            session_id=self._session_id,
            role="user",
            content=user_log_text,
            metadata={"skill_name": payload.get("skill_name", ""), "success": payload.get("success", True)},
        )
        self.message_repo.add_message(
            session_id=self._session_id,
            role="assistant",
            content=assistant_text,
            metadata={
                "skill_name": payload.get("skill_name", ""),
                "success": payload.get("success", True),
                "summary": payload.get("summary", ""),
                "user_message_id": user_message_id,
            },
        )
        self.history.append({"role": "user", "content": user_log_text})
        self.history.append({"role": "assistant", "content": assistant_text})
        self._last_skill_name = str(payload.get("skill_name", "") or "")
        self._last_structured = self._remember_structured_context(payload, normalized_response if "normalized_response" in locals() else None)
        if self._last_skill_name == "screen-understanding":
            self._screen_followup_remaining = 3
        else:
            self._screen_followup_remaining = 0

    def _cleanup_thread_refs(self, thread: QThread) -> None:
        if thread in self._threads:
            self._threads.remove(thread)
        self._workers.pop(thread, None)

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        logger.info("AssistantModeController shutdown started")
        if self._startup_timer.isActive():
            self._startup_timer.stop()
        if self._mode_timer.isActive():
            self._mode_timer.stop()
        if self._knowledge_timer.isActive():
            self._knowledge_timer.stop()
        if self._notification_timer.isActive():
            self._notification_timer.stop()

        try:
            self.window.prepare_for_shutdown()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.presence_window.prepare_for_shutdown()
            self.presence_window.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.window.close()
        except Exception:  # noqa: BLE001
            pass

        for thread in list(self._threads):
            try:
                thread.requestInterruption()
                thread.quit()
                if not thread.wait(1500):
                    thread.terminate()
                    thread.wait(1000)
            except Exception:  # noqa: BLE001
                pass

        self._threads.clear()
        self._workers.clear()
        self.voice.shutdown()
        self.llm.shutdown()
        logger.info("AssistantModeController shutdown completed")

    def shutdown_and_quit(self) -> None:
        self.shutdown()
        callback = self.on_quit_requested
        if callback is not None:
            callback()
