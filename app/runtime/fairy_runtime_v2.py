from __future__ import annotations

import concurrent.futures
import logging
import os
import queue
import re
import threading
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Callable, Iterable
from app.agent.perception import EntityExtractor, FollowUpResolver, InputNormalizer, IntentClassifier
from app.context import ContextManager
from app.core.perception import ModalityDetector
from app.core.perception.perception_models import DetectedEntity, PerceptionFrame
from app.core.query_resolution import QueryResolver
from app.core.response import PlannedResponse, ResponsePlanner
from app.debug import RendererTrace, ResponseTrace, SchemaTrace, SpeechTrace, StateTrace, ToolTrace
from app.response import ResponsePipeline
from app.response.orchestration_summary import build_orchestration_summary_card
from app.response.models import (
    CardPayload,
    CardStreamEvent,
    ErrorEvent,
    MessageEndEvent,
    MessageStartEvent,
    NormalizedAssistantResponse,
    ProgressStreamEvent,
    ResponseProgressEvent,
    ResponseRequestPlan,
    SpeechPayload,
    StreamEventBase,
    TextDeltaEvent,
)
from app.schemas import SchemaValidator
from app.speech import SpeechPlanner, SpeechRouter
from app.state import ActivityTracker, AssistantStateMachine, ConcurrentTaskController
from app.runtime.orchestration import (
    ExecutionPlan as RuntimeExecutionPlan,
    ExecutionPlanState as RuntimeExecutionPlanState,
    ExecutionStep as RuntimeExecutionStep,
    PlanExtensionHook,
    StepExecutionResult,
)
from app.runtime.state_machine import RuntimeAssistantState, RuntimeStateMachine
from app.runtime.fairy_presence import fairy_meta_for_response, fairy_meta_for_runtime_state
from app.runtime.system_actions.desktop_automation_compat import (
    is_explicit_desktop_automation_command,
    strip_explicit_desktop_automation_prefix,
)
from app.runtime.system_actions import SystemActionExecutor
from app.system_bridge import SystemActionResult, SystemBridgeManager
from app.tools.adapters.structured_tool_router import StructuredToolRouter
from app.config import voice_config


logger = logging.getLogger(__name__)

BundleExecutor = Callable[..., dict[str, Any]]
StreamingBundleExecutor = Callable[..., Iterable[dict[str, Any]]]
DirectCapabilityExecutor = Callable[..., dict[str, Any]]
StreamingDirectCapabilityExecutor = Callable[..., Iterable[dict[str, Any]]]


@dataclass(slots=True)
class RuntimeRequestContext:
    request_id: str
    session_id: str
    perception: PerceptionFrame
    planned_response: PlannedResponse
    response_trace: ResponseTrace
    tool_trace: ToolTrace
    state_machine: AssistantStateMachine
    route_hints: dict[str, Any]


class FairyRuntimeV2:
    """Compatibility-first structured runtime.

    Main path:
    Perception -> Response Planning -> Tool Routing -> Schema Normalization
    -> Context Update -> Card/Layout Decision -> UI Rendering -> Speech Output -> Debug Trace
    """

    def __init__(
        self,
        *,
        language: str = "zh_CN",
        response_pipeline: ResponsePipeline | None = None,
        search_tool: Callable[..., list[dict[str, Any]]] | None = None,
        fetch_page: Callable[[str], str] | None = None,
        vision_fallback: Callable[[str], Any] | None = None,
        bundle_executor: BundleExecutor | None = None,
        streaming_bundle_executor: StreamingBundleExecutor | None = None,
        capability_executors: dict[str, DirectCapabilityExecutor] | None = None,
        streaming_capability_executors: dict[str, StreamingDirectCapabilityExecutor] | None = None,
        system_bridge: SystemBridgeManager | None = None,
        semantic_arbitration_callback: Callable[..., Any] | None = None,
        web_browse_decision_callback: Callable[..., Any] | None = None,
    ) -> None:
        self.language = language
        self.response_pipeline = response_pipeline or ResponsePipeline(language=language)
        self.input_normalizer = InputNormalizer()
        self.intent_classifier = IntentClassifier()
        self.entity_extractor = EntityExtractor()
        self.followup_resolver = FollowUpResolver()
        self.modality_detector = ModalityDetector()
        self.query_resolver = QueryResolver(
            semantic_arbitration_callback=semantic_arbitration_callback,
            web_browse_decision_callback=web_browse_decision_callback,
        )
        contract_issues = self.query_resolver.validate_contracts()
        coverage = self.query_resolver.coverage_report()
        if contract_issues:
            logger.warning("query_resolution_contract_issues=%s", "; ".join(contract_issues))
        logger.info("query_resolution_coverage=%s", coverage)
        self.response_planner = ResponsePlanner()
        self.context_manager = ContextManager()
        self.schema_validator = SchemaValidator()
        self.speech_planner = SpeechPlanner()
        self.speech_router = SpeechRouter()
        self.tool_router = StructuredToolRouter()
        self.invocation_service = None
        active_bundle_executor = bundle_executor
        active_streaming_bundle_executor = streaming_bundle_executor
        self._bundle_executor = active_bundle_executor
        self._streaming_bundle_executor = active_streaming_bundle_executor
        self._capability_executors = dict(capability_executors or {})
        self._streaming_capability_executors = dict(streaming_capability_executors or {})
        self.activity_tracker = ActivityTracker()
        self.concurrent_tasks = ConcurrentTaskController()
        self._request_contexts: dict[str, RuntimeRequestContext] = {}
        self._schema_traces: dict[str, SchemaTrace] = {}
        self._renderer_traces: dict[str, RendererTrace] = {}
        self._speech_traces: dict[str, SpeechTrace] = {}
        self._state_traces: dict[str, list[StateTrace]] = {}
        self.enable_desktop_automation_compatibility = str(os.getenv("FAIRY_ENABLE_DESKTOP_AUTOMATION_COMPAT") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.system_bridge = system_bridge or SystemBridgeManager()
        self.runtime_state_machine = RuntimeStateMachine()
        self.system_action_executor = SystemActionExecutor(runtime_action_handler=self.execute_system_action)
        self.plan_extension_hook = PlanExtensionHook()
        self.system_bridge.update_capabilities(self.capabilities_snapshot())
        self._sync_runtime_state_snapshot(reason="runtime_constructed")
        self.system_bridge.register_action_handler("clear_asset_cache", lambda _payload: self.clear_asset_cache())
        self.system_bridge.register_action_handler("refresh_capabilities", lambda _payload: self.refresh_capabilities())
        self.system_bridge.register_action_handler(
            "restart_backend",
            lambda _payload: SystemActionResult(
                action="restart_backend",
                ok=False,
                message="restart_backend must be handled by the desktop bridge that owns the backend process.",
            ),
        )
        self._capability_executors.setdefault("system_action", self._direct_system_action_execute)
        self._streaming_capability_executors.setdefault("system_action", self._stream_direct_system_action_execute)
        self._set_runtime_state("warming_up", reason="runtime_components_initialized", force=True)

    def set_language(self, language: str) -> None:
        self.language = language
        self.response_pipeline.set_language(language)

    def set_bundle_executor(self, executor: BundleExecutor | None) -> None:
        self._bundle_executor = executor

    def set_streaming_bundle_executor(self, executor: StreamingBundleExecutor | None) -> None:
        self._streaming_bundle_executor = executor

    def capabilities_snapshot(self) -> dict[str, Any]:
        return {
            "chat": True,
            "streaming": True,
            "voice_input": False,
            "voice_output": bool(voice_config.enabled and (voice_config.speak_responses or voice_config.speak_system)),
            "cards": [
                "weather",
                "time",
                "location",
                "map_preview",
                "news_list",
                "visual_read",
                "generic_info",
                "specs",
                "compare",
                "release",
                "web_brief",
            ],
            "system_actions": [
                "cancel_current_request",
                "restart_backend",
                "clear_asset_cache",
                "refresh_capabilities",
            ],
            "chat_system_actions": {
                "backend": self.system_action_executor.supported_backend_actions(),
                "desktop": self.system_action_executor.supported_desktop_actions(),
            },
            "desktop_automation_compatibility_enabled": self.enable_desktop_automation_compatibility,
        }

    def get_system_state(self) -> dict[str, Any]:
        return self.system_bridge.snapshot()

    def get_system_events(self, *, limit: int = 25) -> list[dict[str, Any]]:
        return self.system_bridge.list_events(limit=limit)

    def execute_system_action(self, action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        result = self.system_bridge.perform_action(action, payload)
        if not result.ok and result.message:
            self.system_bridge.set_last_error(result.message)
        return result.to_dict()

    def cancel_current_request(self) -> dict[str, Any]:
        return self.execute_system_action("cancel_current_request")

    def clear_asset_cache(self) -> SystemActionResult:
        from app.services.assets import get_image_cache

        cache_root = get_image_cache().root
        removed = 0
        for child in cache_root.rglob("*"):
            if child.is_file():
                child.unlink(missing_ok=True)
                removed += 1
        self.system_bridge.set_backend_status("ready", message="asset cache cleared", emit_event=False)
        return SystemActionResult(
            action="clear_asset_cache",
            ok=True,
            message=f"Cleared {removed} cached asset file(s).",
            detail={"removed_files": removed, "cache_root": str(cache_root)},
        )

    def refresh_capabilities(self) -> SystemActionResult:
        capabilities = self.capabilities_snapshot()
        self.system_bridge.update_capabilities(capabilities)
        return SystemActionResult(
            action="refresh_capabilities",
            ok=True,
            message="Capabilities refreshed.",
            detail={"capabilities": capabilities},
        )

    def set_runtime_presence_state(
        self,
        state: RuntimeAssistantState,
        *,
        reason: str = "",
        request_id: str = "",
        session_id: str = "",
        force: bool = False,
    ) -> None:
        self._set_runtime_state(state, reason=reason, request_id=request_id, session_id=session_id, force=force)

    def runtime_state_snapshot(self) -> dict[str, Any]:
        return self.system_bridge.snapshot()

    def _runtime_fairy_meta(self, *, state: str | None = None, reason: str = "", last_error: str = "") -> dict[str, Any]:
        return fairy_meta_for_runtime_state(
            state or self.runtime_state_machine.current_state,
            reason=reason,
            last_error=last_error,
        )

    def _response_fairy_meta(
        self,
        *,
        payload: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        text: str = "",
        cards: list[Any] | None = None,
        errors: list[dict[str, Any]] | None = None,
        resolution: dict[str, Any] | None = None,
        selected_capability: str = "",
    ) -> dict[str, Any]:
        return fairy_meta_for_response(
            payload=payload,
            meta=meta,
            text=text,
            cards=cards,
            errors=errors,
            resolution=resolution,
            selected_capability=selected_capability,
        )

    def _sync_runtime_state_snapshot(
        self,
        *,
        request_id: str = "",
        session_id: str = "",
        reason: str = "",
        emit_event: bool = False,
    ) -> None:
        self.system_bridge.update_runtime_state(
            current_state=self.runtime_state_machine.current_state,
            trace=self.runtime_state_machine.trace_snapshot(),
            request_id=request_id or None,
            session_id=session_id or None,
            reason=reason,
            fairy=self._runtime_fairy_meta(reason=reason),
            emit_event=emit_event,
        )

    def _set_runtime_state(
        self,
        next_state: RuntimeAssistantState,
        *,
        reason: str = "",
        request_id: str = "",
        session_id: str = "",
        force: bool = False,
    ) -> None:
        transition = self.runtime_state_machine.transition(
            next_state,
            reason=reason,
            request_id=request_id or None,
            session_id=session_id or None,
            force=force,
        )
        if transition is None:
            return
        self.system_bridge.update_runtime_state(
            current_state=self.runtime_state_machine.current_state,
            trace=self.runtime_state_machine.trace_snapshot(),
            request_id=request_id or None,
            session_id=session_id or None,
            reason=transition.reason,
            fairy=self._runtime_fairy_meta(reason=transition.reason),
            emit_event=True,
        )

    def _direct_system_action_execute(
        self,
        *,
        message: str,
        session_id: str,
        request_id: str,
        attachments: list[str],
        request_origin: str,
        route_hints: dict[str, Any],
    ) -> dict[str, Any]:
        explicit_intent = str(route_hints.get("explicit_intent") or "").strip().lower()
        effective_message = strip_explicit_desktop_automation_prefix(message) if explicit_intent == "desktop_automation" else message
        _ = attachments
        return self.system_action_executor.execute(
            message=effective_message,
            session_id=session_id,
            request_id=request_id,
            request_origin=request_origin,
            cancel_event=None,
            explicit_intent=explicit_intent,
            allow_desktop_automation_compatibility=self.enable_desktop_automation_compatibility and explicit_intent == "desktop_automation",
        )

    def _stream_direct_system_action_execute(
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
        explicit_intent = str(route_hints.get("explicit_intent") or "").strip().lower()
        effective_message = strip_explicit_desktop_automation_prefix(message) if explicit_intent == "desktop_automation" else message
        _ = attachments
        if cancel_event is not None and cancel_event.is_set():
            yield {"kind": "error", "message": "cancelled"}
            return
        yield from self.system_action_executor.stream_execute(
            message=effective_message,
            session_id=session_id,
            request_id=request_id,
            request_origin=request_origin,
            cancel_event=cancel_event,
            explicit_intent=explicit_intent,
            allow_desktop_automation_compatibility=self.enable_desktop_automation_compatibility and explicit_intent == "desktop_automation",
        )

    def plan_request(
        self,
        user_text: str,
        *,
        previous_structured: dict[str, Any] | None = None,
        session_id: str = "",
        request_id: str = "",
    ) -> ResponseRequestPlan:
        self._set_runtime_state("thinking", reason="request_accepted", request_id=request_id, session_id=session_id)
        perception = self._build_perception_frame(user_text, previous_structured=previous_structured, session_id=session_id)
        planned = self.response_planner.plan(perception, previous_structured=previous_structured)
        runtime_request_id = request_id or ""
        response_trace = ResponseTrace(
            request_id=runtime_request_id,
            perception=asdict(perception),
            planning={
                "intent": planned.request_plan.intent,
                "modality": planned.request_plan.modality,
                "speech_mode": planned.request_plan.speech_mode,
                "card_types": list(planned.execution_plan.card_types),
                "layout_mode": planned.execution_plan.layout_mode,
            },
        )
        tool_chain = self.tool_router.determine_tool_chain(perception.intent)
        tool_trace = ToolTrace(request_id=runtime_request_id, tool_chain=tool_chain)
        state_machine = AssistantStateMachine()
        state_machine.transition("planning")
        self._record_state(runtime_request_id, "", "planning")
        resolution_meta = dict(perception.metadata.get("resolution") or {})
        resolution_route_hints = dict(resolution_meta.get("route_hints") or {})
        route_hints = {
            "perception_intent": perception.intent,
            "preferred_routes": self._preferred_route_hints_for(perception.intent),
            "active_focus": dict(perception.metadata.get("active_focus") or {}),
            "preferred_modalities": list(perception.preferred_modalities),
            "explicit_intent": str(perception.metadata.get("explicit_intent") or ""),
            "raw_user_text": str(user_text or "").strip(),
            "normalized_user_text": str(perception.normalized_text or "").strip(),
            "resolved_query": str(resolution_meta.get("normalized_query") or user_text).strip(),
            "followup_target": str(perception.followup_target or ""),
            "followup_focus_value": str((perception.metadata.get("active_focus") or {}).get("location") or ""),
            "followup_reused": bool(perception.metadata.get("followup_reused")),
            "resolution": resolution_meta,
            "clarification_needed": bool(resolution_meta.get("clarification_needed")),
            "execution_mode": str(resolution_meta.get("execution_mode") or "single"),
            "secondary_intents": list(resolution_meta.get("secondary_intents") or []),
        }
        route_hints.update(resolution_route_hints)
        if runtime_request_id:
            self._request_contexts[runtime_request_id] = RuntimeRequestContext(
                request_id=runtime_request_id,
                session_id=session_id,
                perception=perception,
                planned_response=planned,
                response_trace=response_trace,
                tool_trace=tool_trace,
                state_machine=state_machine,
                route_hints=route_hints,
            )
        self.activity_tracker.record(runtime_request_id, "plan_request", perception.intent)
        logger.info(
            "runtime_v2_plan request_id=%s intent=%s confidence=%.2f tool_chain=%s response_mode=%s layout=%s clarification=%s slots=%s",
            runtime_request_id,
            perception.intent,
            perception.confidence,
            ",".join(tool_chain),
            planned.execution_plan.response_mode,
            planned.execution_plan.layout_mode,
            bool((perception.metadata.get("resolution") or {}).get("clarification_needed")),
            dict((perception.metadata.get("resolution") or {}).get("slots") or {}),
        )
        return planned.request_plan

    def get_route_hints(self, request_id: str) -> dict[str, Any]:
        context = self._request_contexts.get(request_id)
        if context is None:
            return {}
        return dict(context.route_hints)

    def invoke(
        self,
        message: str,
        session_id: str,
        attachments: list[str] | None = None,
        *,
        request_id: str = "",
        previous_structured: dict[str, Any] | None = None,
        bundle_executor: BundleExecutor | None = None,
        request_origin: str = "runtime",
        include_compat_fields: bool = False,
    ) -> dict[str, Any]:
        runtime_request_id = request_id or uuid.uuid4().hex
        try:
            request_plan = self.plan_request(
                message,
                previous_structured=previous_structured,
                session_id=session_id,
                request_id=runtime_request_id,
            )
            runtime_context = self._request_contexts.get(runtime_request_id)
            resolution = dict((runtime_context.perception.metadata.get("resolution") or {}) if runtime_context else {})
            bypass_clarification = self._should_bypass_resolution_clarification(
                runtime_context=runtime_context,
                request_plan=request_plan,
                resolution=resolution,
            )
            if resolution.get("clarification_needed") and not bypass_clarification:
                self.context_manager.mark_clarification(
                    session_id,
                    capability=str(resolution.get("capability") or request_plan.intent),
                    message=str(resolution.get("clarification_message") or ""),
                )
                self._set_runtime_state("replying", reason="clarification_response", request_id=runtime_request_id, session_id=session_id)
                self._set_runtime_state("idle", reason="clarification_completed", request_id=runtime_request_id, session_id=session_id)
                contract = self._build_clarification_contract(
                    request_id=runtime_request_id,
                    session_id=session_id,
                    request_plan=request_plan,
                    resolution=resolution,
                    include_compat_fields=include_compat_fields,
                    request_origin=request_origin,
                )
                self.system_bridge.set_last_error("")
                return contract
            orchestration_plan = self._build_runtime_execution_plan_from_resolution(resolution)
            if orchestration_plan is None:
                orchestration_plan = self._build_continuation_plan_from_resolution(resolution)
            active_bundle_executor = bundle_executor or self._bundle_executor
            if orchestration_plan is not None and orchestration_plan.steps:
                self._set_runtime_state("analyzing", reason="orchestration_plan_built", request_id=runtime_request_id, session_id=session_id)
                return self._invoke_orchestration(
                    request_id=runtime_request_id,
                    session_id=session_id,
                    message=message,
                    request_plan=request_plan,
                    resolution=resolution,
                    plan=orchestration_plan,
                    attachments=list(attachments or []),
                    bundle_executor=active_bundle_executor,
                    request_origin=request_origin,
                    include_compat_fields=include_compat_fields,
                )
            route_hints = self.get_route_hints(runtime_request_id)
            forced_bundle = str(route_hints.get("forced_bundle") or "").strip()
            selected_capability = self._capability_for_intent(request_plan.intent)
            effective_message = str(route_hints.get("resolved_query") or message).strip() or message
            execution_payload = self._invoke_capability(
                capability_name=selected_capability,
                message=effective_message,
                request_id=runtime_request_id,
                session_id=session_id,
                bundle_executor=active_bundle_executor,
                attachments=list(attachments or []),
                request_origin=request_origin,
                route_hints=route_hints,
            )
            normalized = self.normalize_result(
                user_text=message,
                payload=execution_payload,
                assistant_text=str(execution_payload.get("assistant_text") or execution_payload.get("text") or "").strip(),
                assistant_html=str(execution_payload.get("assistant_html") or "").strip(),
                request_plan=request_plan,
                session_id=session_id,
                request_id=runtime_request_id,
            )
            if normalized.text_reply or normalized.cards:
                self._set_runtime_state("replying", reason="response_ready", request_id=runtime_request_id, session_id=session_id)
            self._set_runtime_state("idle", reason="request_completed", request_id=runtime_request_id, session_id=session_id)
            contract = normalized.to_contract_dict()
            meta = dict(contract.get("meta") or {})
            meta["resolution"] = resolution
            meta["runtime_state"] = self.runtime_state_snapshot()
            meta["runtime"] = {
                "selected_capability": selected_capability or "bundle-runtime",
                "selected_bundle": str(
                    execution_payload.get("skill_name")
                    or forced_bundle
                    or execution_payload.get("_runtime_forced_bundle")
                    or ""
                ),
                "forced_bundle": forced_bundle,
                "executor_path": str(execution_payload.get("_runtime_executor_path") or ""),
                "used_bundle_fallback": bool(execution_payload.get("_bundle_fallback")),
                "request_origin": request_origin,
                "compat_fields_emitted": bool(include_compat_fields),
                "explicit_intent": str(runtime_context.perception.metadata.get("explicit_intent") or "") if runtime_context else "",
                "desktop_automation_compatibility": bool(execution_payload.get("_desktop_automation_compatibility")),
                "system_action_type": str(execution_payload.get("_runtime_system_action_type") or ""),
                "system_action_name": str(execution_payload.get("_runtime_system_action_name") or ""),
                "desktop_bridge_result": dict(execution_payload.get("_runtime_desktop_bridge_result") or {}),
                "web_access": dict(execution_payload.get("_runtime_web_access") or {}),
                "query_debug": dict(execution_payload.get("_runtime_query_debug") or {}),
                "activity": dict(execution_payload.get("_runtime_activity") or {}),
            }
            contract["meta"] = meta
            if execution_payload.get("errors"):
                contract["errors"] = list(contract.get("errors") or []) + list(execution_payload.get("errors") or [])
            meta = dict(contract.get("meta") or {})
            meta["fairy"] = self._response_fairy_meta(
                payload=execution_payload,
                meta=meta,
                text=str(contract.get("text") or ""),
                cards=list(contract.get("cards") or []),
                errors=list(contract.get("errors") or []),
                resolution=resolution,
                selected_capability=selected_capability or forced_bundle or "bundle-runtime",
            )
            contract["meta"] = meta
            if contract.get("errors"):
                self.system_bridge.set_last_error(
                    str((contract.get("errors") or [{}])[0].get("message") or ""),
                    request_id=runtime_request_id,
                    session_id=session_id,
                )
            else:
                self.context_manager.clear_clarification(session_id)
                self.system_bridge.set_last_error("")
            self._remember_completed_single_step_plan(
                session_id=session_id,
                request_id=runtime_request_id,
                capability=str(
                    execution_payload.get("skill_name")
                    or forced_bundle
                    or selected_capability
                    or "bundle-runtime"
                ),
                normalized_query=effective_message,
                slots=dict(resolution.get("validated_slots") or resolution.get("slots") or {}),
                normalized=normalized,
                payload=execution_payload,
            )
            if include_compat_fields:
                contract.update(
                    {
                        # Deprecated Qt bridge fields. Keep them opt-in so they do not leak into the API contract.
                        "assistant_text": normalized.text_reply,
                        "assistant_html": normalized.text_rich_html,
                        "summary": str(execution_payload.get("summary") or normalized.text_reply).strip(),
                        "sources": list(execution_payload.get("sources") or []),
                        "warnings": list(execution_payload.get("warnings") or []),
                        "structured": dict(execution_payload.get("structured") or {}),
                        "skill_name": str(execution_payload.get("skill_name") or selected_capability or "bundle-runtime"),
                        "success": bool(execution_payload.get("success", True)) and not contract["errors"],
                        "changed_files": list(execution_payload.get("changed_files") or []),
                        "commands_run": list(execution_payload.get("commands_run") or []),
                        "validations": list(execution_payload.get("validations") or []),
                        "cancelled": bool(execution_payload.get("cancelled", False)),
                        "request_origin": request_origin,
                    }
                )
            return contract
        except Exception as exc:
            self._set_runtime_state("error", reason=f"invoke_failed:{type(exc).__name__}", request_id=runtime_request_id, session_id=session_id, force=True)
            raise

    def stream_invoke(
        self,
        message: str,
        session_id: str,
        attachments: list[str] | None = None,
        *,
        request_id: str = "",
        previous_structured: dict[str, Any] | None = None,
        bundle_executor: BundleExecutor | None = None,
        streaming_bundle_executor: StreamingBundleExecutor | None = None,
        request_origin: str = "runtime_stream",
        cancel_event: threading.Event | None = None,
    ) -> Iterable[StreamEventBase]:
        runtime_request_id = request_id or uuid.uuid4().hex
        effective_session_id = session_id or "default"
        if cancel_event is None:
            cancel_event = threading.Event()
        active_bundle_executor = bundle_executor or self._bundle_executor
        active_streaming_bundle_executor = streaming_bundle_executor or self._streaming_bundle_executor
        request_plan = self.plan_request(
            message,
            previous_structured=previous_structured,
            session_id=effective_session_id,
            request_id=runtime_request_id,
        )
        runtime_context = self._request_contexts.get(runtime_request_id)
        resolution = dict((runtime_context.perception.metadata.get("resolution") or {}) if runtime_context else {})
        selected_capability = self._capability_for_intent(request_plan.intent)
        bypass_clarification = self._should_bypass_resolution_clarification(
            runtime_context=runtime_context,
            request_plan=request_plan,
            resolution=resolution,
        )
        attachments = list(attachments or [])
        self.system_bridge.register_stream(
            request_id=runtime_request_id,
            session_id=effective_session_id,
            cancel_handler=lambda: (cancel_event.set() or True),
        )
        yield MessageStartEvent(
            request_id=runtime_request_id,
            session_id=effective_session_id,
            meta={
                "intent": request_plan.intent,
                "modality": request_plan.modality,
                "speech_mode": request_plan.speech_mode,
                "resolution": resolution,
                "runtime_state": self.runtime_state_snapshot(),
                "fairy": self._runtime_fairy_meta(state="thinking", reason="stream_started"),
            },
        )
        for trace_event in list(resolution.get("trace") or []):
            if not isinstance(trace_event, dict):
                continue
            yield ProgressStreamEvent(
                request_id=runtime_request_id,
                session_id=effective_session_id,
                stage=str(trace_event.get("stage") or "resolution"),
                text=str(trace_event.get("text") or "Resolution update."),
            )

        if resolution.get("clarification_needed") and not bypass_clarification:
            self.context_manager.mark_clarification(
                effective_session_id,
                capability=str(resolution.get("capability") or request_plan.intent),
                message=str(resolution.get("clarification_message") or ""),
            )
            self._set_runtime_state("replying", reason="clarification_response", request_id=runtime_request_id, session_id=effective_session_id)
            clarification = self._build_clarification_response(
                request_id=runtime_request_id,
                session_id=effective_session_id,
                request_plan=request_plan,
                resolution=resolution,
                request_origin=request_origin,
            )
            for chunk in self._chunk_text_for_streaming(clarification.text_reply):
                yield TextDeltaEvent(
                    request_id=runtime_request_id,
                    session_id=effective_session_id,
                    text=chunk,
                )
            for card in clarification.cards:
                yield CardStreamEvent(
                    request_id=runtime_request_id,
                    session_id=effective_session_id,
                    card=card,
                )
            self._set_runtime_state("idle", reason="clarification_completed", request_id=runtime_request_id, session_id=effective_session_id)
            end_meta = dict(clarification.meta)
            runtime_meta = dict(end_meta.get("runtime") or {})
            runtime_meta["compat_fields_emitted"] = False
            end_meta["runtime"] = runtime_meta
            end_meta["runtime_state"] = self.runtime_state_snapshot()
            end_meta["fairy"] = self._response_fairy_meta(
                meta=end_meta,
                text=clarification.text_reply,
                cards=clarification.cards,
                errors=list(clarification.errors),
                resolution=resolution,
                selected_capability=self._capability_for_intent(request_plan.intent) or "clarification",
            )
            yield MessageEndEvent(
                request_id=runtime_request_id,
                session_id=effective_session_id,
                text=clarification.text_reply,
                cards=clarification.cards,
                meta=end_meta,
                errors=list(clarification.errors),
            )
            self.system_bridge.finish_stream(
                request_id=runtime_request_id,
                session_id=effective_session_id,
                status="finished",
                detail={"card_count": len(clarification.cards), "error_count": len(clarification.errors), "clarification": True},
            )
            return
        orchestration_plan = self._build_runtime_execution_plan_from_resolution(resolution)
        if orchestration_plan is None:
            orchestration_plan = self._build_continuation_plan_from_resolution(resolution)
        if orchestration_plan is not None and orchestration_plan.steps:
            self._set_runtime_state("analyzing", reason="orchestration_plan_built", request_id=runtime_request_id, session_id=effective_session_id)
            yield from self._stream_orchestration(
                request_id=runtime_request_id,
                session_id=effective_session_id,
                message=message,
                request_plan=request_plan,
                resolution=resolution,
                plan=orchestration_plan,
                attachments=attachments,
                bundle_executor=active_bundle_executor,
                request_origin=request_origin,
                cancel_event=cancel_event,
            )
            return

        route_hints = self.get_route_hints(runtime_request_id)
        forced_bundle = str(route_hints.get("forced_bundle") or "").strip()

        yield ProgressStreamEvent(
            request_id=runtime_request_id,
            session_id=effective_session_id,
            stage="planning",
            text=f"Selected execution path: {forced_bundle or selected_capability or 'bundle_runtime_executor'}.",
        )

        emitted_text_delta = False
        execution_payload: dict[str, Any] | None = None
        active_streaming_executor = active_streaming_bundle_executor
        direct_streaming_executor = self._streaming_capability_executors.get(selected_capability or "")
        effective_message = str(route_hints.get("resolved_query") or message).strip() or message
        try:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("cancelled")
            if forced_bundle:
                if active_streaming_executor is not None:
                    for event in active_streaming_executor(
                        message=effective_message,
                        session_id=effective_session_id,
                        request_id=runtime_request_id,
                        attachments=attachments,
                        request_origin=request_origin,
                        route_hints=route_hints,
                        cancel_event=cancel_event,
                    ):
                        kind = str(event.get("kind") or "").strip().lower()
                        if kind == "text_delta":
                            token = str(event.get("text") or "")
                            if token:
                                emitted_text_delta = True
                                yield TextDeltaEvent(
                                    request_id=runtime_request_id,
                                    session_id=effective_session_id,
                                    text=token,
                                )
                            continue
                        if kind == "progress":
                            progress = self.build_progress_event(
                                str(event.get("event_name") or ""),
                                dict(event.get("payload") or {}),
                                user_text=message,
                                request_id=runtime_request_id,
                            )
                            if progress is not None:
                                yield ProgressStreamEvent(
                                    request_id=runtime_request_id,
                                    session_id=effective_session_id,
                                    stage=progress.stage,
                                    text=progress.text,
                                )
                            continue
                        if kind == "error":
                            raise RuntimeError(str(event.get("message") or "Streaming bundle executor failed."))
                        if kind == "result":
                            execution_payload = dict(event.get("payload") or {})
                            execution_payload.setdefault("_runtime_forced_bundle", forced_bundle)
                else:
                    execution_payload = self._invoke_capability(
                        capability_name=selected_capability,
                        message=effective_message,
                        request_id=runtime_request_id,
                        session_id=effective_session_id,
                        bundle_executor=active_bundle_executor,
                        attachments=attachments,
                        request_origin=request_origin,
                        route_hints=route_hints,
                    )
            elif selected_capability and self._can_use_invocation_service(selected_capability):
                for event in self._stream_invocation_service(
                    capability_name=selected_capability,
                    message=effective_message,
                    request_id=runtime_request_id,
                    session_id=effective_session_id,
                    cancel_event=cancel_event,
                ):
                    kind = str(event.get("kind") or "").strip().lower()
                    if kind == "progress":
                        progress = self.build_progress_event(
                            str(event.get("event_name") or ""),
                            dict(event.get("payload") or {}),
                            user_text=message,
                            request_id=runtime_request_id,
                        )
                        if progress is not None:
                            yield ProgressStreamEvent(
                                request_id=runtime_request_id,
                                session_id=effective_session_id,
                                stage=progress.stage,
                                text=progress.text,
                            )
                        continue
                    if kind == "error":
                        raise RuntimeError(str(event.get("message") or "Streaming execution failed."))
                    if kind == "result":
                        execution_payload = dict(event.get("payload") or {})
            elif selected_capability and direct_streaming_executor is not None:
                self.record_tool_selection(
                    runtime_request_id,
                    selected_capability,
                    "runtime_facade_direct_streaming_executor",
                )
                delegate_to_bundle_runtime = False
                delegate_reason = ""
                delegated_runtime_meta: dict[str, Any] = {}
                for event in direct_streaming_executor(
                    message=effective_message,
                    session_id=effective_session_id,
                    request_id=runtime_request_id,
                    attachments=attachments,
                    request_origin=request_origin,
                    route_hints=self.get_route_hints(runtime_request_id),
                    cancel_event=cancel_event,
                ):
                    if cancel_event is not None and cancel_event.is_set():
                        break
                    kind = str(event.get("kind") or "").strip().lower()
                    if kind == "delegate_bundle_runtime":
                        delegate_to_bundle_runtime = True
                        delegate_reason = str(event.get("reason") or "streaming_direct_executor_requested_bundle_fallback").strip()
                        delegated_runtime_meta = {
                            "system_action_type": str(event.get("system_action_type") or ""),
                            "system_action_name": str(event.get("system_action_name") or ""),
                            "desktop_automation_compatibility": bool(event.get("desktop_automation_compatibility")),
                            "executor_path": str(
                                event.get("executor_path") or "desktop_automation_compatibility_executor"
                            ),
                        }
                        break
                    if kind == "text_delta":
                        token = str(event.get("text") or "")
                        if token:
                            emitted_text_delta = True
                            yield TextDeltaEvent(
                                request_id=runtime_request_id,
                                session_id=effective_session_id,
                                text=token,
                            )
                        continue
                    if kind == "progress":
                        progress = self.build_progress_event(
                            str(event.get("event_name") or ""),
                            dict(event.get("payload") or {}),
                            user_text=message,
                            request_id=runtime_request_id,
                        )
                        if progress is not None:
                            yield ProgressStreamEvent(
                                request_id=runtime_request_id,
                                session_id=effective_session_id,
                                stage=progress.stage,
                                text=progress.text,
                            )
                        continue
                    if kind == "error":
                        raise RuntimeError(str(event.get("message") or "Streaming direct executor failed."))
                    if kind == "result":
                        execution_payload = dict(event.get("payload") or {})
                if delegate_to_bundle_runtime:
                    logger.info(
                        "runtime_v2_direct_streaming_executor_delegated_to_bundle_runtime request_id=%s capability=%s reason=%s",
                        runtime_request_id,
                        selected_capability or "bundle_fallback",
                        delegate_reason,
                    )
                    if active_streaming_executor is not None:
                        for event in active_streaming_executor(
                            message=effective_message,
                            session_id=effective_session_id,
                            request_id=runtime_request_id,
                            attachments=attachments,
                            request_origin=request_origin,
                            route_hints=self.get_route_hints(runtime_request_id),
                            cancel_event=cancel_event,
                        ):
                            kind = str(event.get("kind") or "").strip().lower()
                            if kind == "text_delta":
                                token = str(event.get("text") or "")
                                if token:
                                    emitted_text_delta = True
                                    yield TextDeltaEvent(
                                        request_id=runtime_request_id,
                                        session_id=effective_session_id,
                                        text=token,
                                    )
                                continue
                            if kind == "progress":
                                progress = self.build_progress_event(
                                    str(event.get("event_name") or ""),
                                    dict(event.get("payload") or {}),
                                    user_text=message,
                                    request_id=runtime_request_id,
                                )
                                if progress is not None:
                                    yield ProgressStreamEvent(
                                        request_id=runtime_request_id,
                                        session_id=effective_session_id,
                                        stage=progress.stage,
                                        text=progress.text,
                                    )
                                continue
                            if kind == "error":
                                raise RuntimeError(str(event.get("message") or "Streaming bundle executor failed."))
                            if kind == "result":
                                execution_payload = dict(event.get("payload") or {})
                                execution_payload["_runtime_system_action_type"] = str(
                                    delegated_runtime_meta.get("system_action_type") or "desktop_automation_compatibility"
                                )
                                execution_payload["_runtime_system_action_name"] = str(
                                    delegated_runtime_meta.get("system_action_name") or "desktop_automation_compatibility_action"
                                )
                                execution_payload["_runtime_executor_path"] = str(
                                    delegated_runtime_meta.get("executor_path") or "desktop_automation_compatibility_executor"
                                )
                                execution_payload["_desktop_automation_compatibility"] = bool(
                                    delegated_runtime_meta.get("desktop_automation_compatibility")
                                )
                    else:
                        execution_payload = {
                            "assistant_text": "This system action still requires the bundle executor, but no compatible streaming bundle executor is available.",
                            "summary": "This system action still requires the bundle executor, but no compatible streaming bundle executor is available.",
                            "sources": [],
                            "warnings": ["missing_streaming_bundle_executor"],
                            "structured": {},
                            "skill_name": selected_capability or "system_action",
                            "success": False,
                            "errors": [{"code": "missing_streaming_bundle_executor", "message": "No streaming bundle executor was available for the delegated system action."}],
                            "_runtime_system_action_type": str(
                                delegated_runtime_meta.get("system_action_type") or "desktop_automation_compatibility"
                            ),
                            "_runtime_system_action_name": str(
                                delegated_runtime_meta.get("system_action_name") or "desktop_automation_compatibility_action"
                            ),
                            "_runtime_executor_path": str(
                                delegated_runtime_meta.get("executor_path") or "desktop_automation_compatibility_executor"
                            ),
                            "_desktop_automation_compatibility": bool(
                                delegated_runtime_meta.get("desktop_automation_compatibility")
                            ),
                        }
            elif active_streaming_executor is not None:
                for event in active_streaming_executor(
                    message=effective_message,
                    session_id=effective_session_id,
                    request_id=runtime_request_id,
                    attachments=attachments,
                    request_origin=request_origin,
                    route_hints=self.get_route_hints(runtime_request_id),
                    cancel_event=cancel_event,
                ):
                    kind = str(event.get("kind") or "").strip().lower()
                    if kind == "text_delta":
                        token = str(event.get("text") or "")
                        if token:
                            emitted_text_delta = True
                            yield TextDeltaEvent(
                                request_id=runtime_request_id,
                                session_id=effective_session_id,
                                text=token,
                            )
                        continue
                    if kind == "progress":
                        progress = self.build_progress_event(
                            str(event.get("event_name") or ""),
                            dict(event.get("payload") or {}),
                            user_text=message,
                            request_id=runtime_request_id,
                        )
                        if progress is not None:
                            yield ProgressStreamEvent(
                                request_id=runtime_request_id,
                                session_id=effective_session_id,
                                stage=progress.stage,
                                text=progress.text,
                            )
                        continue
                    if kind == "error":
                        raise RuntimeError(str(event.get("message") or "Streaming bundle executor failed."))
                    if kind == "result":
                        execution_payload = dict(event.get("payload") or {})
            else:
                yield ProgressStreamEvent(
                    request_id=runtime_request_id,
                    session_id=effective_session_id,
                    stage="executing",
                    text="Running runtime pipeline...",
                )
                execution_payload = self._invoke_capability(
                    capability_name=selected_capability,
                    message=effective_message,
                    request_id=runtime_request_id,
                    session_id=effective_session_id,
                    bundle_executor=active_bundle_executor,
                    attachments=attachments,
                    request_origin=request_origin,
                    route_hints=route_hints,
                )
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("cancelled")
            if execution_payload is None:
                execution_payload = {
                    "assistant_text": "",
                    "summary": "",
                    "sources": [],
                    "warnings": [],
                    "structured": {},
                    "skill_name": selected_capability or "runtime_stream",
                    "success": False,
                    "errors": [{"code": "empty_stream_result", "message": "Streaming path returned no final payload."}],
                }

            normalized = self.normalize_result(
                user_text=message,
                payload=execution_payload,
                assistant_text=str(execution_payload.get("assistant_text") or execution_payload.get("text") or "").strip(),
                assistant_html=str(execution_payload.get("assistant_html") or "").strip(),
                request_plan=request_plan,
                session_id=effective_session_id,
                request_id=runtime_request_id,
            )
            if not emitted_text_delta:
                for chunk in self._chunk_text_for_streaming(normalized.text_reply):
                    self._set_runtime_state("replying", reason="streaming_text", request_id=runtime_request_id, session_id=effective_session_id)
                    yield TextDeltaEvent(
                        request_id=runtime_request_id,
                        session_id=effective_session_id,
                        text=chunk,
                    )
            for card in normalized.cards:
                self._set_runtime_state("replying", reason="streaming_card", request_id=runtime_request_id, session_id=effective_session_id)
                yield CardStreamEvent(
                    request_id=runtime_request_id,
                    session_id=effective_session_id,
                    card=card,
                )
            self._set_runtime_state("idle", reason="stream_finished", request_id=runtime_request_id, session_id=effective_session_id)
            end_meta = dict(normalized.meta)
            end_meta["resolution"] = resolution
            end_meta["runtime_state"] = self.runtime_state_snapshot()
            runtime_meta = dict(end_meta.get("runtime") or {})
            runtime_meta["selected_capability"] = selected_capability or "bundle-runtime"
            runtime_meta["selected_bundle"] = str(
                execution_payload.get("skill_name")
                or forced_bundle
                or execution_payload.get("_runtime_forced_bundle")
                or ""
            )
            runtime_meta["forced_bundle"] = forced_bundle
            runtime_meta["executor_path"] = str(execution_payload.get("_runtime_executor_path") or "")
            runtime_meta["compat_fields_emitted"] = False
            runtime_meta["explicit_intent"] = str((runtime_context.perception.metadata.get("explicit_intent") or "") if runtime_context else "")
            runtime_meta["desktop_automation_compatibility"] = bool(
                execution_payload.get("_desktop_automation_compatibility")
            )
            runtime_meta["system_action_type"] = str(execution_payload.get("_runtime_system_action_type") or "")
            runtime_meta["system_action_name"] = str(execution_payload.get("_runtime_system_action_name") or "")
            runtime_meta["desktop_bridge_result"] = dict(execution_payload.get("_runtime_desktop_bridge_result") or {})
            runtime_meta["web_access"] = dict(execution_payload.get("_runtime_web_access") or {})
            runtime_meta["query_debug"] = dict(execution_payload.get("_runtime_query_debug") or {})
            runtime_meta["activity"] = dict(execution_payload.get("_runtime_activity") or {})
            end_meta["runtime"] = runtime_meta
            end_meta["fairy"] = self._response_fairy_meta(
                payload=execution_payload,
                meta=end_meta,
                text=normalized.text_reply,
                cards=normalized.cards,
                errors=list(normalized.errors),
                resolution=resolution,
                selected_capability=selected_capability or forced_bundle or "bundle-runtime",
            )
            yield MessageEndEvent(
                request_id=runtime_request_id,
                session_id=effective_session_id,
                text=normalized.text_reply,
                cards=normalized.cards,
                meta=end_meta,
                errors=list(normalized.errors),
            )
            self.system_bridge.finish_stream(
                request_id=runtime_request_id,
                session_id=effective_session_id,
                status="finished",
                detail={"card_count": len(normalized.cards), "error_count": len(normalized.errors)},
            )
            self._remember_completed_single_step_plan(
                session_id=effective_session_id,
                request_id=runtime_request_id,
                capability=str(
                    execution_payload.get("skill_name")
                    or forced_bundle
                    or selected_capability
                    or "bundle-runtime"
                ),
                normalized_query=effective_message,
                slots=dict(resolution.get("validated_slots") or resolution.get("slots") or {}),
                normalized=normalized,
                payload=execution_payload,
            )
            self.context_manager.clear_clarification(effective_session_id)
        except Exception as exc:
            logger.exception("runtime_v2_stream_invoke_failed request_id=%s", runtime_request_id)
            error_code = "cancelled" if str(exc).strip().lower() == "cancelled" else "runtime_stream_error"
            self._set_runtime_state(
                "idle" if error_code == "cancelled" else "error",
                reason="stream_cancelled" if error_code == "cancelled" else f"stream_failed:{type(exc).__name__}",
                request_id=runtime_request_id,
                session_id=effective_session_id,
                force=error_code != "cancelled",
            )
            self.system_bridge.finish_stream(
                request_id=runtime_request_id,
                session_id=effective_session_id,
                status="cancelled" if error_code == "cancelled" else "error",
                detail={"code": error_code, "message": str(exc)},
            )
            yield ErrorEvent(
                request_id=runtime_request_id,
                session_id=effective_session_id,
                code=error_code,
                message=str(exc),
            )

    def _stream_invocation_service(
        self,
        *,
        capability_name: str,
        message: str,
        request_id: str,
        session_id: str,
        cancel_event: threading.Event | None,
    ) -> Iterable[dict[str, Any]]:
        event_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        done_sentinel = {"kind": "done"}

        def emit_progress(event_name: str, payload: dict[str, Any]) -> None:
            if cancel_event is not None and cancel_event.is_set():
                return
            event_queue.put(
                {
                    "kind": "progress",
                    "event_name": event_name,
                    "payload": dict(payload or {}),
                }
            )

        def worker() -> None:
            try:
                result = self.invocation_service.invoke(
                    capability_name,
                    message,
                    request_id=request_id,
                    session_id=session_id,
                    progress_callback=emit_progress,
                    cancel_event=cancel_event,
                    skip_state_resolution=True,
                )
                normalized_payload = self._normalize_execution_payload(
                    result,
                    skill_name=capability_name,
                    request_id=request_id,
                    session_id=session_id,
                )
                event_queue.put({"kind": "result", "payload": normalized_payload})
            except Exception as exc:
                event_queue.put({"kind": "error", "message": str(exc)})
            finally:
                event_queue.put(done_sentinel)

        thread = threading.Thread(
            target=worker,
            name=f"runtime-invoke-{request_id[:8]}",
            daemon=True,
        )
        thread.start()

        while True:
            if cancel_event is not None and cancel_event.is_set():
                break
            try:
                item = event_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if item is done_sentinel:
                break
            yield item

    def build_progress_event(
        self,
        event_name: str,
        payload: dict[str, Any],
        *,
        user_text: str = "",
        request_id: str = "",
    ) -> ResponseProgressEvent | None:
        progress_event = self.response_pipeline.build_progress_event(event_name, payload, user_text=user_text)
        if request_id:
            self.activity_tracker.record(request_id, f"progress:{event_name}", progress_event.text if progress_event else "")
            if progress_event is not None:
                self._transition_for_progress(request_id, event_name)
        return progress_event

    def normalize_result(
        self,
        *,
        user_text: str,
        payload: dict[str, Any],
        assistant_text: str,
        assistant_html: str = "",
        request_plan: ResponseRequestPlan | None = None,
        session_id: str = "",
        request_id: str = "",
    ) -> NormalizedAssistantResponse:
        runtime_context = self._request_contexts.get(request_id or "")
        effective_plan = request_plan or (runtime_context.planned_response.request_plan if runtime_context else None)
        normalized = self.response_pipeline.normalize_result(
            user_text=user_text,
            payload=payload,
            assistant_text=assistant_text,
            assistant_html=assistant_html,
            request_plan=effective_plan,
            request_id=request_id,
            session_id=session_id,
        )
        normalized = self._validate_cards(normalized, request_id=request_id)
        speech_decision = self.speech_planner.plan(normalized)
        normalized.speech_payload = SpeechPayload(
            mode=speech_decision.mode if speech_decision.mode != "silent" else "summary_first",
            text=speech_decision.text,
            allow_streaming=speech_decision.allow_streaming,
        )
        route = self.speech_router.route(speech_decision)
        if request_id:
            self._speech_traces[request_id] = SpeechTrace(request_id=request_id, mode=speech_decision.mode, route=route)
            self._record_state(request_id, self._current_state(request_id), "rendering_cards")
            if route != "skip":
                self._record_state(request_id, "rendering_cards", "speaking")
            self.activity_tracker.record(request_id, "normalize_result", normalized.modality)
        if session_id and runtime_context is not None:
            structured_payload = payload.get("structured") if isinstance(payload.get("structured"), dict) else {}
            self.context_manager.update_from_turn(session_id, runtime_context.perception, structured=structured_payload)
        if runtime_context is not None:
            normalized.meta["resolution"] = dict(runtime_context.perception.metadata.get("resolution") or {})
        normalized.meta["fairy"] = self._response_fairy_meta(
            payload=payload,
            meta=normalized.meta,
            text=normalized.text_reply,
            cards=normalized.cards,
            errors=list(normalized.errors),
            resolution=dict(normalized.meta.get("resolution") or {}),
            selected_capability=str(payload.get("skill_name") or ""),
        )
        logger.info(
            "runtime_v2_normalized request_id=%s intent=%s cards=%s speech_mode=%s speech_route=%s",
            request_id,
            normalized.intent,
            ",".join(card.type for card in normalized.card_payloads),
            speech_decision.mode,
            route,
        )
        return normalized

    def _build_runtime_execution_plan_from_resolution(self, resolution: dict[str, Any]) -> RuntimeExecutionPlan | None:
        if str(resolution.get("execution_mode") or "single") != "sequential":
            return None
        intents: list[dict[str, Any]] = []
        primary = resolution.get("primary_intent")
        if isinstance(primary, dict) and primary.get("capability"):
            intents.append(primary)
        for item in list(resolution.get("secondary_intents") or []):
            if isinstance(item, dict) and item.get("capability"):
                intents.append(item)
        if len(intents) <= 1:
            return None
        steps: list[RuntimeExecutionStep] = []
        step_ids_by_index: dict[int, str] = {}
        for index, item in enumerate(intents):
            capability = str(item.get("capability") or "").strip()
            if not capability:
                continue
            step = RuntimeExecutionStep(
                capability=capability,
                slots=dict(item.get("slots") or {}),
                step_index=index,
                normalized_query=str(item.get("normalized_query") or "").strip(),
                retryable=True,
                can_run_parallel=bool(item.get("can_run_parallel")),
                branch_key=f"branch_{index}" if bool(item.get("can_run_parallel")) else "main",
                dependency_mode=str(item.get("dependency_mode") or "success"),
                execution_confidence_score=float(item.get("confidence") or 0.72),
            )
            steps.append(step)
            step_ids_by_index[index] = step.step_id
        for step, item in zip(steps, intents):
            dependency_indexes = [int(value) for value in list(item.get("depends_on") or []) if str(value).strip()]
            step.depends_on = [step_ids_by_index[idx] for idx in dependency_indexes if idx in step_ids_by_index]
        if len(steps) <= 1:
            return None
        return RuntimeExecutionPlan(
            steps=steps,
            allow_partial_failure=True,
            continuation_of_plan_id=str(resolution.get("continuation_plan_id") or ""),
            graph_mode="graph" if any(step.can_run_parallel or step.depends_on for step in steps) else "linear",
            parallel_enabled=any(step.can_run_parallel for step in steps),
        )

    def _build_continuation_plan_from_resolution(self, resolution: dict[str, Any]) -> RuntimeExecutionPlan | None:
        if not resolution.get("plan_continuation"):
            return None
        primary = resolution.get("primary_intent")
        if not isinstance(primary, dict) or not primary.get("capability"):
            return None
        step = RuntimeExecutionStep(
            capability=str(primary.get("capability") or "").strip(),
            slots=dict(primary.get("slots") or {}),
            step_index=0,
            normalized_query=str(primary.get("normalized_query") or resolution.get("normalized_query") or "").strip(),
            retryable=True,
            execution_confidence_score=float(primary.get("confidence") or 0.72),
        )
        if not step.capability:
            return None
        return RuntimeExecutionPlan(
            steps=[step],
            allow_partial_failure=True,
            continuation_of_plan_id=str(resolution.get("continuation_plan_id") or ""),
            graph_mode="linear",
            parallel_enabled=False,
        )

    def _request_plan_for_capability(self, capability: str, *, planner_confidence: float, context_payload: dict[str, Any]) -> ResponseRequestPlan:
        intent = {
            "weather_lookup": "weather",
            "time_lookup": "time",
            "location_lookup": "location",
            "display_information": "location",
            "news_lookup": "news",
            "generic_search": "generic_search",
            "explanation": "explanation",
            "system_action": "system_action",
        }.get(capability, "text")
        modality = "text_only"
        force_card_type = ""
        if capability == "weather_lookup":
            modality = "text_plus_card"
            force_card_type = "weather"
        elif capability == "time_lookup":
            modality = "text_plus_card"
            force_card_type = "time"
        elif capability in {"location_lookup", "display_information"}:
            modality = "card_primary_text_summary"
            force_card_type = "location"
        elif capability == "news_lookup":
            modality = "text_plus_card"
            force_card_type = "news_list"
        elif capability == "system_action":
            modality = "text_only"
        return ResponseRequestPlan(
            intent=intent,
            modality=modality,  # type: ignore[arg-type]
            speech_mode="summary_first" if capability in {"weather_lookup", "time_lookup", "location_lookup", "display_information", "news_lookup", "system_action"} else "detailed_explainer",
            allow_voice_streaming=False,
            planner_confidence=planner_confidence,
            force_card_type=force_card_type,
            context_payload=dict(context_payload or {}),
        )

    def _create_plan_state(self, *, session_id: str, plan: RuntimeExecutionPlan, plan_id: str) -> RuntimeExecutionPlanState:
        context = self.context_manager.get_or_create(session_id)
        if plan.continuation_of_plan_id and context.active_execution_plan_state:
            existing = dict(context.active_execution_plan_state)
            steps_payload = list(existing.get("steps") or [])
            current_steps = [
                RuntimeExecutionStep(
                    capability=str(item.get("capability") or "").strip(),
                    slots=dict(item.get("slots") or {}),
                    step_index=int(item.get("step_index") or 0),
                    normalized_query=str(item.get("normalized_query") or "").strip(),
                    retryable=bool(item.get("retryable", True)),
                    step_id=str(item.get("step_id") or ""),
                    depends_on=[str(value).strip() for value in list(item.get("depends_on") or []) if str(value).strip()],
                    can_run_parallel=bool(item.get("can_run_parallel")),
                    abort_group=str(item.get("abort_group") or ""),
                    branch_key=str(item.get("branch_key") or "main"),
                    dependency_mode=str(item.get("dependency_mode") or "success"),
                    execution_confidence_score=float(item.get("execution_confidence_score") or 0.72),
                    inserted_by=str(item.get("inserted_by") or ""),
                    rewrite_source=str(item.get("rewrite_source") or ""),
                    alternative_capabilities=[str(value).strip() for value in list(item.get("alternative_capabilities") or []) if str(value).strip()],
                )
                for item in steps_payload
                if str(item.get("capability") or "").strip()
            ]
            for step in plan.steps:
                cloned = RuntimeExecutionStep(
                    capability=step.capability,
                    slots=dict(step.slots),
                    step_index=len(current_steps),
                    normalized_query=step.normalized_query,
                    retryable=step.retryable,
                    step_id=step.step_id,
                    depends_on=list(step.depends_on),
                    can_run_parallel=step.can_run_parallel,
                    abort_group=step.abort_group,
                    branch_key=step.branch_key,
                    dependency_mode=step.dependency_mode,
                    execution_confidence_score=step.execution_confidence_score,
                    inserted_by=step.inserted_by,
                    rewrite_source=step.rewrite_source,
                    alternative_capabilities=list(step.alternative_capabilities),
                )
                current_steps.append(cloned)
            existing_results = [
                StepExecutionResult(
                    step_id=str(item.get("step_id") or ""),
                    capability=str(item.get("capability") or "").strip(),
                    success=bool(item.get("success")),
                    output_summary=str(item.get("output_summary") or "").strip() or None,
                    produced_entities=[str(value).strip() for value in list(item.get("produced_entities") or []) if str(value).strip()],
                    error_type=str(item.get("error_type") or "").strip() or None,
                    skipped=bool(item.get("skipped")),
                    retry_count=int(item.get("retry_count") or 0),
                    execution_confidence_score=float(item.get("execution_confidence_score") or 0.0),
                    confidence_factors=[str(value).strip() for value in list(item.get("confidence_factors") or []) if str(value).strip()],
                    branch_key=str(item.get("branch_key") or "main"),
                    inserted_steps=[str(value).strip() for value in list(item.get("inserted_steps") or []) if str(value).strip()],
                    metadata=dict(item.get("metadata") or {}),
                )
                for item in list(existing.get("results") or [])
                if str(item.get("capability") or "").strip()
            ]
            state = RuntimeExecutionPlanState(
                plan_id=str(existing.get("plan_id") or plan.continuation_of_plan_id or plan_id),
                steps=current_steps,
                current_index=int(existing.get("current_index") or len(steps_payload) or 0),
                status="running",
                results=existing_results,
                step_statuses={str(key): str(value) for key, value in dict(existing.get("step_statuses") or {}).items()},
                rewrite_log=list(existing.get("rewrite_log") or []),
                branch_statuses={str(key): str(value) for key, value in dict(existing.get("branch_statuses") or {}).items()},
                active_batch=[str(value) for value in list(existing.get("active_batch") or []) if str(value).strip()],
            )
        else:
            state = RuntimeExecutionPlanState(
                plan_id=plan_id,
                steps=[
                    RuntimeExecutionStep(
                        capability=step.capability,
                        slots=dict(step.slots),
                        step_index=index,
                        normalized_query=step.normalized_query,
                        retryable=step.retryable,
                        step_id=step.step_id,
                        depends_on=list(step.depends_on),
                        can_run_parallel=step.can_run_parallel,
                        abort_group=step.abort_group,
                        branch_key=step.branch_key,
                        dependency_mode=step.dependency_mode,
                        execution_confidence_score=step.execution_confidence_score,
                        inserted_by=step.inserted_by,
                        rewrite_source=step.rewrite_source,
                        alternative_capabilities=list(step.alternative_capabilities),
                    )
                    for index, step in enumerate(plan.steps)
                ],
                current_index=0,
                status="running",
                results=[],
            )
        context.active_execution_plan_state = state.to_dict()
        return state

    def _persist_plan_state(self, session_id: str, state: RuntimeExecutionPlanState) -> None:
        context = self.context_manager.get_or_create(session_id)
        context.active_execution_plan_state = state.to_dict()

    def _extract_step_entities(self, *, step: RuntimeExecutionStep, normalized: NormalizedAssistantResponse, payload: dict[str, Any]) -> list[str]:
        entities: list[str] = []
        if bool(payload.get("_desktop_automation_compatibility")):
            entities.append("desktop_automation_call")
        for key in ("location", "topic", "query", "action_name"):
            value = str(step.slots.get(key) or "").strip()
            if value and not self._is_noise_entity_value(value):
                entities.append(value)
        structured = dict(payload.get("structured") or {}) if isinstance(payload.get("structured"), dict) else {}
        for key in ("city", "title", "topic", "weather_location", "address", "query"):
            value = str(structured.get(key) or "").strip()
            if value and not self._is_noise_entity_value(value):
                entities.append(value)
        for card in normalized.cards:
            if not isinstance(card, dict):
                continue
            data = dict(card.get("data") or {})
            for key in ("city", "title", "topic", "address"):
                value = str(data.get(key) or "").strip()
                if value and not self._is_noise_entity_value(value):
                    entities.append(value)
        deduped: list[str] = []
        seen: set[str] = set()
        for item in entities:
            if item and item not in seen:
                seen.add(item)
                deduped.append(item)
        return deduped[:4]

    @staticmethod
    def _is_noise_entity_value(value: str) -> bool:
        cleaned = str(value or "").strip()
        if not cleaned:
            return False
        lowered = cleaned.lower()
        noisy_tokens = (
            "web access failed",
            "error_card",
            "type: error_card",
            "runtime_stream_error",
            "no running session file found",
            "message:",
            "traceback",
            "exception",
            "实时数据暂不可用",
            "data\\runtime\\fairy_desktop_dev.json",
        )
        if any(token in lowered for token in noisy_tokens):
            return True
        if re.search(r"[a-z]:\\", lowered):
            return True
        if ".json" in lowered and ("runtime" in lowered or "session" in lowered):
            return True
        return False

    def _should_retry_step(self, step: RuntimeExecutionStep, errors: list[dict[str, Any]]) -> tuple[bool, str]:
        if not step.retryable or not errors:
            return False, ""
        error = errors[0]
        code = str(error.get("code") or "").strip().lower()
        message = str(error.get("message") or "").strip().lower()
        retryable_tokens = ("timeout", "network", "connection", "temporar", "unavailable")
        if any(token in code for token in retryable_tokens) or any(token in message for token in retryable_tokens):
            return True, code or message or "retryable_error"
        return False, ""

    def _should_skip_step(self, step: RuntimeExecutionStep) -> tuple[bool, str]:
        contract = self.query_resolver.contract_registry.get(step.capability)
        if contract is None:
            return False, ""
        missing = [slot for slot in contract.required_slots if not str(step.slots.get(slot) or "").strip()]
        if missing:
            return True, f"missing_required_slots:{','.join(missing)}"
        if not step.normalized_query and step.capability not in {"system_action"}:
            return True, "empty_normalized_query"
        return False, ""

    @staticmethod
    def _step_request_id(request_id: str, step: RuntimeExecutionStep) -> str:
        return f"{request_id}:{step.step_id}"

    def _route_hints_for_step(self, request_id: str, step: RuntimeExecutionStep) -> dict[str, Any]:
        route_hints = dict(self.get_route_hints(request_id))
        route_hints.update(
            {
                "resolved_query": step.normalized_query,
                "resolved_slots": dict(step.slots),
                "orchestration_step_id": step.step_id,
                "orchestration_step_capability": step.capability,
                "orchestration_step_confidence": step.execution_confidence_score,
            }
        )
        return route_hints

    @staticmethod
    def _confidence_clamp(value: float) -> float:
        return max(0.0, min(1.0, value))

    def _estimate_step_confidence(
        self,
        *,
        step: RuntimeExecutionStep,
        normalized: NormalizedAssistantResponse | None,
        payload: dict[str, Any] | None,
        retry_count: int,
        skipped: bool,
    ) -> tuple[float, list[str]]:
        if skipped:
            return 0.0, ["skipped"]
        score = float(step.execution_confidence_score or 0.72)
        factors: list[str] = [f"base={score:.2f}"]
        payload = dict(payload or {})
        normalized = normalized or NormalizedAssistantResponse(intent="text", modality="text_only", text_reply="")
        if normalized.text_reply:
            score += 0.05
            factors.append("text_reply")
        if normalized.card_payloads:
            score += 0.08
            factors.append("card_payload")
        produced = self._extract_step_entities(step=step, normalized=normalized, payload=payload)
        if produced:
            score += 0.06
            factors.append("produced_entities")
        if normalized.errors:
            score -= 0.35
            factors.append("has_errors")
        if retry_count:
            score -= 0.12 * retry_count
            factors.append(f"retry_penalty={retry_count}")
        if payload.get("_bundle_fallback"):
            score -= 0.1
            factors.append("bundle_fallback_penalty")
        return self._confidence_clamp(score), factors

    def _build_step_execution_result(
        self,
        *,
        step: RuntimeExecutionStep,
        normalized: NormalizedAssistantResponse | None,
        payload: dict[str, Any] | None,
        skipped: bool = False,
        skip_reason: str = "",
        retry_count: int = 0,
    ) -> StepExecutionResult:
        if skipped:
            confidence_score, confidence_factors = self._estimate_step_confidence(
                step=step,
                normalized=None,
                payload=None,
                retry_count=retry_count,
                skipped=True,
            )
            return StepExecutionResult(
                step_id=step.step_id,
                capability=step.capability,
                success=False,
                output_summary=skip_reason or None,
                produced_entities=[],
                error_type=skip_reason or "skipped",
                skipped=True,
                retry_count=retry_count,
                execution_confidence_score=confidence_score,
                confidence_factors=confidence_factors,
                branch_key=step.branch_key,
            )
        payload = dict(payload or {})
        normalized = normalized or NormalizedAssistantResponse(intent="text", modality="text_only", text_reply="")
        errors = list(normalized.errors)
        produced_entities = self._extract_step_entities(step=step, normalized=normalized, payload=payload)
        confidence_score, confidence_factors = self._estimate_step_confidence(
            step=step,
            normalized=normalized,
            payload=payload,
            retry_count=retry_count,
            skipped=False,
        )
        return StepExecutionResult(
            step_id=step.step_id,
            capability=step.capability,
            success=not errors,
            output_summary=normalized.text_reply or str(payload.get("summary") or "").strip() or None,
            produced_entities=produced_entities,
            error_type=str((errors[0] if errors else {}).get("code") or "").strip() or None,
            skipped=False,
            retry_count=retry_count,
            execution_confidence_score=confidence_score,
            confidence_factors=confidence_factors,
            branch_key=step.branch_key,
            metadata={
                "executor_path": str(payload.get("_runtime_executor_path") or ""),
                "bundle_fallback": bool(payload.get("_bundle_fallback")),
            },
        )

    def _apply_step_result_to_session_context(self, session_id: str, step: RuntimeExecutionStep, result: StepExecutionResult) -> None:
        context = self.context_manager.get_or_create(session_id)
        context.last_step_capability = step.capability
        if "desktop_automation_call" in result.produced_entities:
            context.flags["used_desktop_automation_compatibility"] = True
        if not result.success:
            return
        primary = next((item for item in result.produced_entities if not self._is_noise_entity_value(item)), "")
        if primary:
            if step.capability in {"location_lookup", "display_information"}:
                context.active_location_target = primary
                context.active_city = primary
            if step.capability in {"weather_lookup", "time_lookup"}:
                context.active_location_target = primary
                context.active_weather_location = primary
                context.active_city = primary
            if step.capability in {"generic_search", "news_lookup", "explanation"}:
                context.active_topic = primary
        if step.capability == "generic_search" and result.output_summary:
            query_value = str(step.slots.get("query") or "").strip()
            if query_value and not self._is_noise_entity_value(query_value):
                context.last_generic_query = query_value
        if step.capability == "explanation" and primary:
            context.last_explanation_topic = primary

    def _remember_completed_single_step_plan(
        self,
        *,
        session_id: str,
        request_id: str,
        capability: str,
        normalized_query: str,
        slots: dict[str, Any],
        normalized: NormalizedAssistantResponse,
        payload: dict[str, Any],
    ) -> None:
        step = RuntimeExecutionStep(
            capability=capability,
            slots={str(key): str(value) for key, value in dict(slots or {}).items() if str(value).strip()},
            step_index=0,
            normalized_query=normalized_query,
            retryable=True,
        )
        state = RuntimeExecutionPlanState(
            plan_id=request_id,
            steps=[step],
            current_index=1,
            status="failed" if normalized.errors else "completed",
            results=[],
        )
        result = self._build_step_execution_result(step=step, normalized=normalized, payload=payload, retry_count=0)
        state.results.append(result)
        state.mark_step_status(step.step_id, "failed" if normalized.errors else "completed")
        state.sync_branch_statuses()
        self._persist_plan_state(session_id, state)
        self._apply_step_result_to_session_context(session_id, step, result)
        self._update_session_execution_context(
            session_id,
            RuntimeExecutionPlan(steps=[step], allow_partial_failure=True),
            state=state,
        )

    def _execute_step_invoke(
        self,
        *,
        step: RuntimeExecutionStep,
        parent_request_id: str,
        session_id: str,
        request_plan: ResponseRequestPlan,
        bundle_executor: BundleExecutor | None,
        attachments: list[str],
        request_origin: str,
        original_message: str,
    ) -> dict[str, Any]:
        step_request_id = self._step_request_id(parent_request_id, step)
        step_plan = self._request_plan_for_capability(
            step.capability,
            planner_confidence=request_plan.planner_confidence,
            context_payload=request_plan.context_payload,
        )
        retry_count = 0
        retry_reason = ""
        while True:
            payload = self._invoke_capability(
                capability_name=step.capability,
                message=step.normalized_query or original_message,
                request_id=step_request_id,
                session_id=session_id,
                bundle_executor=bundle_executor,
                attachments=attachments,
                request_origin=request_origin,
                route_hints=self._route_hints_for_step(parent_request_id, step),
            )
            normalized = self.normalize_result(
                user_text=step.normalized_query or original_message,
                payload=payload,
                assistant_text=str(payload.get("assistant_text") or payload.get("text") or "").strip(),
                assistant_html=str(payload.get("assistant_html") or "").strip(),
                request_plan=step_plan,
                session_id=session_id,
                request_id=step_request_id,
            )
            should_retry, retry_reason = self._should_retry_step(step, list(normalized.errors))
            if should_retry and retry_count < 1:
                retry_count += 1
                continue
            break
        step_result = self._build_step_execution_result(
            step=step,
            normalized=normalized,
            payload=payload,
            retry_count=retry_count,
        )
        return {
            "step": step,
            "payload": payload,
            "normalized": normalized,
            "step_result": step_result,
            "retry_count": retry_count,
            "retry_reason": retry_reason,
        }

    @staticmethod
    def _coerce_step_error(normalized: NormalizedAssistantResponse) -> dict[str, Any] | None:
        return normalized.errors[0] if normalized.errors else None

    def _apply_plan_extension_decision(
        self,
        *,
        plan: RuntimeExecutionPlan,
        state: RuntimeExecutionPlanState,
        decision: Any,
    ) -> list[str]:
        inserted_step_ids: list[str] = []
        if getattr(decision, "updated_steps", None):
            updated_map = {step.step_id: step for step in decision.updated_steps}
            if updated_map:
                for index, existing in enumerate(list(plan.steps)):
                    replacement = updated_map.get(existing.step_id)
                    if replacement is None:
                        continue
                    replacement.step_index = existing.step_index
                    plan.steps[index] = replacement
                for index, existing in enumerate(list(state.steps)):
                    replacement = updated_map.get(existing.step_id)
                    if replacement is None:
                        continue
                    replacement.step_index = existing.step_index
                    state.steps[index] = replacement
        if getattr(decision, "cancelled_step_ids", None):
            for step_id in decision.cancelled_step_ids:
                if step_id in state.step_statuses and state.step_statuses.get(step_id) == "pending":
                    state.mark_step_status(step_id, "cancelled")
        if getattr(decision, "inserted_steps", None):
            start_index = len(plan.steps)
            for offset, inserted in enumerate(decision.inserted_steps):
                inserted.step_index = start_index + offset
                plan.steps.append(inserted)
                state.append_steps([inserted])
                inserted_step_ids.append(inserted.step_id)
        if getattr(decision, "reasoning", None):
            state.rewrite_log.extend(list(decision.reasoning))
        state.sync_branch_statuses()
        return inserted_step_ids

    def _finalize_batch_result(
        self,
        *,
        session_id: str,
        plan: RuntimeExecutionPlan,
        state: RuntimeExecutionPlanState,
        message: str,
        step_result: StepExecutionResult,
        step: RuntimeExecutionStep,
        reasoning: list[dict[str, Any]],
    ) -> list[str]:
        self._apply_step_result_to_session_context(session_id, step, step_result)
        decision = self.plan_extension_hook.extend(
            original_message=message,
            step=step,
            result=step_result,
            current_state=state,
            session_context=self.context_manager.get_or_create(session_id),
        )
        inserted_step_ids = self._apply_plan_extension_decision(plan=plan, state=state, decision=decision)
        if decision.reasoning:
            reasoning.extend(list(decision.reasoning))
        return inserted_step_ids

    def _mark_failed_dependency_steps(
        self,
        *,
        plan: RuntimeExecutionPlan,
        state: RuntimeExecutionPlanState,
        reasoning: list[dict[str, Any]],
    ) -> list[RuntimeExecutionStep]:
        blocked = plan.blocked_by_failed_dependencies(
            completed=state.completed_step_ids,
            failed=state.failed_step_ids,
            skipped=state.skipped_step_ids,
        )
        skipped_steps: list[RuntimeExecutionStep] = []
        for step in blocked:
            if state.step_statuses.get(step.step_id) != "pending":
                continue
            state.mark_step_status(step.step_id, "skipped")
            skipped_steps.append(step)
            state.results.append(
                self._build_step_execution_result(
                    step=step,
                    normalized=None,
                    payload=None,
                    skipped=True,
                    skip_reason="dependency_failed",
                )
            )
            reasoning.append(
                {
                    "capability": step.capability,
                    "why_skipped": "dependency_failed",
                    "depends_on": list(step.depends_on),
                }
            )
        if skipped_steps:
            state.sync_branch_statuses()
        return skipped_steps

    def _next_step_batch(
        self,
        *,
        plan: RuntimeExecutionPlan,
        state: RuntimeExecutionPlanState,
    ) -> list[RuntimeExecutionStep]:
        return plan.next_parallel_batch(
            completed=state.completed_step_ids,
            failed=state.failed_step_ids,
            skipped=state.skipped_step_ids,
        )

    def _invoke_orchestration(
        self,
        *,
        request_id: str,
        session_id: str,
        message: str,
        request_plan: ResponseRequestPlan,
        resolution: dict[str, Any],
        plan: RuntimeExecutionPlan,
        attachments: list[str],
        bundle_executor: BundleExecutor | None,
        request_origin: str,
        include_compat_fields: bool,
    ) -> dict[str, Any]:
        texts: list[str] = []
        cards: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        step_results: list[dict[str, Any]] = []
        reasoning: list[dict[str, Any]] = []
        last_payload: dict[str, Any] = {}
        plan_state = self._create_plan_state(session_id=session_id, plan=plan, plan_id=request_id)
        self._set_runtime_state("analyzing", reason="orchestration_plan_built", request_id=request_id, session_id=session_id)
        while True:
            blocked = self._mark_failed_dependency_steps(plan=plan, state=plan_state, reasoning=reasoning)
            for skipped_step in blocked:
                step_results.append(
                    {
                        "step_id": skipped_step.step_id,
                        "capability": skipped_step.capability,
                        "success": False,
                        "error": {"code": "step_skipped", "message": "dependency_failed"},
                        "skipped": True,
                        "retry_count": 0,
                        "produced_entities": [],
                        "execution_confidence_score": 0.0,
                        "inserted_steps": [],
                    }
                )
            batch = self._next_step_batch(plan=plan, state=plan_state)
            if not batch:
                break
            plan_state.active_batch = [step.step_id for step in batch]
            for step in batch:
                plan_state.mark_step_status(step.step_id, "running")
            plan_state.sync_branch_statuses()
            self._persist_plan_state(session_id, plan_state)
            batch_outcomes: list[dict[str, Any]] = []
            if len(batch) == 1:
                step = batch[0]
                skip_step, skip_reason = self._should_skip_step(step)
                if skip_step:
                    step_result = self._build_step_execution_result(
                        step=step,
                        normalized=None,
                        payload=None,
                        skipped=True,
                        skip_reason=skip_reason,
                    )
                    batch_outcomes.append(
                        {
                            "step": step,
                            "payload": {},
                            "normalized": None,
                            "step_result": step_result,
                            "retry_count": 0,
                            "retry_reason": "",
                            "skip_reason": skip_reason,
                        }
                    )
                else:
                    batch_outcomes.append(
                        self._execute_step_invoke(
                            step=step,
                            parent_request_id=request_id,
                            session_id=session_id,
                            request_plan=request_plan,
                            bundle_executor=bundle_executor,
                            attachments=attachments,
                            request_origin=request_origin,
                            original_message=message,
                        )
                    )
            else:
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(batch), thread_name_prefix="fairy-orch") as pool:
                    future_map = {
                        pool.submit(
                            self._execute_step_invoke,
                            step=step,
                            parent_request_id=request_id,
                            session_id=session_id,
                            request_plan=request_plan,
                            bundle_executor=bundle_executor,
                            attachments=attachments,
                            request_origin=request_origin,
                            original_message=message,
                        ): step
                        for step in batch
                    }
                    for future in concurrent.futures.as_completed(future_map):
                        batch_outcomes.append(future.result())
            for outcome in sorted(batch_outcomes, key=lambda item: int(item["step"].step_index)):
                step = outcome["step"]
                skip_reason = str(outcome.get("skip_reason") or "").strip()
                if skip_reason:
                    step_result = outcome["step_result"]
                    plan_state.results.append(step_result)
                    plan_state.mark_step_status(step.step_id, "skipped")
                    reasoning.append({"capability": step.capability, "why_skipped": skip_reason})
                    step_results.append(
                        {
                            "step_id": step.step_id,
                            "capability": step.capability,
                            "success": False,
                            "error": {"code": "step_skipped", "message": skip_reason},
                            "skipped": True,
                            "retry_count": 0,
                            "produced_entities": [],
                            "execution_confidence_score": step_result.execution_confidence_score,
                            "inserted_steps": [],
                        }
                    )
                    continue
                normalized = outcome["normalized"]
                payload = outcome["payload"]
                step_result = outcome["step_result"]
                retry_count = int(outcome.get("retry_count") or 0)
                retry_reason = str(outcome.get("retry_reason") or "").strip()
                if retry_count:
                    reasoning.append(
                        {
                            "capability": step.capability,
                            "why_retry": retry_reason or "retryable_error",
                            "retry_count": retry_count,
                        }
                    )
                if normalized.text_reply:
                    self._set_runtime_state("replying", reason=f"step_output:{step.capability}", request_id=request_id, session_id=session_id)
                    texts.append(normalized.text_reply)
                if normalized.cards:
                    self._set_runtime_state("replying", reason=f"step_card:{step.capability}", request_id=request_id, session_id=session_id)
                cards.extend(normalized.cards)
                errors.extend(list(normalized.errors))
                plan_state.results.append(step_result)
                plan_state.mark_step_status(step.step_id, "completed" if step_result.success else "failed")
                inserted_step_ids = self._finalize_batch_result(
                    session_id=session_id,
                    plan=plan,
                    state=plan_state,
                    message=message,
                    step_result=step_result,
                    step=step,
                    reasoning=reasoning,
                )
                step_result.inserted_steps.extend(inserted_step_ids)
                step_results.append(
                    {
                        "step_id": step.step_id,
                        "capability": step.capability,
                        "success": step_result.success,
                        "error": self._coerce_step_error(normalized),
                        "skipped": False,
                        "retry_count": retry_count,
                        "produced_entities": list(step_result.produced_entities),
                        "execution_confidence_score": step_result.execution_confidence_score,
                        "inserted_steps": list(inserted_step_ids),
                    }
                )
                last_payload = payload
                if not step_result.success and not plan.allow_partial_failure:
                    plan_state.status = "failed"
            plan_state.current_index = len(plan_state.completed_step_ids | plan_state.failed_step_ids | plan_state.skipped_step_ids)
            plan_state.active_batch = []
            plan_state.sync_branch_statuses()
            self._persist_plan_state(session_id, plan_state)
            if plan_state.status == "failed":
                break
        if plan_state.status not in {"failed", "cancelled"}:
            plan_state.status = "completed"
            self._persist_plan_state(session_id, plan_state)

        summary_card = build_orchestration_summary_card(
            step_results=step_results,
            text_blocks=texts,
            reasoning=reasoning,
            graph_mode=plan.graph_mode,
        )
        if summary_card is not None:
            self._set_runtime_state("replying", reason="orchestration_summary_ready", request_id=request_id, session_id=session_id)
            cards.append(summary_card)

        self._set_runtime_state("idle", reason="orchestration_completed", request_id=request_id, session_id=session_id)
        meta_runtime = {
            "selected_capability": plan.steps[0].capability,
            "executor_path": "adaptive_orchestration_graph" if plan.graph_mode == "graph" else "sequential_orchestration",
            "used_bundle_fallback": False,
            "request_origin": request_origin,
            "compat_fields_emitted": bool(include_compat_fields),
            "desktop_automation_compatibility": any(
                "desktop_automation_call" in list(item.produced_entities) for item in plan_state.results
            ),
            "orchestration": {
                "execution_mode": "sequential",
                "plan_id": plan_state.plan_id,
                "status": plan_state.status,
                "graph_mode": plan.graph_mode,
                "parallel_enabled": plan.parallel_enabled,
                "step_results": [item.to_dict() for item in plan_state.results],
                "rewrite_log": list(plan_state.rewrite_log),
                "branch_statuses": dict(plan_state.branch_statuses),
            },
            "orchestration_reasoning": {
                "step_decisions": reasoning,
            },
        }
        contract = {
            "request_id": request_id,
            "session_id": session_id,
            "text": "\n\n".join(item for item in texts if item).strip(),
            "cards": cards,
            "meta": {
                "intent": request_plan.intent,
                "modality": request_plan.modality,
                "speech": {"mode": request_plan.speech_mode, "text": "", "allow_streaming": False},
                "resolution": resolution,
                "runtime_state": self.runtime_state_snapshot(),
                "runtime": meta_runtime,
                "fairy": self._response_fairy_meta(
                    payload=last_payload,
                    text="\n\n".join(item for item in texts if item).strip(),
                    cards=cards,
                    errors=errors,
                    resolution=resolution,
                    selected_capability=plan.steps[0].capability if plan.steps else "bundle-runtime",
                ),
            },
            "errors": errors,
        }
        if include_compat_fields:
            contract.update(
                {
                    "assistant_text": contract["text"],
                    "assistant_html": "",
                    "summary": contract["text"],
                    "sources": list(last_payload.get("sources") or []),
                    "warnings": list(last_payload.get("warnings") or []),
                    "structured": dict(last_payload.get("structured") or {}),
                    "skill_name": plan_state.steps[-1].capability if plan_state.steps else "",
                    "success": not errors,
                }
            )
        self._update_session_execution_context(session_id, plan, state=plan_state)
        self.context_manager.clear_clarification(session_id)
        if errors:
            self.system_bridge.set_last_error(str((errors or [{}])[0].get("message") or ""), request_id=request_id, session_id=session_id)
        else:
            self.system_bridge.set_last_error("")
        return contract

    def _stream_orchestration(
        self,
        *,
        request_id: str,
        session_id: str,
        message: str,
        request_plan: ResponseRequestPlan,
        resolution: dict[str, Any],
        plan: RuntimeExecutionPlan,
        attachments: list[str],
        bundle_executor: BundleExecutor | None,
        request_origin: str,
        cancel_event: threading.Event | None,
    ) -> Iterable[StreamEventBase]:
        plan_state = self._create_plan_state(session_id=session_id, plan=plan, plan_id=request_id)
        self._set_runtime_state("analyzing", reason="orchestration_plan_built", request_id=request_id, session_id=session_id)
        yield ProgressStreamEvent(
            request_id=request_id,
            session_id=session_id,
            stage="orchestration_plan_built",
            text=f"Built {plan.graph_mode} execution plan with {len(plan_state.steps)} steps.",
        )
        text_parts: list[str] = []
        cards: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        step_results: list[dict[str, Any]] = []
        reasoning: list[dict[str, Any]] = []
        while True:
            if cancel_event is not None and cancel_event.is_set():
                plan_state.status = "cancelled"
                break
            blocked = self._mark_failed_dependency_steps(plan=plan, state=plan_state, reasoning=reasoning)
            for skipped_step in blocked:
                step_results.append(
                    {
                        "step_id": skipped_step.step_id,
                        "capability": skipped_step.capability,
                        "success": False,
                        "error": {"code": "step_skipped", "message": "dependency_failed"},
                        "skipped": True,
                        "retry_count": 0,
                        "produced_entities": [],
                        "execution_confidence_score": 0.0,
                        "inserted_steps": [],
                    }
                )
                yield ProgressStreamEvent(
                    request_id=request_id,
                    session_id=session_id,
                    stage="orchestration_step_skipped",
                    text=f"Skipped step {skipped_step.step_index + 1}: dependency_failed",
                )
            batch = self._next_step_batch(plan=plan, state=plan_state)
            if not batch:
                break
            plan_state.active_batch = [step.step_id for step in batch]
            for step in batch:
                plan_state.mark_step_status(step.step_id, "running")
                yield ProgressStreamEvent(
                    request_id=request_id,
                    session_id=session_id,
                    stage="orchestration_step_start",
                    text=f"Starting step {step.step_index + 1}: {step.capability}",
                )
            plan_state.sync_branch_statuses()
            self._persist_plan_state(session_id, plan_state)
            if len(batch) == 1 and not batch[0].can_run_parallel:
                step = batch[0]
                skip_step, skip_reason = self._should_skip_step(step)
                if skip_step:
                    step_result = self._build_step_execution_result(
                        step=step,
                        normalized=None,
                        payload=None,
                        skipped=True,
                        skip_reason=skip_reason,
                    )
                    plan_state.results.append(step_result)
                    plan_state.mark_step_status(step.step_id, "skipped")
                    reasoning.append({"capability": step.capability, "why_skipped": skip_reason})
                    step_results.append(
                        {
                            "step_id": step.step_id,
                            "capability": step.capability,
                            "success": False,
                            "error": {"code": "step_skipped", "message": skip_reason},
                            "skipped": True,
                            "retry_count": 0,
                            "produced_entities": [],
                            "execution_confidence_score": step_result.execution_confidence_score,
                            "inserted_steps": [],
                        }
                    )
                    yield ProgressStreamEvent(
                        request_id=request_id,
                        session_id=session_id,
                        stage="orchestration_step_skipped",
                        text=f"Skipped step {step.step_index + 1}: {skip_reason}",
                    )
                else:
                    step_plan = self._request_plan_for_capability(
                        step.capability,
                        planner_confidence=request_plan.planner_confidence,
                        context_payload=request_plan.context_payload,
                    )
                    retry_count = 0
                    payload = {}
                    while True:
                        normalized = yield from self._stream_capability_step(
                            capability_name=step.capability,
                            message=step.normalized_query or message,
                            request_id=self._step_request_id(request_id, step),
                            session_id=session_id,
                            request_plan=step_plan,
                            bundle_executor=bundle_executor,
                            attachments=attachments,
                            request_origin=request_origin,
                            cancel_event=cancel_event,
                            route_hints=self._route_hints_for_step(request_id, step),
                        )
                        if normalized is None:
                            plan_state.status = "cancelled"
                            self._persist_plan_state(session_id, plan_state)
                            break
                        should_retry, retry_reason = self._should_retry_step(step, list(normalized.errors))
                        if should_retry and retry_count < 1 and not normalized.text_reply and not normalized.cards:
                            retry_count += 1
                            reasoning.append(
                                {
                                    "capability": step.capability,
                                    "why_retry": retry_reason or "retryable_error",
                                    "retry_count": retry_count,
                                }
                            )
                            yield ProgressStreamEvent(
                                request_id=request_id,
                                session_id=session_id,
                                stage="orchestration_step_retry",
                                text=f"Retrying step {step.step_index + 1}: {step.capability}",
                            )
                            continue
                        break
                    if normalized is None:
                        break
                    if normalized.text_reply:
                        self._set_runtime_state("replying", reason=f"step_output:{step.capability}", request_id=request_id, session_id=session_id)
                        text_parts.append(normalized.text_reply)
                    for card in normalized.cards:
                        self._set_runtime_state("replying", reason=f"step_card:{step.capability}", request_id=request_id, session_id=session_id)
                        cards.append(card)
                    step_result = self._build_step_execution_result(
                        step=step,
                        normalized=normalized,
                        payload=payload,
                        retry_count=retry_count,
                    )
                    plan_state.results.append(step_result)
                    plan_state.mark_step_status(step.step_id, "completed" if step_result.success else "failed")
                    inserted_step_ids = self._finalize_batch_result(
                        session_id=session_id,
                        plan=plan,
                        state=plan_state,
                        message=message,
                        step_result=step_result,
                        step=step,
                        reasoning=reasoning,
                    )
                    step_result.inserted_steps.extend(inserted_step_ids)
                    if normalized.errors:
                        errors.extend(list(normalized.errors))
                        yield ProgressStreamEvent(
                            request_id=request_id,
                            session_id=session_id,
                            stage="orchestration_partial_failure",
                            text=f"Step {step.step_index + 1} failed: {normalized.errors[0].get('message') or step.capability}",
                        )
                    if inserted_step_ids:
                        yield ProgressStreamEvent(
                            request_id=request_id,
                            session_id=session_id,
                            stage="orchestration_plan_extended",
                            text=f"Inserted {len(inserted_step_ids)} adaptive step(s).",
                        )
                    step_results.append(
                        {
                            "step_id": step.step_id,
                            "capability": step.capability,
                            "success": step_result.success,
                            "error": self._coerce_step_error(normalized),
                            "skipped": False,
                            "retry_count": retry_count,
                            "produced_entities": list(step_result.produced_entities),
                            "execution_confidence_score": step_result.execution_confidence_score,
                            "inserted_steps": list(inserted_step_ids),
                        }
                    )
                    yield ProgressStreamEvent(
                        request_id=request_id,
                        session_id=session_id,
                        stage="orchestration_step_complete",
                        text=f"Completed step {step.step_index + 1}: {step.capability}",
                    )
                    if not step_result.success and not plan.allow_partial_failure:
                        plan_state.status = "failed"
            else:
                yield ProgressStreamEvent(
                    request_id=request_id,
                    session_id=session_id,
                    stage="orchestration_parallel_batch",
                    text=f"Running {len(batch)} steps in parallel.",
                )
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(batch), thread_name_prefix="fairy-orch") as pool:
                    future_map = {
                        pool.submit(
                            self._execute_step_invoke,
                            step=step,
                            parent_request_id=request_id,
                            session_id=session_id,
                            request_plan=request_plan,
                            bundle_executor=bundle_executor,
                            attachments=attachments,
                            request_origin=request_origin,
                            original_message=message,
                        ): step
                        for step in batch
                    }
                    for future in concurrent.futures.as_completed(future_map):
                        if cancel_event is not None and cancel_event.is_set():
                            plan_state.status = "cancelled"
                            break
                        outcome = future.result()
                        step = outcome["step"]
                        normalized = outcome["normalized"]
                        payload = outcome["payload"]
                        step_result = outcome["step_result"]
                        retry_count = int(outcome.get("retry_count") or 0)
                        retry_reason = str(outcome.get("retry_reason") or "").strip()
                        if retry_count:
                            reasoning.append(
                                {
                                    "capability": step.capability,
                                    "why_retry": retry_reason or "retryable_error",
                                    "retry_count": retry_count,
                                }
                            )
                            yield ProgressStreamEvent(
                                request_id=request_id,
                                session_id=session_id,
                                stage="orchestration_step_retry",
                                text=f"Retried step {step.step_index + 1}: {step.capability}",
                            )
                        if normalized.text_reply:
                            self._set_runtime_state("replying", reason=f"step_output:{step.capability}", request_id=request_id, session_id=session_id)
                            text_parts.append(normalized.text_reply)
                            yield TextDeltaEvent(request_id=request_id, session_id=session_id, text=normalized.text_reply)
                        for card in normalized.cards:
                            self._set_runtime_state("replying", reason=f"step_card:{step.capability}", request_id=request_id, session_id=session_id)
                            cards.append(card)
                            yield CardStreamEvent(request_id=request_id, session_id=session_id, card=card)
                        if normalized.errors:
                            errors.extend(list(normalized.errors))
                            yield ProgressStreamEvent(
                                request_id=request_id,
                                session_id=session_id,
                                stage="orchestration_partial_failure",
                                text=f"Step {step.step_index + 1} failed: {normalized.errors[0].get('message') or step.capability}",
                            )
                        plan_state.results.append(step_result)
                        plan_state.mark_step_status(step.step_id, "completed" if step_result.success else "failed")
                        inserted_step_ids = self._finalize_batch_result(
                            session_id=session_id,
                            plan=plan,
                            state=plan_state,
                            message=message,
                            step_result=step_result,
                            step=step,
                            reasoning=reasoning,
                        )
                        step_result.inserted_steps.extend(inserted_step_ids)
                        if inserted_step_ids:
                            yield ProgressStreamEvent(
                                request_id=request_id,
                                session_id=session_id,
                                stage="orchestration_plan_extended",
                                text=f"Inserted {len(inserted_step_ids)} adaptive step(s).",
                            )
                        step_results.append(
                            {
                                "step_id": step.step_id,
                                "capability": step.capability,
                                "success": step_result.success,
                                "error": self._coerce_step_error(normalized),
                                "skipped": False,
                                "retry_count": retry_count,
                                "produced_entities": list(step_result.produced_entities),
                                "execution_confidence_score": step_result.execution_confidence_score,
                                "inserted_steps": list(inserted_step_ids),
                            }
                        )
                        yield ProgressStreamEvent(
                            request_id=request_id,
                            session_id=session_id,
                            stage="orchestration_step_complete",
                            text=f"Completed step {step.step_index + 1}: {step.capability}",
                        )
                        if not step_result.success and not plan.allow_partial_failure:
                            plan_state.status = "failed"
            plan_state.current_index = len(plan_state.completed_step_ids | plan_state.failed_step_ids | plan_state.skipped_step_ids)
            plan_state.active_batch = []
            plan_state.sync_branch_statuses()
            self._persist_plan_state(session_id, plan_state)
            if plan_state.status in {"failed", "cancelled"}:
                break
        if plan_state.status not in {"failed", "cancelled"}:
            plan_state.status = "completed"
            self._persist_plan_state(session_id, plan_state)

        final_text = "\n\n".join(part for part in text_parts if part).strip()
        summary_card = build_orchestration_summary_card(
            step_results=step_results,
            text_blocks=text_parts,
            reasoning=reasoning,
            graph_mode=plan.graph_mode,
        )
        if summary_card is not None:
            self._set_runtime_state("replying", reason="orchestration_summary_ready", request_id=request_id, session_id=session_id)
            cards.append(summary_card)
            yield CardStreamEvent(request_id=request_id, session_id=session_id, card=summary_card)
        self._set_runtime_state(
            "idle" if plan_state.status != "cancelled" else "idle",
            reason="orchestration_cancelled" if plan_state.status == "cancelled" else "orchestration_completed",
            request_id=request_id,
            session_id=session_id,
        )
        end_meta = {
            "intent": request_plan.intent,
            "modality": request_plan.modality,
            "speech": {"mode": request_plan.speech_mode, "text": "", "allow_streaming": False},
            "resolution": resolution,
            "runtime_state": self.runtime_state_snapshot(),
            "runtime": {
                "selected_capability": plan_state.steps[0].capability if plan_state.steps else "",
                "executor_path": "adaptive_orchestration_graph" if plan.graph_mode == "graph" else "sequential_orchestration",
                "used_bundle_fallback": False,
                "request_origin": request_origin,
                "compat_fields_emitted": False,
                "desktop_automation_compatibility": any(
                    "desktop_automation_call" in list(item.produced_entities) for item in plan_state.results
                ),
                "orchestration": {
                    "execution_mode": "sequential",
                    "plan_id": plan_state.plan_id,
                    "status": plan_state.status,
                    "graph_mode": plan.graph_mode,
                    "parallel_enabled": plan.parallel_enabled,
                    "step_results": [item.to_dict() for item in plan_state.results],
                    "rewrite_log": list(plan_state.rewrite_log),
                    "branch_statuses": dict(plan_state.branch_statuses),
                },
                "orchestration_reasoning": {
                    "step_decisions": reasoning,
                },
            },
        }
        end_meta["fairy"] = self._response_fairy_meta(
            meta=end_meta,
            text=final_text,
            cards=cards,
            errors=errors,
            resolution=resolution,
            selected_capability=plan_state.steps[0].capability if plan_state.steps else "bundle-runtime",
        )
        yield MessageEndEvent(
            request_id=request_id,
            session_id=session_id,
            text=final_text,
            cards=cards,
            meta=end_meta,
            errors=errors,
        )
        self.system_bridge.finish_stream(
            request_id=request_id,
            session_id=session_id,
            status="cancelled" if plan_state.status == "cancelled" else "finished",
            detail={"card_count": len(cards), "error_count": len(errors), "orchestration": True},
        )
        self._update_session_execution_context(session_id, plan, state=plan_state)
        self.context_manager.clear_clarification(session_id)
        if errors:
            self.system_bridge.set_last_error(str((errors or [{}])[0].get("message") or ""), request_id=request_id, session_id=session_id)
        else:
            self.system_bridge.set_last_error("")

    def _stream_capability_step(
        self,
        *,
        capability_name: str,
        message: str,
        request_id: str,
        session_id: str,
        request_plan: ResponseRequestPlan,
        bundle_executor: BundleExecutor | None,
        attachments: list[str],
        request_origin: str,
        cancel_event: threading.Event | None,
        route_hints: dict[str, Any] | None = None,
    ) -> Iterable[StreamEventBase]:
        emitted_text_delta = False
        execution_payload: dict[str, Any] | None = None
        active_streaming_executor = self._streaming_bundle_executor
        direct_streaming_executor = self._streaming_capability_executors.get(capability_name or "")
        effective_route_hints = dict(route_hints or self.get_route_hints(request_id))
        try:
            self._set_runtime_state("analyzing", reason=f"step_started:{capability_name or 'bundle-runtime'}", request_id=request_id, session_id=session_id)
            if cancel_event is not None and cancel_event.is_set():
                return None
            if capability_name and direct_streaming_executor is not None:
                delegate_to_bundle_runtime = False
                delegate_reason = ""
                delegated_runtime_meta: dict[str, Any] = {}
                for event in direct_streaming_executor(
                    message=message,
                    session_id=session_id,
                    request_id=request_id,
                    attachments=attachments,
                    request_origin=request_origin,
                    route_hints=effective_route_hints,
                    cancel_event=cancel_event,
                ):
                    if cancel_event is not None and cancel_event.is_set():
                        return None
                    kind = str(event.get("kind") or "").strip().lower()
                    if kind == "delegate_bundle_runtime":
                        delegate_to_bundle_runtime = True
                        delegate_reason = str(event.get("reason") or "streaming_direct_executor_requested_bundle_fallback").strip()
                        delegated_runtime_meta = {
                            "system_action_type": str(event.get("system_action_type") or ""),
                            "system_action_name": str(event.get("system_action_name") or ""),
                            "desktop_automation_compatibility": bool(event.get("desktop_automation_compatibility")),
                            "executor_path": str(
                                event.get("executor_path") or "desktop_automation_compatibility_executor"
                            ),
                        }
                        break
                    if kind == "text_delta":
                        token = str(event.get("text") or "")
                        if token:
                            emitted_text_delta = True
                            yield TextDeltaEvent(request_id=request_id, session_id=session_id, text=token)
                        continue
                    if kind == "progress":
                        progress = self.build_progress_event(
                            str(event.get("event_name") or ""),
                            dict(event.get("payload") or {}),
                            user_text=message,
                            request_id=request_id,
                        )
                        if progress is not None:
                            yield ProgressStreamEvent(
                                request_id=request_id,
                                session_id=session_id,
                                stage=progress.stage,
                                text=progress.text,
                            )
                        continue
                    if kind == "error":
                        raise RuntimeError(str(event.get("message") or "Streaming direct executor failed."))
                    if kind == "result":
                        execution_payload = dict(event.get("payload") or {})
                if delegate_to_bundle_runtime:
                    logger.info(
                        "runtime_v2_orchestration_direct_streaming_executor_delegated_to_bundle_runtime request_id=%s capability=%s reason=%s",
                        request_id,
                        capability_name or "bundle_fallback",
                        delegate_reason,
                    )
                    if active_streaming_executor is not None:
                        for event in active_streaming_executor(
                            message=message,
                            session_id=session_id,
                            request_id=request_id,
                            attachments=attachments,
                            request_origin=request_origin,
                            route_hints=effective_route_hints,
                            cancel_event=cancel_event,
                        ):
                            kind = str(event.get("kind") or "").strip().lower()
                            if kind == "text_delta":
                                token = str(event.get("text") or "")
                                if token:
                                    emitted_text_delta = True
                                    yield TextDeltaEvent(request_id=request_id, session_id=session_id, text=token)
                                continue
                            if kind == "progress":
                                progress = self.build_progress_event(
                                    str(event.get("event_name") or ""),
                                    dict(event.get("payload") or {}),
                                    user_text=message,
                                    request_id=request_id,
                                )
                                if progress is not None:
                                    yield ProgressStreamEvent(
                                        request_id=request_id,
                                        session_id=session_id,
                                        stage=progress.stage,
                                        text=progress.text,
                                    )
                                continue
                            if kind == "error":
                                raise RuntimeError(str(event.get("message") or "Streaming bundle executor failed."))
                            if kind == "result":
                                execution_payload = dict(event.get("payload") or {})
                                execution_payload["_runtime_system_action_type"] = str(
                                    delegated_runtime_meta.get("system_action_type") or "desktop_automation_compatibility"
                                )
                                execution_payload["_runtime_system_action_name"] = str(
                                    delegated_runtime_meta.get("system_action_name") or "desktop_automation_compatibility_action"
                                )
                                execution_payload["_runtime_executor_path"] = str(
                                    delegated_runtime_meta.get("executor_path") or "desktop_automation_compatibility_executor"
                                )
                                execution_payload["_desktop_automation_compatibility"] = bool(
                                    delegated_runtime_meta.get("desktop_automation_compatibility")
                                )
                    else:
                        execution_payload = {
                            "assistant_text": "This system action still requires the bundle executor, but no compatible streaming bundle executor is available.",
                            "summary": "This system action still requires the bundle executor, but no compatible streaming bundle executor is available.",
                            "sources": [],
                            "warnings": ["missing_streaming_bundle_executor"],
                            "structured": {},
                            "skill_name": capability_name or "system_action",
                            "success": False,
                            "errors": [{"code": "missing_streaming_bundle_executor", "message": "No streaming bundle executor was available for the delegated system action."}],
                            "_runtime_system_action_type": str(
                                delegated_runtime_meta.get("system_action_type") or "desktop_automation_compatibility"
                            ),
                            "_runtime_system_action_name": str(
                                delegated_runtime_meta.get("system_action_name") or "desktop_automation_compatibility_action"
                            ),
                            "_runtime_executor_path": str(
                                delegated_runtime_meta.get("executor_path") or "desktop_automation_compatibility_executor"
                            ),
                            "_desktop_automation_compatibility": bool(
                                delegated_runtime_meta.get("desktop_automation_compatibility")
                            ),
                        }
            elif capability_name and self._can_use_invocation_service(capability_name):
                for event in self._stream_invocation_service(
                    capability_name=capability_name,
                    message=message,
                    request_id=request_id,
                    session_id=session_id,
                    cancel_event=cancel_event,
                ):
                    kind = str(event.get("kind") or "").strip().lower()
                    if kind == "progress":
                        progress = self.build_progress_event(
                            str(event.get("event_name") or ""),
                            dict(event.get("payload") or {}),
                            user_text=message,
                            request_id=request_id,
                        )
                        if progress is not None:
                            yield ProgressStreamEvent(
                                request_id=request_id,
                                session_id=session_id,
                                stage=progress.stage,
                                text=progress.text,
                            )
                        continue
                    if kind == "error":
                        raise RuntimeError(str(event.get("message") or "Streaming execution failed."))
                    if kind == "result":
                        execution_payload = dict(event.get("payload") or {})
            elif active_streaming_executor is not None:
                for event in active_streaming_executor(
                    message=message,
                    session_id=session_id,
                    request_id=request_id,
                    attachments=attachments,
                    request_origin=request_origin,
                    route_hints={},
                    cancel_event=cancel_event,
                ):
                    kind = str(event.get("kind") or "").strip().lower()
                    if kind == "text_delta":
                        token = str(event.get("text") or "")
                        if token:
                            emitted_text_delta = True
                            yield TextDeltaEvent(request_id=request_id, session_id=session_id, text=token)
                        continue
                    if kind == "progress":
                        progress = self.build_progress_event(
                            str(event.get("event_name") or ""),
                            dict(event.get("payload") or {}),
                            user_text=message,
                            request_id=request_id,
                        )
                        if progress is not None:
                            yield ProgressStreamEvent(
                                request_id=request_id,
                                session_id=session_id,
                                stage=progress.stage,
                                text=progress.text,
                            )
                        continue
                    if kind == "error":
                        raise RuntimeError(str(event.get("message") or "Streaming bundle executor failed."))
                    if kind == "result":
                        execution_payload = dict(event.get("payload") or {})
            else:
                yield ProgressStreamEvent(
                    request_id=request_id,
                    session_id=session_id,
                    stage="executing",
                    text=f"Running {capability_name or 'runtime'} step...",
                )
                execution_payload = self._invoke_capability(
                    capability_name=capability_name,
                    message=message,
                    request_id=request_id,
                    session_id=session_id,
                    bundle_executor=bundle_executor,
                    attachments=attachments,
                    request_origin=request_origin,
                )

            if cancel_event is not None and cancel_event.is_set():
                return None
            if execution_payload is None:
                execution_payload = {
                    "assistant_text": "",
                    "summary": "",
                    "sources": [],
                    "warnings": [],
                    "structured": {},
                    "skill_name": capability_name or "runtime_stream_step",
                    "success": False,
                    "errors": [{"code": "empty_stream_result", "message": "Streaming step returned no final payload."}],
                }
            normalized = self.normalize_result(
                user_text=message,
                payload=execution_payload,
                assistant_text=str(execution_payload.get("assistant_text") or execution_payload.get("text") or "").strip(),
                assistant_html=str(execution_payload.get("assistant_html") or "").strip(),
                request_plan=request_plan,
                session_id=session_id,
                request_id=request_id,
            )
            if not emitted_text_delta:
                for chunk in self._chunk_text_for_streaming(normalized.text_reply):
                    if cancel_event is not None and cancel_event.is_set():
                        return None
                    yield TextDeltaEvent(request_id=request_id, session_id=session_id, text=chunk)
            for card in normalized.cards:
                if cancel_event is not None and cancel_event.is_set():
                    return None
                yield CardStreamEvent(request_id=request_id, session_id=session_id, card=card)
            return normalized
        except RuntimeError as exc:
            if str(exc).strip().lower() == "cancelled":
                return None
            fallback_payload = {
                "assistant_text": "",
                "summary": "",
                "sources": [],
                "warnings": ["step_stream_runtime_error"],
                "structured": {},
                "skill_name": capability_name or "runtime_stream_step",
                "success": False,
                "errors": [{"code": "runtime_error", "message": str(exc) or "Streaming step failed."}],
            }
            normalized = self.normalize_result(
                user_text=message,
                payload=fallback_payload,
                assistant_text="",
                assistant_html="",
                request_plan=request_plan,
                session_id=session_id,
                request_id=request_id,
            )
            return normalized

    def _invoke_capability(
        self,
        *,
        capability_name: str,
        message: str,
        request_id: str,
        session_id: str,
        bundle_executor: BundleExecutor | None,
        attachments: list[str],
        request_origin: str,
        route_hints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        executor = bundle_executor
        delegated_runtime_meta: dict[str, Any] = {}
        effective_route_hints = dict(route_hints or self.get_route_hints(request_id))
        forced_bundle = str(effective_route_hints.get("forced_bundle") or "").strip()
        execution_label = forced_bundle or capability_name or "bundle-runtime"
        self._set_runtime_state("analyzing", reason=f"capability_started:{execution_label}", request_id=request_id, session_id=session_id)
        if forced_bundle and executor is not None:
            logger.info(
                "runtime_v2_forced_bundle_runtime request_id=%s bundle=%s capability=%s",
                request_id,
                forced_bundle,
                capability_name or "",
            )
            self.record_tool_selection(request_id, forced_bundle, "runtime_facade_forced_bundle_executor")
            result = executor(
                message=message,
                session_id=session_id,
                request_id=request_id,
                attachments=attachments,
                request_origin=request_origin,
                route_hints=effective_route_hints,
            )
            normalized_forced_bundle = self._normalize_execution_payload(
                result,
                skill_name=str(result.get("skill_name") or forced_bundle),
                request_id=request_id,
                session_id=session_id,
            )
            normalized_forced_bundle["_runtime_executor_path"] = "bundle_runtime_executor"
            normalized_forced_bundle["_bundle_fallback"] = False
            normalized_forced_bundle["_runtime_forced_bundle"] = forced_bundle
            return normalized_forced_bundle
        direct_executor = self._capability_executors.get(capability_name or "")
        if capability_name and direct_executor is not None:
            self.record_tool_selection(request_id, capability_name, "runtime_facade_direct_capability_executor")
            result = direct_executor(
                message=message,
                session_id=session_id,
                request_id=request_id,
                attachments=attachments,
                request_origin=request_origin,
                route_hints=effective_route_hints,
            )
            if isinstance(result, dict) and result.get("_force_bundle_fallback"):
                bundle_reason = str(result.get("_bundle_reason") or "direct_executor_requested_bundle_fallback").strip()
                delegated_runtime_meta = {
                    "executor_path": str(
                        result.get("_runtime_executor_path") or "desktop_automation_compatibility_executor"
                    ),
                    "system_action_type": str(result.get("_runtime_system_action_type") or ""),
                    "system_action_name": str(result.get("_runtime_system_action_name") or ""),
                    "desktop_automation_compatibility": bool(result.get("_desktop_automation_compatibility")),
                }
                logger.info(
                    "runtime_v2_direct_executor_delegated_to_bundle_runtime request_id=%s capability=%s reason=%s",
                    request_id,
                    capability_name or "bundle_fallback",
                    bundle_reason,
                )
            else:
                normalized_direct = self._normalize_execution_payload(
                    result,
                    skill_name=str(result.get("skill_name") or capability_name),
                    request_id=request_id,
                    session_id=session_id,
                )
                normalized_direct["_runtime_executor_path"] = str(result.get("_runtime_executor_path") or "direct_capability_executor")
                normalized_direct["_bundle_fallback"] = False
                if isinstance(result, dict):
                    normalized_direct["_runtime_system_action_type"] = str(result.get("_runtime_system_action_type") or "")
                    normalized_direct["_runtime_system_action_name"] = str(result.get("_runtime_system_action_name") or "")
                    normalized_direct["_runtime_desktop_bridge_result"] = dict(result.get("_runtime_desktop_bridge_result") or {})
                return normalized_direct
        if capability_name and self._can_use_invocation_service(capability_name):
            self.record_tool_selection(request_id, capability_name, "runtime_facade_invocation_service")
            result = self.invocation_service.invoke(
                capability_name,
                message,
                request_id=request_id,
                session_id=session_id,
                skip_state_resolution=True,
            )
            normalized_agent = self._normalize_execution_payload(
                result,
                skill_name=capability_name,
                request_id=request_id,
                session_id=session_id,
            )
            normalized_agent["_runtime_executor_path"] = "invocation_service"
            normalized_agent["_bundle_fallback"] = False
            return normalized_agent
        if executor is None:
            self.record_tool_selection(request_id, "bundle_runtime", "runtime_facade_missing_bundle_executor")
            return {
                "assistant_text": "当前没有可用的执行器。",
                "summary": "当前没有可用的执行器。",
                "sources": [],
                "warnings": ["missing_bundle_executor"],
                "structured": {},
                "skill_name": capability_name or "bundle-runtime",
                "success": False,
                "changed_files": [],
                "commands_run": [],
                "validations": [],
                "errors": [{"code": "missing_bundle_executor", "message": "No compatible bundle executor was provided."}],
                "request_id": request_id,
                "session_id": session_id,
                "request_origin": request_origin,
                "_runtime_executor_path": "missing_bundle_executor",
                "_bundle_fallback": False,
            }
        logger.info(
            "runtime_v2_bundle_runtime request_id=%s capability=%s reason=runtime_facade_bundle_executor",
            request_id,
            capability_name or "bundle_runtime",
        )
        self.record_tool_selection(request_id, capability_name or "bundle_runtime", "runtime_facade_bundle_executor")
        result = executor(
            message=message,
            session_id=session_id,
            request_id=request_id,
            attachments=attachments,
            request_origin=request_origin,
            route_hints=effective_route_hints,
        )
        normalized_bundle = self._normalize_execution_payload(
            result,
            skill_name=str(result.get("skill_name") or capability_name or "bundle-runtime"),
            request_id=request_id,
            session_id=session_id,
        )
        normalized_bundle["_runtime_executor_path"] = str(delegated_runtime_meta.get("executor_path") or "bundle_runtime_executor")
        normalized_bundle["_bundle_fallback"] = False
        if "result" in locals() and isinstance(result, dict):
            normalized_bundle["_runtime_system_action_type"] = str(
                delegated_runtime_meta.get("system_action_type") or result.get("_runtime_system_action_type") or ""
            )
            normalized_bundle["_runtime_system_action_name"] = str(
                delegated_runtime_meta.get("system_action_name") or result.get("_runtime_system_action_name") or ""
            )
            normalized_bundle["_desktop_automation_compatibility"] = bool(
                delegated_runtime_meta.get("desktop_automation_compatibility")
                or result.get("_desktop_automation_compatibility")
            )
        return normalized_bundle

    def _normalize_execution_payload(
        self,
        payload: dict[str, Any] | Any,
        *,
        skill_name: str,
        request_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {
                "assistant_text": str(payload or "").strip(),
                "summary": str(payload or "").strip(),
                "sources": [],
                "warnings": [],
                "structured": {},
                "skill_name": skill_name,
                "success": bool(payload),
                "changed_files": [],
                "commands_run": [],
                "validations": [],
                "request_id": request_id,
                "session_id": session_id,
            }

        if "assistant_text" in payload or "structured" in payload:
            normalized = dict(payload)
            normalized.setdefault("request_id", request_id)
            normalized.setdefault("session_id", session_id)
            normalized.setdefault("skill_name", skill_name)
            normalized.setdefault("sources", [])
            normalized.setdefault("warnings", [])
            normalized.setdefault("changed_files", [])
            normalized.setdefault("commands_run", [])
            normalized.setdefault("validations", [])
            normalized.setdefault("structured", {})
            return normalized

        card_payload = payload.get("card")
        structured = self._structured_from_agent_card(card_payload)
        citations = list(payload.get("citations") or [])
        sources = [
            {
                "title": str(item.get("title") or item.get("domain") or "").strip(),
                "url": str(item.get("url") or "").strip(),
            }
            for item in citations
            if isinstance(item, dict) and str(item.get("url") or "").strip()
        ]
        if not sources:
            sources = [
                {"title": str(url).strip(), "url": str(url).strip()}
                for url in list(payload.get("source_urls") or [])
                if str(url).strip()
            ]
        error_message = str(payload.get("reason") or payload.get("error_message") or "").strip()
        return {
            "assistant_text": str(payload.get("answer") or payload.get("speech_text") or "").strip(),
            "summary": str(payload.get("answer") or payload.get("speech_text") or "").strip(),
            "sources": sources,
            "warnings": [],
            "structured": structured,
            "skill_name": skill_name,
            "success": bool(payload.get("success", False)),
            "changed_files": [],
            "commands_run": [],
            "validations": [],
            "request_id": request_id,
            "session_id": session_id,
            "errors": ([{"code": "agent_error", "message": error_message}] if error_message else []),
        }

    def _structured_from_agent_card(self, card_payload: Any) -> dict[str, Any]:
        if not isinstance(card_payload, dict):
            return {}
        card_type = str(card_payload.get("type") or "").strip().lower()
        data = dict(card_payload.get("data") or {})
        source = data or dict(card_payload)
        if card_type in {"weather", "weather_card"}:
            return {
                "card_type": "weather",
                "weather": source,
                **source,
            }
        if card_type in {"time", "time_card"}:
            return {
                "card_type": "time",
                "time": source,
                **source,
            }
        if card_type in {"location", "location_map_card", "map_card"}:
            return {
                "card_type": "location",
                "location": source,
                **source,
            }
        if card_type in {"news_list", "news_card"}:
            return {
                "card_type": "news_list",
                "news_items": list(source.get("items") or []),
                **source,
            }
        if card_type:
            return {
                "card_type": card_type,
                "card": source,
                **source,
            }
        return source

    def _capability_for_intent(self, intent: str) -> str:
        normalized = str(intent or "").strip().lower()
        if normalized in {"weather", "weather_lookup", "time", "time_lookup"}:
            return "realtime_lookup"
        if normalized in {"news", "news_lookup"}:
            return "web_research"
        if normalized in {"location", "location_lookup", "display_information"}:
            return "location_lookup"
        if normalized == "explanation":
            return "explanation"
        if normalized == "generic_search":
            return "generic_search"
        if normalized == "system_action":
            return "system_action"
        return ""

    def _can_use_invocation_service(self, capability_name: str) -> bool:
        return False

    def _chunk_text_for_streaming(self, text: str, *, max_chunk_chars: int = 28) -> list[str]:
        content = str(text or "")
        if not content:
            return []
        chunks: list[str] = []
        buffer = ""
        for char in content:
            buffer += char
            if len(buffer) >= max_chunk_chars or char in {"\n", "。", "！", "？", ".", "!", "?"}:
                chunks.append(buffer)
                buffer = ""
        if buffer:
            chunks.append(buffer)
        return chunks

    def _update_session_execution_context(
        self,
        session_id: str,
        plan: RuntimeExecutionPlan,
        *,
        state: RuntimeExecutionPlanState | None = None,
    ) -> None:
        context = self.context_manager.get_or_create(session_id)
        effective_steps = list(state.steps) if state is not None else list(plan.steps)
        context.last_execution_plan = [
            {
                "step_id": step.step_id,
                "step_index": step.step_index,
                "capability": step.capability,
                "slots": dict(step.slots),
                "normalized_query": step.normalized_query,
                "retryable": step.retryable,
                "depends_on": list(step.depends_on),
                "can_run_parallel": step.can_run_parallel,
                "branch_key": step.branch_key,
                "dependency_mode": step.dependency_mode,
                "execution_confidence_score": step.execution_confidence_score,
                "inserted_by": step.inserted_by,
                "rewrite_source": step.rewrite_source,
            }
            for step in effective_steps
        ]
        if state is not None:
            context.active_execution_plan_state = state.to_dict()
            if state.results:
                context.last_step_capability = state.results[-1].capability
            elif effective_steps:
                context.last_step_capability = effective_steps[-1].capability
        elif plan.steps:
            context.last_step_capability = plan.steps[-1].capability

    def get_trace_bundle(self, request_id: str) -> dict[str, Any]:
        request = self._request_contexts.get(request_id)
        return {
            "response_trace": asdict(request.response_trace) if request is not None else {},
            "tool_trace": asdict(request.tool_trace) if request is not None else {},
            "schema_trace": asdict(self._schema_traces.get(request_id)) if request_id in self._schema_traces else {},
            "renderer_trace": asdict(self._renderer_traces.get(request_id)) if request_id in self._renderer_traces else {},
            "speech_trace": asdict(self._speech_traces.get(request_id)) if request_id in self._speech_traces else {},
            "state_trace": [asdict(item) for item in self._state_traces.get(request_id, [])],
        }

    def record_tool_selection(self, request_id: str, selected_tool: str, reason: str = "") -> None:
        if not request_id:
            return
        context = self._request_contexts.get(request_id)
        if context is None:
            return
        context.tool_trace.selected_tool = str(selected_tool or "")
        context.tool_trace.selection_reason = str(reason or "")

    def _build_perception_frame(
        self,
        user_text: str,
        *,
        previous_structured: dict[str, Any] | None = None,
        session_id: str = "",
    ) -> PerceptionFrame:
        explicit_intent = "desktop_automation" if is_explicit_desktop_automation_command(user_text) else ""
        effective_user_text = strip_explicit_desktop_automation_prefix(user_text) if explicit_intent == "desktop_automation" else user_text
        normalized_text = self.input_normalizer.normalize(effective_user_text)
        base_intent, base_confidence = self.intent_classifier.classify(normalized_text)
        entities = self.entity_extractor.extract(normalized_text)
        session_context = self.context_manager.get_or_create(session_id or "global")
        followup = self.context_manager.resolve_followup(
            session_id or "global",
            normalized_text,
            previous_structured=previous_structured,
        )
        if followup.focus_value and not any(entity.kind == "location" for entity in entities):
            entities.append(
                DetectedEntity(
                    kind="location",
                    value=followup.focus_value,
                    confidence=0.84,
                    source_text=followup.focus_value,
                    metadata={"source": "followup_context"},
                )
            )
        stable_intent = self._resolution_intent_hint(
            normalized_text,
            base_intent=base_intent,
            followup_target=followup.target,
            explicit_intent=explicit_intent,
        )
        resolution_result = self.query_resolver.resolve(
            raw_text=effective_user_text,
            normalized_text=normalized_text,
            intent_hint=stable_intent,
            entities=entities,
            session_context=session_context,
            previous_structured=previous_structured,
            followup_target=followup.target,
            followup_focus_value=followup.focus_value,
        )
        resolved_capability = str(resolution_result.capability or stable_intent).strip() or stable_intent
        for slot_name, slot_value in resolution_result.resolved_slots.items():
            if slot_name not in {"location", "date", "topic"}:
                continue
            replaced = False
            for entity in entities:
                if entity.kind != slot_name:
                    continue
                source_name = str(entity.metadata.get("source") or "")
                if slot_name == "location" and (
                    source_name in {"followup_context", "resolution"}
                    or entity.value in {"今天", "显示地图", "查", "地图"}
                ):
                    entity.value = slot_value
                    entity.source_text = slot_value
                    entity.metadata["source"] = resolution_result.slot_sources.get(slot_name, "resolution")
                    replaced = True
                    break
            if replaced or any(entity.kind == slot_name for entity in entities):
                continue
            entities.append(
                DetectedEntity(
                    kind=slot_name,  # type: ignore[arg-type]
                    value=slot_value,
                    confidence=0.82,
                    source_text=slot_value,
                    metadata={"source": resolution_result.slot_sources.get(slot_name, "resolution")},
                )
            )
        active_focus = {entity.kind: entity.value for entity in entities if entity.value}
        if followup.target == "location":
            source = previous_structured if isinstance(previous_structured, dict) else {}
            nested = source.get("location") if isinstance(source.get("location"), dict) else {}
            focus_source = nested or source
            focus_location = str(focus_source.get("title") or focus_source.get("address") or "").strip()
            if focus_location:
                active_focus["location"] = focus_location
        if followup.focus_value and followup.target in {"location", "weather"}:
            active_focus["location"] = followup.focus_value
        preliminary = PerceptionFrame(
            raw_text=user_text,
            normalized_text=normalized_text,
            intent=resolved_capability,  # type: ignore[arg-type]
            entities=entities,
            confidence=base_confidence,
            preferred_modalities=(),
            followup_target=followup.target,
            metadata={
                "followup_reused": followup.reused,
                "followup_reason": followup.reason,
                "active_focus": active_focus,
                "explicit_intent": explicit_intent,
                "resolution": resolution_result.to_meta(),
            },
        )
        modalities = self.modality_detector.detect(preliminary)
        return PerceptionFrame(
            raw_text=user_text,
            normalized_text=normalized_text,
            intent=resolved_capability,  # type: ignore[arg-type]
            entities=entities,
            confidence=base_confidence,
            preferred_modalities=modalities,
            followup_target=followup.target,
            metadata={
                "followup_reused": followup.reused,
                "followup_reason": followup.reason,
                "active_focus": active_focus,
                "explicit_intent": explicit_intent,
                "resolution": resolution_result.to_meta(),
            },
        )

    @staticmethod
    def _should_bypass_resolution_clarification(
        *,
        runtime_context: RuntimeRequestContext | None,
        request_plan: ResponseRequestPlan,
        resolution: dict[str, Any],
    ) -> bool:
        explicit_intent = str((runtime_context.perception.metadata.get("explicit_intent") or "") if runtime_context else "").strip().lower()
        capability = str(resolution.get("capability") or request_plan.intent or "").strip().lower()
        if explicit_intent == "desktop_automation" and capability == "system_action":
            return True
        if capability not in {"location_lookup", "display_information"} or runtime_context is None:
            return False
        location_slots = dict(
            resolution.get("validated_slots")
            or resolution.get("normalized_slots")
            or resolution.get("slots")
            or {}
        )
        has_location_slot = bool(str(location_slots.get("location") or "").strip())
        has_location_entity = any(
            getattr(entity, "kind", "") == "location" and str(getattr(entity, "value", "") or "").strip()
            for entity in runtime_context.perception.entities
        )
        raw_text = str(runtime_context.perception.raw_text or "").strip()
        normalized_text = str(runtime_context.perception.normalized_text or "").strip()
        return (
            has_location_slot
            or has_location_entity
            or FairyRuntimeV2._looks_like_explicit_location_map_request(raw_text)
            or FairyRuntimeV2._looks_like_explicit_location_map_request(normalized_text)
        )

    @staticmethod
    def _looks_like_explicit_location_map_request(text: str) -> bool:
        cleaned = str(text or "").strip()
        if not cleaned:
            return False
        lowered = cleaned.lower()
        if "地图" in cleaned:
            stripped = cleaned.replace("地图", "")
            for token in ("显示", "打开", "看看", "查看", "看一下", "给我看看", "给我看"):
                stripped = stripped.replace(token, "")
            if stripped.strip(" ，,。.!！？?"):
                return True
        if "在哪里" in cleaned or "在哪儿" in cleaned or "在哪" in cleaned:
            stripped = cleaned.replace("在哪里", "").replace("在哪儿", "").replace("在哪", "")
            if stripped.strip(" ，,。.!！？?"):
                return True
        if "位置" in cleaned and any(token in cleaned for token in ("看看", "查看", "看一下")):
            stripped = cleaned.replace("位置", "").replace("看看", "").replace("查看", "").replace("看一下", "")
            if stripped.strip(" ，,。.!！？?"):
                return True
        for prefix in ("show map for", "display map for", "open map for", "where is"):
            if lowered.startswith(prefix):
                remainder = lowered[len(prefix) :].strip(" ,.!?")
                if remainder:
                    return True
        return False

    def _build_clarification_response(
        self,
        *,
        request_id: str,
        session_id: str,
        request_plan: ResponseRequestPlan,
        resolution: dict[str, Any],
        request_origin: str,
    ) -> NormalizedAssistantResponse:
        clarification_text = str(resolution.get("clarification_message") or "我需要更多信息才能继续。").strip()
        clarification_title = str(resolution.get("clarification_title") or "需要更多信息").strip()
        return NormalizedAssistantResponse(
            intent=request_plan.intent,
            modality="text_plus_card",
            text_reply=clarification_text,
            request_id=request_id,
            session_id=session_id,
            speech_payload=SpeechPayload(mode="summary_first", text=clarification_text, allow_streaming=False),
            card_payloads=[
                CardPayload(
                    type="generic_info",
                    version="1",
                    data={
                        "title": clarification_title,
                        "summary": clarification_text,
                        "fields": [
                            {"label": "intent", "value": str(resolution.get("intent_guess") or request_plan.intent)},
                        ],
                    },
                    layout="single",
                    metadata={"clarification": True},
                )
            ],
            meta={
                "intent": request_plan.intent,
                "modality": "text_plus_card",
                "planner_confidence": float(getattr(request_plan, "planner_confidence", 0.0) or 0.0),
                "resolution": dict(resolution),
                "runtime": {"clarification_response": True, "request_origin": request_origin},
            },
            errors=[],
        )

    def _build_clarification_contract(
        self,
        *,
        request_id: str,
        session_id: str,
        request_plan: ResponseRequestPlan,
        resolution: dict[str, Any],
        include_compat_fields: bool,
        request_origin: str,
    ) -> dict[str, Any]:
        clarification = self._build_clarification_response(
            request_id=request_id,
            session_id=session_id,
            request_plan=request_plan,
            resolution=resolution,
            request_origin=request_origin,
        )
        contract = clarification.to_contract_dict()
        meta = dict(contract.get("meta") or {})
        runtime_meta = dict(meta.get("runtime") or {})
        runtime_meta.update(
            {
                "selected_capability": self._capability_for_intent(request_plan.intent) or "clarification",
                "executor_path": "query_resolution_clarification",
                "used_bundle_fallback": False,
                "request_origin": request_origin,
                "compat_fields_emitted": bool(include_compat_fields),
                "system_action_type": "",
                "system_action_name": "",
                "desktop_bridge_result": {},
            }
        )
        meta["runtime"] = runtime_meta
        meta["runtime_state"] = self.runtime_state_snapshot()
        meta["resolution"] = dict(resolution)
        meta["fairy"] = self._response_fairy_meta(
            meta=meta,
            text=clarification.text_reply,
            cards=clarification.cards,
            errors=list(clarification.errors),
            resolution=resolution,
            selected_capability=self._capability_for_intent(request_plan.intent) or "clarification",
        )
        contract["meta"] = meta
        if include_compat_fields:
            contract.update(
                {
                    "assistant_text": clarification.text_reply,
                    "assistant_html": "",
                    "summary": clarification.text_reply,
                    "sources": [],
                    "warnings": [],
                    "structured": {},
                    "skill_name": request_plan.intent,
                    "success": True,
                    "changed_files": [],
                    "commands_run": [],
                    "validations": [],
                    "cancelled": False,
                    "request_origin": request_origin,
                }
            )
        return contract

    def _structured_intent(
        self,
        normalized_text: str,
        *,
        base_intent: str,
        followup_target: str = "",
        explicit_intent: str = "",
    ) -> str:
        lowered = normalized_text.lower()
        if explicit_intent == "desktop_automation":
            return "system_action"
        if followup_target in {"weather", "location"} and any(
            token in lowered for token in ("\u51e0\u70b9", "\u65f6\u95f4", "\u5f53\u5730\u65f6\u95f4", "current time", "what time", "local time", "time")
        ):
            return "time_lookup"
        if followup_target == "weather":
            return "weather_lookup"
        if any(token in lowered for token in ("\u51e0\u70b9", "\u65f6\u95f4", "\u5f53\u5730\u65f6\u95f4", "current time", "what time", "local time", "time")):
            return "time_lookup"
        if any(token in lowered for token in ("\u5929\u6c14", "weather", "forecast", "\u6e29\u5ea6", "\u6c14\u6e29")):
            return "weather_lookup"
        if followup_target == "location" and any(
            token in lowered for token in ("\u5730\u56fe", "show map", "display map", "\u7ed9\u6211\u770b\u770b\u5730\u56fe", "\u663e\u793a\u5730\u56fe", "\u518d\u7ed9\u6211\u770b\u770b\u5730\u56fe")
        ):
            return "display_information"
        if followup_target == "location":
            return "location_lookup"
        if any(token in lowered for token in ("where is", "\u5730\u56fe", "\u5730\u5740", "\u4f4d\u7f6e", "\u9644\u8fd1", "nearest", "nearby", "\u5728\u54ea", "\u5728\u54ea\u91cc")):
            return "location_lookup"
        if any(token in lowered for token in ("\u65b0\u95fb", "news", "latest", "\u5feb\u8baf", "\u79d1\u6280\u65b0\u95fb")):
            return "news_lookup"
        if any(
            token in lowered
            for token in (
                "\u4ec0\u4e48\u662f",
                "\u662f\u4ec0\u4e48",
                "\u4ec0\u4e48\u610f\u601d",
                "\u662f\u4ec0\u4e48\u610f\u601d",
                "\u89e3\u91ca\u4e00\u4e0b",
                "\u600e\u4e48\u56de\u4e8b",
                "\u4e3a\u4ec0\u4e48\u4f1a\u8fd9\u6837",
                "what is",
                "explain",
            )
        ):
            return "explanation"
        if any(
            token in lowered
            for token in (
                "refresh capabilities",
                "reload capabilities",
                "clear cache",
                "clear asset cache",
                "restart backend",
                "restart service",
                "open system panel",
                "open debug panel",
                "focus window",
                "show main window",
                "show notification",
                "reveal asset folder",
                "\u5237\u65b0\u80fd\u529b",
                "\u5237\u65b0\u529f\u80fd\u5217\u8868",
                "\u91cd\u65b0\u52a0\u8f7d\u80fd\u529b",
                "\u6e05\u7406\u7f13\u5b58",
                "\u6e05\u9664\u7f13\u5b58",
                "\u6e05\u7406\u8d44\u6e90\u7f13\u5b58",
                "\u6e05\u9664\u8d44\u6e90\u7f13\u5b58",
                "\u91cd\u542f\u540e\u7aef",
                "\u91cd\u542f\u670d\u52a1",
                "\u6253\u5f00\u7cfb\u7edf\u9762\u677f",
                "\u6253\u5f00\u8c03\u8bd5\u9762\u677f",
                "\u805a\u7126\u7a97\u53e3",
                "\u5207\u5230\u4e3b\u7a97\u53e3",
                "\u663e\u793a\u901a\u77e5",
                "\u5f39\u51fa\u901a\u77e5",
                "\u6253\u5f00\u7f13\u5b58\u76ee\u5f55",
                "\u6253\u5f00\u8d44\u6e90\u76ee\u5f55",
            )
        ):
            return "system_action"
        if base_intent == "system_action":
            return "system_action"
        if base_intent == "explanation":
            return "explanation"
        if any(
            token in lowered
            for token in (
                "search",
                "find",
                "look up",
                "查",
                "搜",
                "帮我查",
                "帮我找",
                "看看",
                "有没有",
                "哪个好",
                "给我查一下",
            )
        ):
            return "generic_search"
        return "generic_search"

    def _preferred_routes_for(self, perception_intent: str) -> list[str]:
        if perception_intent == "weather_lookup":
            return ["weather", "web_search"]
        if perception_intent == "time_lookup":
            return ["realtime_lookup"]
        if perception_intent in {"location_lookup", "display_information"}:
            return ["web_search"]
        if perception_intent == "news_lookup":
            return ["news", "web_search"]
        if perception_intent == "system_action":
            return ["system_bridge", "agent_shell"]
        return ["direct_answer"]

    def _resolution_intent_hint(
        self,
        normalized_text: str,
        *,
        base_intent: str,
        followup_target: str = "",
        explicit_intent: str = "",
    ) -> str:
        _ = normalized_text
        if explicit_intent == "desktop_automation":
            return "system_action"
        if followup_target == "weather":
            return "weather_lookup"
        if followup_target == "location":
            return "location_lookup"
        if base_intent == "system_action":
            return "system_action"
        if base_intent == "explanation":
            return "explanation"
        return "generic_search"

    def _preferred_route_hints_for(self, perception_intent: str) -> list[str]:
        if perception_intent == "weather_lookup":
            return ["weather", "web_search"]
        if perception_intent == "time_lookup":
            return ["realtime_lookup"]
        if perception_intent in {"location_lookup", "display_information"}:
            return ["web_search"]
        if perception_intent == "news_lookup":
            return ["news", "web_search"]
        if perception_intent == "system_action":
            return ["system_bridge", "agent_shell"]
        return []

    def _validate_cards(self, normalized: NormalizedAssistantResponse, *, request_id: str = "") -> NormalizedAssistantResponse:
        validated_cards = []
        issues: list[str] = []
        renderer_names: list[str] = []
        for card in normalized.card_payloads:
            validation = self.schema_validator.validate_card(card)
            repaired = validation.repaired_card
            if validation.issues:
                repaired.metadata.setdefault("schema_issues", list(validation.issues))
                if card.type in {"weather", "location", "news_list"}:
                    repaired.metadata.setdefault("degraded_specific_renderer", True)
            fallback_reason = str(repaired.metadata.get("fallback_reason", "") or "")
            if fallback_reason:
                issues.append(fallback_reason)
            validated_cards.append(repaired)
            issues.extend(validation.issues)
            renderer_names.append(self._renderer_name_for(repaired.type))
        normalized.card_payloads = validated_cards
        if request_id:
            fallback_text = ";".join(issue for issue in issues if issue)
            self._schema_traces[request_id] = SchemaTrace(
                request_id=request_id,
                card_types=[card.type for card in validated_cards],
                fallback_reason=fallback_text,
            )
            self._renderer_traces[request_id] = RendererTrace(
                request_id=request_id,
                renderers=renderer_names,
                layout=",".join(card.layout for card in validated_cards),
                fallback_reason=fallback_text,
            )
        return normalized

    def _renderer_name_for(self, card_type: str) -> str:
        mapping = {
            "weather": "WeatherCardWidget",
            "time": "TimeCardWidget",
            "location": "MapPreviewWidget",
            "news_list": "NewsCarouselWidget",
            "specs": "SpecsCardWidget",
            "compare": "CompareCardWidget",
            "release": "ReleaseCardWidget",
            "web_brief": "WebBriefCardWidget",
            "generic_info": "GenericInfoCardWidget",
            "image": "ImageCardWidget",
            "link": "LinkCardWidget",
            "suggestion": "SuggestionCardWidget",
        }
        return mapping.get(card_type, "GenericInfoCardWidget")

    def _transition_for_progress(self, request_id: str, event_name: str) -> None:
        next_state = "searching"
        if event_name == "assistant_response_chunk":
            next_state = "streaming_text"
        elif event_name in {"skill_result_ready", "final_response_ready"}:
            next_state = "rendering_cards"
        self._record_state(request_id, self._current_state(request_id), next_state)

    def _record_state(self, request_id: str, from_state: str, to_state: str) -> None:
        if not request_id:
            return
        traces = self._state_traces.setdefault(request_id, [])
        traces.append(StateTrace(request_id=request_id, from_state=from_state, to_state=to_state))
        context = self._request_contexts.get(request_id)
        if context is not None:
            context.state_machine.transition(to_state)  # type: ignore[arg-type]

    def _current_state(self, request_id: str) -> str:
        context = self._request_contexts.get(request_id)
        if context is None:
            return ""
        return context.state_machine.state
