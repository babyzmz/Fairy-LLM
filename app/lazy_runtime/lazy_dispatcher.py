"""
Fairy Lazy Dispatcher
=====================

The dual-path dispatcher that integrates the new Anthropic-style lazy skill
runtime into the existing FairyCore request handling chain.

This module provides:
  1. ``LazyDispatcher`` – initialized once per FairyCore, owns the lazy loader,
     router, context builder, and tool exposure broker.
  2. ``dispatch()`` – called from ``FairyCore.handle_request()`` when the feature
     flag is enabled. Returns a ``SkillResult`` or ``None`` (signaling fallback
     to the legacy pipeline).

Integration point in fairy_core.py:
    from app.lazy_runtime.lazy_dispatcher import LazyDispatcher
    # In __init__:
    self._lazy_dispatcher = LazyDispatcher(self.llm, self.tools, ...)
    # In handle_request:
    if lazy_skills_flags.use_lazy_skills:
        result = self._lazy_dispatcher.dispatch(user_request, ...)
        if result is not None:
            return result  # new pipeline succeeded
        # else: fall through to legacy pipeline
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from app.lazy_skill_router import (
    LazyRouteContext,
    LazyRouteDecision,
    LazySkillRouter,
    SkillBundle,
    SkillLazyLoader,
)
from app.lazy_runtime.context_builder import ContextBuilder, PromptContext
from app.lazy_runtime.tool_exposure_broker import ToolExposureBroker
from app.models.skill_result import SkillResult

logger = logging.getLogger(__name__)

# Base directory of the project
_BASE_DIR = Path(__file__).resolve().parent.parent.parent
_SKILLS_ROOT = _BASE_DIR / "app" / "skills" / "bundles"
_REGISTRY_PATH = _BASE_DIR / "app" / "lazy_skill_router" / "skill_registry.json"


class LazyDispatcher:
    """Owns the full lazy-skill pipeline and provides a single ``dispatch()`` entry point."""

    def __init__(
        self,
        llm_client: Any,
        tool_registry: Any,
        *,
        event_callback: Callable[[str, dict[str, Any]], None] | None = None,
        skills_root: str | Path | None = None,
        registry_path: str | Path | None = None,
    ) -> None:
        self._llm = llm_client
        self._tool_registry = tool_registry
        self._emit_event = event_callback or (lambda name, payload: None)

        # Initialize the lazy loader
        sr = Path(skills_root) if skills_root else _SKILLS_ROOT
        rp = Path(registry_path) if registry_path else _REGISTRY_PATH
        self._loader = SkillLazyLoader(sr, rp)

        # Initialize the router (passes the LLM for Stage-1b)
        self._router = LazySkillRouter(self._loader, llm=llm_client)

        # Initialize the context builder
        self._context_builder = ContextBuilder()

        # Initialize the tool exposure broker
        self._broker = ToolExposureBroker()
        self._broker.sync_from_tool_registry(tool_registry)

        logger.info(
            "lazy_dispatcher_init skills_root=%s registry=%s known_tools=%d",
            sr,
            rp,
            len(self._broker._known_tools),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def dispatch(
        self,
        user_request: str,
        *,
        attachments: list[str] | None = None,
        memory_prompt: str = "",
        route_context: Any | None = None,
        legacy_skills: dict[str, Any] | None = None,
        request_origin: str | None = None,
        request_id: str | None = None,
    ) -> SkillResult | None:
        """Run the full lazy-skill pipeline.

        Returns a ``SkillResult`` on success, or ``None`` to signal that the
        caller should fall back to the legacy pipeline.

        Parameters
        ----------
        user_request : str
            The user's current message.
        attachments : list[str] | None
            File paths attached to the request.
        memory_prompt : str
            Combined memory + RAG context string.
        route_context : Any | None
            Legacy RouteContext for continuity (converted to LazyRouteContext).
        legacy_skills : dict[str, Any] | None
            The old ``self.skills`` dict from FairyCore, used to execute the
            actual skill logic when a bundle is matched.
        request_origin : str | None
            Origin of the request (e.g., "main_chat", "desktop_pet", "floating_fairy").
        request_id : str | None
            Unique request identifier for tracking through the pipeline.
        """
        attachments = attachments or []
        legacy_skills = legacy_skills or {}
        request_origin = request_origin or "main_chat"
        request_id = request_id or ""

        # Convert legacy RouteContext to LazyRouteContext
        lazy_ctx = self._convert_route_context(route_context)

        # ── Stage 1+2: Route and load bundle ──────────────────────
        self._emit_event(
            "lazy_routing_started",
            {
                "user_request": user_request[:200],
                "request_origin": request_origin,
                "request_id": request_id,
            },
        )

        try:
            decision = self._router.route(
                user_request,
                attachments=attachments,
                context=lazy_ctx,
                memory_prompt=memory_prompt,
            )
        except Exception:
            logger.exception("lazy_routing_failed")
            self._emit_event("lazy_routing_failed", {"error": "routing_exception"})
            return None  # Signal fallback to legacy

        self._emit_event(
            "lazy_routing_completed",
            {
                "skill": decision.skill_name,
                "confidence": round(decision.confidence, 3),
                "reason": decision.reason,
                "is_direct_answer": decision.is_direct_answer,
                "has_bundle": decision.bundle is not None,
                "pipeline": "new",
                "request_origin": request_origin,
                "request_id": request_id,
            },
        )

        # ── Direct answer path ────────────────────────────────────
        if decision.is_direct_answer:
            return self._execute_direct_answer(
                user_request,
                memory_prompt,
                attachments,
                request_origin=request_origin,
                request_id=request_id,
            )

        # ── Skill execution path ──────────────────────────────────
        if decision.bundle is None:
            logger.warning("lazy_dispatch no_bundle skill=%s", decision.skill_name)
            return None  # Fallback to legacy

        return self._execute_skill(
            decision,
            user_request=user_request,
            attachments=attachments,
            memory_prompt=memory_prompt,
            legacy_skills=legacy_skills,
            request_origin=request_origin,
            request_id=request_id,
        )

    # ------------------------------------------------------------------
    # Direct answer execution
    # ------------------------------------------------------------------

    def _execute_direct_answer(
        self,
        user_request: str,
        memory_prompt: str,
        attachments: list[str],
        request_origin: str = "main_chat",
        request_id: str = "",
    ) -> SkillResult:
        """Execute the direct-answer path using the context builder."""
        self._broker.deactivate()

        ctx = self._context_builder.build_direct_answer(user_request, memory_prompt=memory_prompt)

        self._emit_event(
            "lazy_context_built",
            {
                "skill": "direct_answer",
                "token_breakdown": ctx.context_token_breakdown,
                "tools": [],
                "request_origin": request_origin,
                "request_id": request_id,
            },
        )

        try:
            response = self._llm.execute_task(
                ctx.system_prompt,
                ctx.user_message,
                attachment_paths=attachments,
                max_tokens=512,
                temperature=0.3,
                instruction_label="Lazy direct answer",
            )
            text = (response.text or "").strip()
        except Exception:
            logger.exception("lazy_direct_answer_failed")
            return SkillResult(
                skill_name="lazy_direct_answer",
                success=False,
                summary="新管线直接回答失败。",
                response_text="",
                structured={"pipeline": "new", "request_origin": request_origin, "request_id": request_id},
            )

        return SkillResult(
            skill_name="lazy_direct_answer",
            success=bool(text),
            summary=text[:220] if text else "无回答内容。",
            response_text=text or "请求已接收，但当前没有可返回的结果。",
            structured={
                "pipeline": "new",
                "token_breakdown": ctx.context_token_breakdown,
                "request_origin": request_origin,
                "request_id": request_id,
            },
        )

    # ------------------------------------------------------------------
    # Skill execution via legacy skill objects
    # ------------------------------------------------------------------

    def _execute_skill(
        self,
        decision: LazyRouteDecision,
        *,
        user_request: str,
        attachments: list[str],
        memory_prompt: str,
        legacy_skills: dict[str, Any],
        request_origin: str = "main_chat",
        request_id: str = "",
    ) -> SkillResult | None:
        """Execute a skill using the new context but the legacy skill executor.

        The bridge works by:
          1. Building the context from the lazy bundle
          2. Activating the tool exposure broker
          3. Mapping the bundle name to the legacy skill name
          4. Executing via the legacy skill's ``.execute()`` method
        """
        bundle = decision.bundle
        assert bundle is not None

        # Activate tool exposure
        exposure = self._broker.activate_skill(bundle.name, bundle.allowed_tools)
        self._emit_event(
            "lazy_tool_exposure_activated",
            {
                "skill": bundle.name,
                "allowed": exposure.allowed_tools,
                "exposed": exposure.exposed_tools,
                "missing": exposure.missing_tools,
                "request_origin": request_origin,
                "request_id": request_id,
            },
        )

        # Build context
        ctx = self._context_builder.build(
            bundle,
            user_request,
            memory_prompt=memory_prompt,
            attachment_paths=attachments,
        )
        self._emit_event(
            "lazy_context_built",
            {
                "skill": bundle.name,
                "token_breakdown": ctx.context_token_breakdown,
                "tools": ctx.allowed_tools,
                "request_origin": request_origin,
                "request_id": request_id,
            },
        )

        # ── Realtime-lookup short-path execution ──────────────────────
        if bundle.name == "realtime-lookup":
            try:
                # Canonical agent — app.agents.realtime_lookup
                from app.agents.realtime_lookup.agent import RealtimeLookupAgent
                from app.agents.realtime_lookup.models import RealtimeLookupRequest

                search_tool = self._broker.get_tool("search_web")
                if search_tool is None:
                    logger.warning("realtime_lookup search_web_not_available")
                    self._broker.deactivate()
                    return None

                fetch_page = self._broker.get_tool("fetch_page")
                vision_fallback = self._broker.get_tool("vision")

                agent = RealtimeLookupAgent(
                    search_tool=search_tool,
                    fetch_page=fetch_page,
                    vision_fallback=vision_fallback,
                )
                request = RealtimeLookupRequest(
                    query=user_request,
                    allowed_tools=self._broker.get_exposed_tool_names(),
                    strict_mode=True,
                    request_id=request_id,
                )
                response = agent.execute(request)

                if response.success:
                    self._broker.deactivate()
                    return SkillResult(
                        skill_name="realtime-lookup",
                        success=True,
                        summary=response.speech_text,
                        response_text=response.speech_text,
                        tool_lock=True,
                        structured={
                            **response.to_dict(),
                            "request_origin": request_origin,
                            "request_id": request_id,
                            "pipeline": "new",
                            "strict_mode": True,
                        },
                    )
                # non-success: fall through to legacy
            except Exception:
                logger.exception("realtime_lookup_execution_failed")
                # fall through to legacy skill execution

        # Map bundle name → legacy skill name
        legacy_name = self._map_to_legacy_skill(bundle.name)
        if legacy_name not in legacy_skills:
            logger.warning(
                "lazy_dispatch legacy_skill_not_found bundle=%s mapped=%s available=%s",
                bundle.name,
                legacy_name,
                list(legacy_skills.keys()),
            )
            self._broker.deactivate()
            return None  # Fallback to legacy pipeline

        skill = legacy_skills[legacy_name]
        logger.info("lazy_dispatch executing legacy_skill=%s via bundle=%s", legacy_name, bundle.name)

        try:
            # Use the exposed tools list from the broker instead of the legacy candidate's list
            allowed_tools = self._broker.get_exposed_tool_names()
            result = skill.execute(
                user_request,
                allowed_tools,
                memory_context=memory_prompt,
                **({"attachment_paths": attachments} if "document" in legacy_name.lower() else {}),
            )
        except Exception:
            logger.exception("lazy_skill_execution_failed skill=%s", legacy_name)
            self._broker.deactivate()
            return SkillResult(
                skill_name=legacy_name,
                success=False,
                summary=f"新管线技能执行失败: {bundle.name}",
                response_text="",
                structured={
                    "pipeline": "new",
                    "error": "execution_exception",
                    "request_origin": request_origin,
                    "request_id": request_id,
                },
            )

        # Enrich result with pipeline metadata
        result.structured = result.structured or {}
        result.structured["pipeline"] = "new"
        result.structured["lazy_bundle"] = bundle.name
        result.structured["token_breakdown"] = ctx.context_token_breakdown
        result.structured["exposure_summary"] = self._broker.get_exposure_summary()
        result.structured["request_origin"] = request_origin
        result.structured["request_id"] = request_id

        blocked = self._broker.get_blocked_calls()
        if blocked:
            self._emit_event(
                "lazy_tool_calls_blocked",
                {
                    "skill": bundle.name,
                    "blocked": blocked,
                    "request_origin": request_origin,
                    "request_id": request_id,
                },
            )

        self._broker.deactivate()
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _map_to_legacy_skill(bundle_name: str) -> str:
        """Map a bundle name (e.g. 'web-research') to a legacy skill name (e.g. 'web_research_skill')."""
        mapping = {
            "web-research": "web_research_skill",
            "document-editing": "document_editor_skill",
            "screen-understanding": "screen_understanding_skill",
            "terminal-agent": "agent_shell_skill",
            "news-intelligence": "news_intelligence_skill",
        }
        return mapping.get(bundle_name, bundle_name.replace("-", "_") + "_skill")

    @staticmethod
    def _convert_route_context(route_context: Any) -> LazyRouteContext:
        """Convert a legacy RouteContext to a LazyRouteContext."""
        if route_context is None:
            return LazyRouteContext()
        return LazyRouteContext(
            previous_skill=getattr(route_context, "previous_skill", "") or "",
            screen_followup_remaining=getattr(route_context, "screen_followup_remaining", 0) or 0,
            previous_structured=getattr(route_context, "previous_structured", {}) or {},
        )
