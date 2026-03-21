from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Iterable

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
from app.prompts import (
    ROUTER_ORCHESTRATOR_PROMPT,
    build_direct_answer_route_instructions,
    build_game_mode_route_instructions,
    build_json_helper_prompt,
)
from app.rag import RagManager, get_reindex_manager
from app.skill_router import LLMRouteDecision, RouteCandidate, RouteContext, RoutingDecision, SkillRouter
from app.skills.agent_shell_skill import AgentShellSkill
from app.skills.document_editor_skill import DocumentEditorSkill
from app.skills.news_intelligence_skill import NewsIntelligenceSkill
from app.skills.screen_understanding_skill import ScreenUnderstandingSkill
from app.skills.web_research_skill import WebResearchSkill
from app.storage.repositories import ActionLogRepo
from app.system_notifications import NotificationEngine
from app.tool_registry import ToolRegistry

# --- Lazy Skill Runtime (feature-flagged) ---
from app.lazy_runtime.feature_flags import lazy_skills_flags
from app.lazy_runtime.lazy_dispatcher import LazyDispatcher
from app.lazy_runtime.legacy_decommissioning import LegacyPromptDisabler, StrictDeterministicMode


logger = logging.getLogger(__name__)
CoreEventCallback = Callable[[str, dict[str, Any]], None]
ResponseChunkCallback = Callable[[str], None]


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
    ) -> dict[str, Any]:
        metric_result = self._extract_metric_summary(user_request, pages)
        if metric_result is not None:
            return metric_result

        prompt = {
            "user_request": user_request,
            "pages": pages,
            "comparison": comparison,
        }
        response = self.llm.execute_task(
            self._with_runtime_context(
                build_json_helper_prompt(
                    keys=("summary", "recommendation", "response_text"),
                    extra_rules=(
                        "Write concise Chinese.",
                        "response_text should be 2-4 short natural sentences.",
                    ),
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
                "summary": "网页信息已整理。",
                "recommendation": comparison.get("recommendation", ""),
                "response_text": "请求已接收。网页信息已整理。建议先查看关键差异后再决定。",
            },
        )

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
        summary = f"已定位到“{channel_name}”。当前识别到的粉丝数约为 {follower_count}。"
        recommendation = "建议打开对应主页再次确认，因为平台展示数字可能随时间变化。"
        response_text = (
            "请求已接收。"
            f"已定位到“{channel_name}”。当前识别到的粉丝数约为 {follower_count}。"
            "该结果来自当前页面可见信息。"
        )
        return {
            "summary": summary,
            "recommendation": recommendation,
            "response_text": response_text,
            "metric_name": "followers",
            "metric_value": follower_count,
            "matched_name": channel_name,
            "matched_url": best.get("url", ""),
        }

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

        self.browser = BrowserCapability(self.registry, self.mcp)
        self.documents = DocumentCapability(self.registry, self.mcp, llm)
        self.commands = CommandCapability(self.registry, self.mcp)
        self.screen = ScreenCapability(self.registry, self.mcp, llm)

        self.skills = {
            AgentShellSkill.SPEC.name: AgentShellSkill(
                llm,
                self.documents,
                self.browser,
                self.commands,
                event_callback=self._emit_event,
            ),
            WebResearchSkill.SPEC.name: WebResearchSkill(self.browser, self.llm_helper),
            DocumentEditorSkill.SPEC.name: DocumentEditorSkill(self.documents, self.llm_helper),
            NewsIntelligenceSkill.SPEC.name: NewsIntelligenceSkill(self.news),
            ScreenUnderstandingSkill.SPEC.name: ScreenUnderstandingSkill(self.screen, self.llm_helper),
        }
        self.router = SkillRouter(
            [
                AgentShellSkill.SPEC,
                WebResearchSkill.SPEC,
                DocumentEditorSkill.SPEC,
                NewsIntelligenceSkill.SPEC,
                ScreenUnderstandingSkill.SPEC,
            ]
        )

        # --- Lazy Skill Runtime (feature-flagged) ---
        self._lazy_dispatcher: LazyDispatcher | None = None
        if lazy_skills_flags.use_lazy_skills:
            try:
                self._lazy_dispatcher = LazyDispatcher(
                    self.llm,
                    self.registry,
                    event_callback=self._emit_event,
                )
                logger.info("lazy_dispatcher_initialized")
            except Exception:
                logger.exception("lazy_dispatcher_init_failed — falling back to legacy pipeline")
                self._lazy_dispatcher = None

    def _execute_text_response(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        attachment_paths: list[str],
        max_tokens: int,
        temperature: float,
        instruction_label: str,
        fallback_text: str = "??????????????????",
    ) -> str:
        if self._response_chunk_callback is not None:
            chunks: list[str] = []
            try:
                for chunk in self.llm.stream_task(
                    system_prompt,
                    user_prompt,
                    attachment_paths=attachment_paths,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    instruction_label=instruction_label,
                ):
                    token = str(chunk or "")
                    if not token:
                        continue
                    chunks.append(token)
                    try:
                        self._response_chunk_callback(token)
                    except Exception:
                        logger.debug("response_chunk_callback_failed", exc_info=True)
                streamed_text = "".join(chunks).strip()
                if streamed_text:
                    return streamed_text
            except Exception:
                logger.exception("task_stream_failed instruction=%s", instruction_label)

        response = self.llm.execute_task(
            system_prompt,
            user_prompt,
            attachment_paths=attachment_paths,
            max_tokens=max_tokens,
            temperature=temperature,
            instruction_label=instruction_label,
        )
        return response.text.strip() or fallback_text

    def _get_persona_engine(self) -> PersonaEngine:
        if self._persona is None:
            self._persona = PersonaEngine()
        return self._persona

    def _fallback_task_type(self, user_request: str, *, chosen_skill: str = "", task_category: str = "") -> str:
        lowered = user_request.lower()
        if chosen_skill in {"direct_answer", "knowledge_lookup"}:
            if any(token in lowered for token in ("reindex", "persona", "memory", "rag", "fingerprint", "backend", "provider")):
                return "planning"
            return "chat"
        if chosen_skill == "system_ops":
            return "planning"
        if chosen_skill in {"weather", "news", "web_search"}:
            return "web_search"
        if chosen_skill == "document_editor_skill":
            return "file_reading"
        if chosen_skill in {"web_research_skill", "news_intelligence_skill"} or task_category == "web_research":
            return "web_search"
        if chosen_skill == "screen_understanding_skill":
            return "planning"
        if chosen_skill == "agent_shell_skill":
            if any(token in lowered for token in ("debug", "错误", "异常", "排查", "修复", "fix")):
                return "debugging"
            if any(token in lowered for token in ("计划", "路线", "优先级", "先做什么", "先改", "方案")):
                return "planning"
            return "coding"
        if task_category in {"coding_help", "fairy_development", "browser_agent_task"}:
            return "coding"
        if any(token in lowered for token in ("总结", "摘要", "概括")):
            return "summary"
        return "chat"

    def _should_use_game_mode_direct_answer(self, active_mode: str) -> bool:
        return active_mode == "game_mode"

    def _looks_like_model_error(self, text: str) -> bool:
        lowered = text.strip().lower()
        return lowered.startswith("任务执行失败") or lowered.startswith("模型服务未就绪") or lowered.startswith("本地模型请求失败")

    def _looks_like_screen_request(self, user_request: str) -> bool:
        lowered = user_request.lower()
        markers = (
            "\u5c4f\u5e55",
            "\u754c\u9762",
            "\u7a97\u53e3",
            "\u622a\u56fe",
            "\u770b\u6211\u73b0\u5728",
            "\u770b\u4e00\u4e0b\u753b\u9762",
            "\u5f53\u524d\u753b\u9762",
            "\u5c4f\u5e55\u5185\u5bb9",
        )
        for marker in markers:
            if marker in lowered:
                return True
        return False

    def _execute_game_mode_direct_answer(
        self,
        task_id: str,
        user_request: str,
        attachments: list[str],
        memory_prompt: str,
    ) -> SkillResult:
        route_reason = "当前处于游戏模式，直接走云端模型回答，不使用本地技能链。"
        self._active_done_steps.append("route:game_mode_direct_answer")
        self.memory.update_task(
            task_id,
            user_request,
            current_step="game_mode_direct_answer",
            done_steps=list(dict.fromkeys(self._active_done_steps)),
            status="running",
        )
        logger.info("skill_routed chosen_skill=%s reason=%s", "game_mode_direct_answer", route_reason)
        self._emit_event(
            "skill_routed",
            {
                "chosen_skill": "game_mode_direct_answer",
                "reason": route_reason,
                "allowed_tools": [],
            },
        )

        system_prompt = build_game_mode_route_instructions()
        effective_attachments = list(attachments)
        screen_note = ""
        direct_route = "game_mode_cloud_chat"
        if self._looks_like_screen_request(user_request) and not effective_attachments:
            direct_route = "screen_to_cloud_vision"
            self._emit_event("route_selected", {"route": direct_route, "reason": "屏幕请求在游戏模式下直接走云端视觉。"})
            active_app = self.screen.get_active_app(allowed_tools=["get_active_app"])
            capture = self.screen.capture_screen(allowed_tools=["capture_screen"])
            image_path = str(capture.get("image_path", "") or "").strip()
            if image_path:
                effective_attachments.append(image_path)
                app_name = str(active_app.get("context_app", {}).get("app_name") or active_app.get("app_name") or "").strip()
                window_title = str(active_app.get("context_app", {}).get("window_title") or active_app.get("window_title") or "").strip()
                screen_note = (
                    "The attached image is the user's current screen capture. "
                    f"Foreground app: {app_name or 'unknown'}. "
                    f"Window title: {window_title or 'unknown'}."
                )
        else:
            self._emit_event("route_selected", {"route": direct_route, "reason": "游戏模式下直接走云端文本回答。"})
        if memory_prompt.strip():
            system_prompt += f"\n\n{memory_prompt.strip()}"
        if screen_note:
            system_prompt += f"\n\n{screen_note}"

        selected_provider = self.llm.provider_router.get_game_settings().selected_provider_id
        selected_model = self.llm.provider_router.get_game_settings().providers.get(selected_provider)
        if isinstance(selected_model, dict):
            provider_model = str(selected_model.get("model", "") or "")
        else:
            provider_model = str(getattr(selected_model, "model", "") or "")
        self._emit_event(
            "provider_request_start",
            {
                "provider_id": selected_provider,
                "model": provider_model,
                "route": "cloud_vision" if effective_attachments else "cloud",
                "uses_screen_capture": bool(screen_note),
            },
        )
        response_text = self._execute_text_response(
            system_prompt,
            user_request,
            attachment_paths=effective_attachments,
            max_tokens=256,
            temperature=0.3,
            instruction_label="Route instructions",
        )
        route_decision = self.llm.provider_router.current_route_decision()
        if route_decision.fallback_used:
            self._emit_event(
                "provider_fallback_used",
                {
                    "provider_id": route_decision.provider_id,
                    "model": route_decision.model,
                    "route": route_decision.route_type,
                    "reason": route_decision.fallback_reason or route_decision.warning,
                },
            )
        else:
            self._emit_event(
                "provider_response_received",
                {
                    "provider_id": route_decision.provider_id,
                    "model": route_decision.model,
                    "route": route_decision.route_type,
                    "uses_screen_capture": bool(screen_note),
                },
            )
        logger.info(
            "game_mode_direct_answer provider=%s model=%s route=%s fallback=%s reason=%s",
            route_decision.provider_id,
            route_decision.model,
            route_decision.route_type,
            route_decision.fallback_used,
            route_decision.fallback_reason,
        )
        return SkillResult(
            skill_name="game_mode_direct_answer",
            success=not self._looks_like_model_error(response_text),
            summary=response_text[:220],
            structured={
                "active_mode": route_decision.active_mode,
                "provider_id": route_decision.provider_id,
                "provider_model": route_decision.model,
                "provider_route": route_decision.route_type,
                "provider_fallback_used": route_decision.fallback_used,
                "provider_fallback_reason": route_decision.fallback_reason,
                "used_screen_capture": bool(screen_note),
                "analysis_route": direct_route,
            },
            response_text=response_text,
        )

    def _is_result_useful(self, route_name: str, result: SkillResult) -> bool:
        if not result.success:
            return False
        text = (result.response_text or result.summary or "").strip()
        if not text:
            return False
        if route_name in {"weather", "news", "web_search"} and self._looks_like_model_error(text):
            return False
        return True

    def _route_name_for_memory(self, candidate: RouteCandidate) -> str:
        return candidate.skill_name or candidate.name

    def _build_routing_debug_payload(self, decision: RoutingDecision) -> dict[str, Any]:
        return {
            "primary_intent": decision.primary_intent,
            "tool_needed": decision.tool_needed,
            "clarification_needed": decision.clarification_needed,
            "selected_tool": decision.selected_tool,
            "candidates": [
                {
                    "name": candidate.name,
                    "score": round(float(candidate.score), 4),
                    "reason": candidate.reason,
                    "skill_name": candidate.skill_name,
                }
                for candidate in decision.candidates
            ],
        }

    def _build_system_ops_snapshot(self) -> dict[str, Any]:
        runtime_snapshot = self.llm.get_runtime_snapshot()
        rag_settings = self.rag.load_settings()
        active_notifications = self.notification_engine.scan_all()
        pending_decisions = self.rag.list_pending_decisions(limit=5)
        jobs = self.reindex.list_jobs(limit=5)
        running_jobs = [job for job in jobs if job.status in {"queued", "running", "cancel_requested"}]
        latest_job = jobs[0] if jobs else None
        return {
            "active_mode": str(runtime_snapshot.get("active_mode", "") or "normal_mode"),
            "provider_id": str(runtime_snapshot.get("provider_id", "") or "local_server"),
            "model": str(runtime_snapshot.get("model", "") or self.llm.config.model),
            "vector_backend": self.rag.vector_backend_name,
            "active_fingerprint": str(rag_settings.active_embedding_fingerprint or self.rag.embedding_fingerprint or ""),
            "notifications": active_notifications,
            "pending_decisions": pending_decisions,
            "jobs": jobs,
            "running_jobs": running_jobs,
            "latest_job": latest_job,
        }

    def _render_system_ops_answer(self, user_request: str, snapshot: dict[str, Any]) -> tuple[str, str]:
        lowered = user_request.lower()
        notifications = list(snapshot.get("notifications") or [])
        pending_decisions = list(snapshot.get("pending_decisions") or [])
        jobs = list(snapshot.get("jobs") or [])
        running_jobs = list(snapshot.get("running_jobs") or [])
        latest_job = snapshot.get("latest_job")
        active_fingerprint = str(snapshot.get("active_fingerprint", "") or "").strip()
        backend = str(snapshot.get("vector_backend", "") or "").strip()
        provider = str(snapshot.get("provider_id", "") or "").strip()
        model = str(snapshot.get("model", "") or "").strip()

        if any(token in lowered for token in ("通知", "提醒", "notification")):
            if not notifications:
                return (
                    "当前没有待处理系统提醒。",
                    "确认，当前没有待处理系统提醒。需要的话我也可以继续帮你打开 Jobs 或 Decisions 页面排查。",
                )
            critical = [item for item in notifications if str(getattr(item, "level", "")).strip() == "critical"]
            action_required = [item for item in notifications if str(getattr(item, "level", "")).strip() == "action_required"]
            preview = "；".join(str(getattr(item, "title", "")).strip() for item in notifications[:3])
            summary = f"当前有 {len(notifications)} 条系统提醒，其中 critical={len(critical)}，action_required={len(action_required)}。"
            response = f"确认，当前有 {len(notifications)} 条待处理通知。{summary}"
            if preview:
                response += f" 主要是：{preview}。"
            response += "建议先处理 critical 和 action_required。"
            return summary, response

        if any(token in lowered for token in ("decision", "待确认", "待处理决定", "pending decision")):
            if not pending_decisions:
                return (
                    "当前没有待确认 decision。",
                    "当前没有待确认 decision。已确认和已拒绝的记录可以在 Decisions 页面继续看。",
                )
            preview = "；".join(str(item.get("title", "") or item.get("content", "") or "").strip()[:36] for item in pending_decisions[:3])
            summary = f"当前有 {len(pending_decisions)} 条待确认 decision。"
            response = f"确认，当前有 {len(pending_decisions)} 条待确认 decision。"
            if preview:
                response += f" 主要是：{preview}。"
            response += "建议去 Decisions 页面做确认或拒绝。"
            return summary, response

        if any(token in lowered for token in ("reindex", "索引", "job", "jobs", "embedding", "collection")):
            if not jobs:
                return (
                    "当前没有 reindex job 记录。",
                    "当前没有 reindex job 记录。需要的话我可以直接帮你发起一次新的 embeddings 重建。",
                )
            latest_text = ""
            if latest_job is not None:
                latest_text = (
                    f"最新 job 状态是 {latest_job.status}，promotion={latest_job.promotion_status}，"
                    f"目标 fingerprint={latest_job.target_fingerprint or 'unknown'}。"
                )
            response = (
                f"当前共有 {len(jobs)} 条 reindex job，运行中的有 {len(running_jobs)} 条。"
                f"{latest_text} 当前 active fingerprint 是 {active_fingerprint or 'unknown'}，backend 是 {backend or 'unknown'}。"
            )
            return "已汇总当前 reindex/job 状态。", response

        summary = "已汇总 Fairy 当前系统状态。"
        response = (
            f"当前模式是 {snapshot.get('active_mode', 'normal_mode')}，provider={provider or 'unknown'}，model={model or 'unknown'}。"
            f" active fingerprint={active_fingerprint or 'unknown'}，vector backend={backend or 'unknown'}。"
            f" 待处理通知 {len(notifications)} 条，待确认 decision {len(pending_decisions)} 条，运行中的 jobs {len(running_jobs)} 条。"
        )
        return summary, response

    def _execute_system_ops_answer(self, user_request: str) -> SkillResult:
        snapshot = self._build_system_ops_snapshot()
        summary, response_text = self._render_system_ops_answer(user_request, snapshot)
        return SkillResult(
            skill_name="system_ops",
            success=True,
            summary=summary,
            structured={
                "active_mode": snapshot.get("active_mode", ""),
                "provider_id": snapshot.get("provider_id", ""),
                "model": snapshot.get("model", ""),
                "active_fingerprint": snapshot.get("active_fingerprint", ""),
                "vector_backend": snapshot.get("vector_backend", ""),
                "notification_count": len(snapshot.get("notifications") or []),
                "pending_decision_count": len(snapshot.get("pending_decisions") or []),
                "running_job_count": len(snapshot.get("running_jobs") or []),
            },
            response_text=response_text,
        )

    def _execute_direct_answer(
        self,
        *,
        route_name: str,
        user_request: str,
        attachments: list[str],
        memory_prompt: str,
    ) -> SkillResult:
        if route_name == "knowledge_lookup" and "[Retrieved Context]" not in memory_prompt:
            return SkillResult(
                skill_name="knowledge_lookup",
                success=False,
                summary="当前本地知识里没有足够明确的历史上下文。",
                response_text="我已经按本地知识检索过了，但当前没有足够明确的历史上下文可直接确认。接下来我会先尝试从 Fairy 当前系统状态补充判断。",
            )
        guidance = {
            "direct_answer": build_direct_answer_route_instructions("direct_answer"),
            "knowledge_lookup": build_direct_answer_route_instructions("knowledge_lookup"),
            "system_ops": build_direct_answer_route_instructions("system_ops"),
        }.get(route_name, build_direct_answer_route_instructions(route_name))
        system_prompt = guidance
        if memory_prompt.strip():
            system_prompt += f"\n\n{memory_prompt.strip()}"

        # Apply legacy prompt disabler to remove behavioral patterns
        system_prompt = LegacyPromptDisabler.filter_prompt(system_prompt, route_name)
        response_text = self._execute_text_response(
            system_prompt,
            user_request,
            attachment_paths=attachments,
            max_tokens=320,
            temperature=0.25,
            instruction_label="Route instructions",
        )
        success = not self._looks_like_model_error(response_text)
        return SkillResult(
            skill_name=route_name,
            success=success,
            summary=response_text[:240],
            response_text=response_text,
        )

    def _execute_route_candidate(
        self,
        candidate: RouteCandidate,
        *,
        user_request: str,
        attachments: list[str],
        memory_prompt: str,
    ) -> SkillResult:
        if candidate.name in {"direct_answer", "knowledge_lookup"}:
            return self._execute_direct_answer(
                route_name=candidate.name,
                user_request=user_request,
                attachments=attachments,
                memory_prompt=memory_prompt,
            )
        if candidate.name == "system_ops":
            return self._execute_system_ops_answer(user_request)

        skill_name = candidate.skill_name
        if not skill_name or skill_name not in self.skills:
            return SkillResult(
                skill_name=candidate.name,
                success=False,
                summary="当前候选路径未找到可执行 skill。",
                response_text="请求已接收，但当前候选路径未找到可执行 skill。",
            )

        skill = self.skills[skill_name]
        logger.info("chosen_skill=%s route_name=%s", skill_name, candidate.name)
        if skill_name == AgentShellSkill.SPEC.name:
            return skill.execute(user_request, candidate.allowed_tools, memory_context=memory_prompt)
        if skill_name == DocumentEditorSkill.SPEC.name:
            return skill.execute(user_request, candidate.allowed_tools, attachment_paths=attachments, memory_context=memory_prompt)
        return skill.execute(user_request, candidate.allowed_tools, memory_context=memory_prompt)

    @staticmethod
    def _run_candidate_executor(
        candidates: list[RouteCandidate],
        executor: Callable[[RouteCandidate], SkillResult],
        usefulness_checker: Callable[[str, SkillResult], bool],
    ) -> tuple[RouteCandidate, SkillResult, bool, str, str]:
        fallback_used = False
        fallback_reason = ""
        resolution_path: list[str] = []
        last_candidate = candidates[0]
        last_result = executor(last_candidate)
        resolution_path.append(last_candidate.name)
        if usefulness_checker(last_candidate.name, last_result):
            return last_candidate, last_result, fallback_used, fallback_reason, " -> ".join(resolution_path)

        if not last_result.success:
            fallback_reason = "tool_failed"
        else:
            fallback_reason = "empty_or_weak_result"

        for candidate in candidates[1:]:
            fallback_used = True
            last_candidate = candidate
            last_result = executor(candidate)
            resolution_path.append(candidate.name)
            if usefulness_checker(candidate.name, last_result):
                return last_candidate, last_result, fallback_used, fallback_reason, " -> ".join(resolution_path)
            if not last_result.success:
                fallback_reason = "tool_failed"
            else:
                fallback_reason = "empty_or_weak_result"
        return last_candidate, last_result, fallback_used, fallback_reason, " -> ".join(resolution_path)

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

        if self._should_use_game_mode_direct_answer(active_mode):
            result = self._execute_game_mode_direct_answer(
                task_id,
                user_request,
                attachments,
                combined_memory_prompt,
            )
            self._finalize_task(task_id, user_request, result, task_category=task_category)
            return result

        # --- Lazy Skill Runtime dual-path dispatch (feature-flagged) ---
        if lazy_skills_flags.use_lazy_skills and self._lazy_dispatcher is not None:
            self._emit_event("lazy_pipeline_entered", {"pipeline": "new"})
            try:
                lazy_result = self._lazy_dispatcher.dispatch(
                    user_request,
                    attachments=attachments,
                    memory_prompt=combined_memory_prompt,
                    route_context=route_context,
                    legacy_skills=self.skills,
                    request_origin=request_origin,
                    request_id=request_id,
                )
                if lazy_result is not None:
                    # New pipeline succeeded — apply persona styling and finalize
                    lazy_result.structured.setdefault("pipeline", "new")
                    lazy_result.structured.setdefault("request_origin", request_origin)
                    lazy_result.structured.setdefault("request_id", request_id)

                    # NEW: Skip persona styling for tool-locked results (realtime factual data)
                    if lazy_result.tool_lock:
                        logger.info("tool_lock_active skill=%s reason=realtime_factual_data", lazy_result.skill_name)
                        self._emit_event("tool_lock_applied", {
                            "skill": lazy_result.skill_name,
                            "reason": "realtime_factual_data",
                            "response_text": lazy_result.response_text[:100] if lazy_result.response_text else "",
                        })
                        # Apply strict deterministic mode for realtime skills
                        card_type = lazy_result.structured.get("card_type") if lazy_result.structured else None
                        if StrictDeterministicMode.should_use_strict_mode(lazy_result.skill_name, card_type):
                            lazy_result.structured = StrictDeterministicMode.apply_strict_mode(lazy_result.structured or {})
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
                else:
                    # New pipeline returned None — fall through to legacy
                    logger.info("lazy_pipeline_fallback_to_legacy reason=dispatch_returned_none")
                    self._emit_event("lazy_pipeline_fallback", {"reason": "dispatch_returned_none", "pipeline": "legacy"})
            except Exception:
                logger.exception("lazy_pipeline_exception — falling back to legacy")
                self._emit_event("lazy_pipeline_fallback", {"reason": "exception", "pipeline": "legacy"})

        # --- Legacy pipeline continues below (unchanged) ---
        self.llm_helper.set_memory_context(combined_memory_prompt)
        llm_decision = self._request_llm_route_decision(user_request, attachments, route_context, combined_memory_prompt)
        routing = self.router.decide(
            user_request,
            attachment_paths=attachments,
            context=route_context,
            llm_decision=llm_decision,
        )
        route_candidates = self.router.build_attempt_sequence(routing)
        selected_candidate = route_candidates[0]
        routing_payload = self._build_routing_debug_payload(routing)
        logger.info(
            "routing_decision primary_intent=%s tool_needed=%s selected_tool=%s candidates=%s",
            routing.primary_intent,
            routing.tool_needed,
            routing.selected_tool,
            ",".join(f"{candidate.name}:{candidate.score:.2f}" for candidate in routing.candidates),
        )
        self._emit_event("routing_decision", routing_payload)

        if routing.primary_intent == "knowledge_lookup" and not rag_prompt:
            self._emit_event("rag_retrieval_started", {"query": user_request, "top_k": rag_settings.rag_top_k})
            rag_result = self.rag.retrieve_context(
                user_request,
                top_k=rag_settings.rag_top_k,
                settings=rag_settings,
            )
            source_kinds = sorted({item.source_kind for item in rag_result.chunks})
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
            if rag_settings.enable_retrieval_debug_panel:
                self._emit_event("rag_retrieval_visualized", dict(rag_result.debug_snapshot or {}))
                self._emit_event(
                    "injected_context_preview_generated",
                    {
                        "fingerprint": str(rag_result.debug_snapshot.get("embedding_fingerprint", "") or ""),
                        "provider": str(rag_result.debug_snapshot.get("embedding_provider", "") or ""),
                        "collection_name": str(rag_result.debug_snapshot.get("collection_name", "") or ""),
                        "progress": f"{rag_result.debug_snapshot.get('injected_context_item_count', 0)} items / {rag_result.debug_snapshot.get('injected_context_chars', 0)} chars",
                    },
                )

        memory_bundle, memory_prompt, memory_counts, task_category = self.memory.prepare_memory_context(
            user_request=user_request,
            project=self.project_name,
            task_id=task_id,
            attachments=attachments,
            chosen_skill=self._route_name_for_memory(selected_candidate),
        )
        combined_memory_prompt = "\n\n".join(part for part in (memory_prompt.strip(), rag_prompt.strip()) if part).strip()
        self.llm_helper.set_memory_context(combined_memory_prompt)
        if persona_mode != "off":
            persona = self._get_persona_engine()
            persona_task_type = persona.classify_task_type(
                user_request,
                chosen_skill=self._route_name_for_memory(selected_candidate),
                task_category=task_category,
            )
        else:
            persona_task_type = self._fallback_task_type(
                user_request,
                chosen_skill=self._route_name_for_memory(selected_candidate),
                task_category=task_category,
            )
        self._active_done_steps.append(f"route:{selected_candidate.name}")
        self.memory.update_task(
            task_id,
            user_request,
            current_step=selected_candidate.name,
            done_steps=list(self._active_done_steps),
            status="running",
        )
        logger.info(
            "skill_routed chosen_skill=%s allowed_tools=%s llm_intent=%s llm_confidence=%s",
            selected_candidate.name,
            selected_candidate.allowed_tools,
            llm_decision.primary_intent if llm_decision else "",
            llm_decision.confidence if llm_decision else "",
        )
        self._emit_event(
            "skill_routed",
            {
                "chosen_skill": selected_candidate.name,
                "reason": selected_candidate.reason,
                "allowed_tools": selected_candidate.allowed_tools,
                "llm_choice": llm_decision.primary_intent if llm_decision else "",
                "primary_intent": routing.primary_intent,
                "tool_needed": routing.tool_needed,
                "candidates": routing_payload["candidates"],
            },
        )
        self._emit_event(
            "route_selected",
            {"route": selected_candidate.name, "reason": f"primary_intent={routing.primary_intent}"},
        )
        final_candidate, result, fallback_used, fallback_reason, resolution_path = self._run_candidate_executor(
            route_candidates,
            executor=lambda candidate: self._execute_route_candidate(
                candidate,
                user_request=user_request,
                attachments=attachments,
                memory_prompt=combined_memory_prompt,
            ),
            usefulness_checker=self._is_result_useful,
        )
        if fallback_used:
            self._emit_event(
                "route_fallback_used",
                {
                    "from_route": selected_candidate.name,
                    "to_route": final_candidate.name,
                    "reason": fallback_reason,
                    "final_resolution_path": resolution_path,
                },
            )

        result.structured.setdefault("routing", {})
        result.structured["routing"] = {
            "primary_intent": routing.primary_intent,
            "tool_needed": routing.tool_needed,
            "selected_tool": selected_candidate.name,
            "final_route": final_candidate.name,
            "fallback_used": fallback_used,
            "fallback_reason": fallback_reason,
            "final_resolution_path": resolution_path,
            "candidates": routing_payload["candidates"],
        }
        result.structured.setdefault("pipeline", "legacy")
        result.structured.setdefault("request_origin", request_origin)
        result.structured.setdefault("request_id", request_id)

        if persona_mode == "full":
            styled_text, guard_report = self._get_persona_engine().style_response(
                result.response_text or result.summary,
                task_type=persona_task_type,
                user_input=user_request,
                user_profile=memory_bundle.profile,
                recent_summary=combined_memory_prompt,
            )
            if styled_text:
                result.response_text = styled_text
            logger.info(
                "persona_guard_applied task_type=%s verdict=%s cute=%s customer_service=%s toxicity=%s modifications=%s",
                persona_task_type,
                guard_report.verdict_present,
                guard_report.cute_score,
                guard_report.customer_service_score,
                guard_report.toxicity_score,
                ",".join(guard_report.modifications),
            )

        self._finalize_task(task_id, user_request, result, task_category=task_category)
        return result

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

    def _request_llm_route_decision(
        self,
        user_request: str,
        attachments: list[str],
        route_context: RouteContext | None,
        memory_prompt: str = "",
    ) -> LLMRouteDecision | None:
        payload = {
            "user_request": user_request,
            "attachment_paths": attachments,
            "previous_skill": route_context.previous_skill if route_context else "",
            "screen_followup_remaining": route_context.screen_followup_remaining if route_context else 0,
            "has_screen_summary": bool((route_context.previous_structured or {}).get("screen_summary")) if route_context else False,
            "available_routes": [
                "direct_answer",
                "weather",
                "news",
                "web_search",
                "knowledge_lookup",
                "system_ops",
                "document_editor",
                "screen_understanding",
                "agent_shell",
            ],
            "relevant_memory": memory_prompt,
        }
        response = self.llm.execute_task(
            ROUTER_ORCHESTRATOR_PROMPT,
            json.dumps(payload, ensure_ascii=False),
            max_tokens=260,
            temperature=0.0,
            instruction_label="Router instructions",
        )
        parsed = self._parse_json_object(response.text)
        if not parsed:
            return None
        primary_intent = str(parsed.get("primary_intent", "") or "").strip()
        if primary_intent not in {
            "direct_answer",
            "general_chat",
            "realtime_info",
            "weather",
            "news",
            "knowledge_lookup",
            "system_ops",
            "local_action",
            "ambiguous",
        }:
            return None
        reason = str(parsed.get("reason", "") or "").strip()
        try:
            confidence = float(parsed.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        tool_needed = bool(parsed.get("tool_needed", False))
        clarification_needed = bool(parsed.get("clarification_needed", False))
        raw_candidates = parsed.get("candidates")
        candidate_scores: dict[str, float] = {}
        if isinstance(raw_candidates, list):
            for item in raw_candidates:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "") or "").strip()
                if name not in {
                    "direct_answer",
                    "weather",
                    "news",
                    "web_search",
                    "knowledge_lookup",
                    "system_ops",
                    "document_editor",
                    "screen_understanding",
                    "agent_shell",
                }:
                    continue
                try:
                    score = float(item.get("score", 0.0) or 0.0)
                except (TypeError, ValueError):
                    score = 0.0
                candidate_scores[name] = max(0.0, min(1.0, score))
        return LLMRouteDecision(
            primary_intent=primary_intent,
            reason=reason,
            confidence=confidence,
            tool_needed=tool_needed,
            clarification_needed=clarification_needed,
            candidate_scores=candidate_scores,
        )

    def _parse_json_object(self, text: str) -> dict[str, Any] | None:
        text = text.strip()
        if not text:
            return None
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else None
        except Exception:
            pass
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
            return data if isinstance(data, dict) else None
        except Exception:
            return None
