from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Any, Callable

from app.lazy_skill_router import LazyRouteContext, LazyRouteDecision, LazySkillRouter, SkillLazyLoader
from app.lazy_runtime.context_builder import ContextBuilder
from app.lazy_runtime.tool_exposure_broker import ToolExposureBroker
from app.models.skill_result import SkillResult
from app.skills.bundles.runtime_types import BundleRuntimeServices


logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).resolve().parent.parent.parent
_SKILLS_ROOT = _BASE_DIR / "app" / "skills" / "bundles"
_REGISTRY_PATH = _BASE_DIR / "app" / "lazy_skill_router" / "skill_registry.json"


class LazyDispatcher:
    """Canonical Anthropic-style bundle dispatcher."""

    def __init__(
        self,
        llm_client: Any,
        tool_registry: Any,
        *,
        services: BundleRuntimeServices | None = None,
        event_callback: Callable[[str, dict[str, Any]], None] | None = None,
        skills_root: str | Path | None = None,
        registry_path: str | Path | None = None,
    ) -> None:
        self._llm = llm_client
        self._emit_event = event_callback or (lambda _name, _payload: None)
        self._services = services or BundleRuntimeServices(llm=llm_client)
        self._services.event_callback = self._emit_event

        sr = Path(skills_root) if skills_root else _SKILLS_ROOT
        rp = Path(registry_path) if registry_path else _REGISTRY_PATH
        self._loader = SkillLazyLoader(sr, rp)
        self._router = LazySkillRouter(self._loader, llm=llm_client)
        self._context_builder = ContextBuilder()
        self._broker = ToolExposureBroker()
        self._broker.sync_from_tool_registry(tool_registry)

        logger.info(
            "lazy_dispatcher_init skills_root=%s registry=%s known_tools=%d",
            sr,
            rp,
            len(self._broker._known_tools),
        )

    def dispatch(
        self,
        user_request: str,
        *,
        attachments: list[str] | None = None,
        memory_prompt: str = "",
        route_context: Any | None = None,
        request_origin: str | None = None,
        request_id: str | None = None,
    ) -> SkillResult:
        attachments = attachments or []
        request_origin = request_origin or "main_chat"
        request_id = request_id or ""

        lazy_ctx = self._convert_route_context(route_context)
        self._emit_event(
            "lazy_routing_started",
            {
                "user_request": user_request[:200],
                "request_origin": request_origin,
                "request_id": request_id,
            },
        )
        forced_bundle = str(lazy_ctx.forced_bundle or "").strip()
        if forced_bundle:
            decision = self._force_bundle_decision(forced_bundle)
        else:
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
                return self._execute_direct_answer(
                    user_request,
                    memory_prompt,
                    attachments,
                    request_origin=request_origin,
                    request_id=request_id,
                )

        self._emit_event(
            "lazy_routing_completed",
            {
                "skill": decision.skill_name,
                "confidence": round(decision.confidence, 3),
                "reason": decision.reason,
                "is_direct_answer": decision.is_direct_answer,
                "has_bundle": decision.bundle is not None,
                "pipeline": "bundle_runtime",
                "request_origin": request_origin,
                "request_id": request_id,
            },
        )

        if decision.is_direct_answer:
            return self._execute_direct_answer(
                user_request,
                memory_prompt,
                attachments,
                request_origin=request_origin,
                request_id=request_id,
            )

        if decision.bundle is None:
            logger.warning("lazy_dispatch no_bundle skill=%s", decision.skill_name)
            return SkillResult(
                skill_name=decision.skill_name or "bundle-runtime-error",
                success=False,
                summary="Bundle route resolution failed.",
                response_text="请求已接收，但当前 bundle 路由没有得到可执行结果。",
                structured={
                    "pipeline": "bundle_runtime",
                    "error": "bundle_resolution_failed",
                    "request_origin": request_origin,
                    "request_id": request_id,
                },
            )

        result = self._execute_bundle(
            decision,
            user_request=user_request,
            attachments=attachments,
            memory_prompt=memory_prompt,
            route_context=route_context,
            request_origin=request_origin,
            request_id=request_id,
        )
        return result or SkillResult(
            skill_name=decision.skill_name or "bundle-runtime-error",
            success=False,
            summary="Bundle runtime unavailable.",
            response_text="请求已接收，但当前 bundle runtime 暂时不可用。",
            structured={
                "pipeline": "bundle_runtime",
                "error": "bundle_runtime_unavailable",
                "request_origin": request_origin,
                "request_id": request_id,
            },
        )

    def _execute_direct_answer(
        self,
        user_request: str,
        memory_prompt: str,
        attachments: list[str],
        *,
        request_origin: str,
        request_id: str,
    ) -> SkillResult:
        self._broker.deactivate()
        ctx = self._context_builder.build_direct_answer(user_request, memory_prompt=memory_prompt)
        self._emit_event(
            "lazy_context_built",
            {
                "skill": "direct-answer",
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
                instruction_label="Bundle direct answer",
            )
            text = (response.text or "").strip()
        except Exception:
            logger.exception("bundle_direct_answer_failed")
            return SkillResult(
                skill_name="direct-answer",
                success=False,
                summary="Direct answer execution failed.",
                response_text="",
                structured={
                    "pipeline": "bundle_runtime",
                    "request_origin": request_origin,
                    "request_id": request_id,
                },
            )

        return SkillResult(
            skill_name="direct-answer",
            success=bool(text),
            summary=text[:220] if text else "No direct answer available.",
            response_text=text or "请求已接收，但当前没有可直接返回的结果。",
            structured={
                "pipeline": "bundle_runtime",
                "token_breakdown": ctx.context_token_breakdown,
                "request_origin": request_origin,
                "request_id": request_id,
            },
        )

    def _execute_bundle(
        self,
        decision: LazyRouteDecision,
        *,
        user_request: str,
        attachments: list[str],
        memory_prompt: str,
        route_context: Any | None,
        request_origin: str,
        request_id: str,
    ) -> SkillResult | None:
        bundle = decision.bundle
        assert bundle is not None

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

        runtime_runner = self._load_bundle_runtime(bundle.name)
        if runtime_runner is None:
            self._broker.deactivate()
            logger.warning("bundle_runtime_not_found bundle=%s", bundle.name)
            return None

        try:
            result = runtime_runner(
                user_request=user_request,
                allowed_tools=self._broker.get_exposed_tool_names(),
                attachments=attachments,
                memory_context=memory_prompt,
                bundle=bundle,
                prompt_context=ctx,
                services=self._services,
                route_context=route_context,
                request_origin=request_origin,
                request_id=request_id,
            )
        except Exception:
            logger.exception("bundle_runtime_execute_failed bundle=%s", bundle.name)
            self._broker.deactivate()
            return SkillResult(
                skill_name=bundle.name,
                success=False,
                summary=f"Bundle runtime execution failed: {bundle.name}",
                response_text="",
                structured={
                    "pipeline": "bundle_runtime",
                    "error": "execution_exception",
                    "bundle_name": bundle.name,
                    "request_origin": request_origin,
                    "request_id": request_id,
                },
            )

        result.structured = result.structured or {}
        result.structured["pipeline"] = "bundle_runtime"
        result.structured["bundle_name"] = bundle.name
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

    @staticmethod
    def _load_bundle_runtime(bundle_name: str) -> Callable[..., SkillResult] | None:
        module_name = f"app.skills.bundles.{bundle_name.replace('-', '_')}.runtime"
        try:
            module = importlib.import_module(module_name)
        except Exception:
            logger.exception("bundle_runtime_import_failed module=%s", module_name)
            return None
        runner = getattr(module, "run_bundle", None)
        return runner if callable(runner) else None

    @staticmethod
    def _convert_route_context(route_context: Any) -> LazyRouteContext:
        if route_context is None:
            return LazyRouteContext()
        return LazyRouteContext(
            previous_skill=getattr(route_context, "previous_skill", "") or "",
            screen_followup_remaining=getattr(route_context, "screen_followup_remaining", 0) or 0,
            previous_structured=getattr(route_context, "previous_structured", {}) or {},
            forced_bundle=getattr(route_context, "forced_bundle", "") or "",
            perception_intent=getattr(route_context, "perception_intent", "") or "",
            web_task_type=getattr(route_context, "web_task_type", "") or "",
            web_intent_plan=getattr(route_context, "web_intent_plan", {}) or {},
            preferred_routes=getattr(route_context, "preferred_routes", []) or [],
        )

    def _force_bundle_decision(self, bundle_name: str) -> LazyRouteDecision:
        bundle = self._loader.load_bundle(bundle_name)
        if bundle is None:
            logger.warning("lazy_route_forced_bundle_missing bundle=%s", bundle_name)
            return LazyRouteDecision(
                skill_name="direct-answer",
                confidence=0.2,
                reason=f"forced_bundle_missing:{bundle_name}",
                is_direct_answer=True,
            )
        return LazyRouteDecision(
            skill_name=bundle_name,
            confidence=1.0,
            reason=f"forced_bundle:{bundle_name}",
            is_direct_answer=False,
            bundle=bundle,
        )
