from __future__ import annotations

from typing import Any, Callable, Iterable

from app.runtime.system_actions.action_models import SystemActionResolution
from app.runtime.system_actions.desktop_automation_compat import DesktopAutomationCompatibilityExecutor
from app.runtime.system_actions.action_registry import SystemActionRegistry
from app.runtime.system_actions.tauri_bridge_client import TauriBridgeClient


RuntimeActionHandler = Callable[[str, dict[str, Any] | None], dict[str, Any]]


class SystemActionExecutor:
    def __init__(
        self,
        *,
        runtime_action_handler: RuntimeActionHandler,
        desktop_bridge_client: TauriBridgeClient | None = None,
        registry: SystemActionRegistry | None = None,
        desktop_automation_compatibility_executor: DesktopAutomationCompatibilityExecutor | None = None,
    ) -> None:
        self._runtime_action_handler = runtime_action_handler
        self._desktop_bridge_client = desktop_bridge_client or TauriBridgeClient()
        self._registry = registry or SystemActionRegistry()
        self._desktop_automation_compatibility_executor = (
            desktop_automation_compatibility_executor or DesktopAutomationCompatibilityExecutor()
        )

    def execute(
        self,
        *,
        message: str,
        session_id: str,
        request_id: str,
        request_origin: str,
        cancel_event: Any | None = None,
        explicit_intent: str = "",
        allow_desktop_automation_compatibility: bool = False,
    ) -> dict[str, Any]:
        resolution = self._registry.resolve(message)
        if resolution.category == "desktop_automation_compatibility":
            return self._desktop_automation_compatibility_executor.prepare(
                resolution=resolution,
                explicit_intent=explicit_intent,
                enabled=allow_desktop_automation_compatibility,
            )
        if resolution.category == "backend_action":
            return self._execute_backend_action(
                resolution=resolution,
                session_id=session_id,
                request_id=request_id,
                request_origin=request_origin,
            )
        if resolution.category == "desktop_action":
            return self._execute_desktop_action(
                resolution=resolution,
                session_id=session_id,
                request_id=request_id,
                request_origin=request_origin,
                cancel_event=cancel_event,
            )
        return self._unknown_action_payload(
            resolution=resolution,
            session_id=session_id,
            request_id=request_id,
            request_origin=request_origin,
        )

    def stream_execute(
        self,
        *,
        message: str,
        session_id: str,
        request_id: str,
        request_origin: str,
        cancel_event: Any | None = None,
        explicit_intent: str = "",
        allow_desktop_automation_compatibility: bool = False,
    ) -> Iterable[dict[str, Any]]:
        resolution = self._registry.resolve(message)
        yield {
            "kind": "progress",
            "event_name": "structured_tool_progress",
            "payload": {
                "phase": "planning",
                "subtype": "system_action",
                "action_name": resolution.name,
                "action_type": resolution.category,
            },
        }
        if resolution.category == "desktop_automation_compatibility":
            payload = self._desktop_automation_compatibility_executor.prepare(
                resolution=resolution,
                explicit_intent=explicit_intent,
                enabled=allow_desktop_automation_compatibility,
            )
            if payload.get("_force_bundle_fallback"):
                yield {
                    "kind": "progress",
                    "event_name": "structured_tool_progress",
                    "payload": {
                        "phase": "desktop_automation_dispatch",
                        "subtype": "system_action",
                        "action_name": resolution.name,
                        "action_type": resolution.category,
                    },
                }
                yield {
                    "kind": "delegate_bundle_runtime",
                    "system_action_type": resolution.category,
                    "system_action_name": resolution.name,
                    "reason": resolution.reason,
                    "desktop_automation_compatibility": True,
                    "executor_path": str(payload.get("_runtime_executor_path") or "desktop_automation_compatibility_executor"),
                }
                return
            summary = str(payload.get("summary") or payload.get("assistant_text") or "").strip()
            if summary:
                yield {"kind": "text_delta", "text": summary}
            yield {
                "kind": "progress",
                "event_name": "structured_tool_progress",
                "payload": {
                    "phase": "desktop_automation_result",
                    "subtype": "system_action",
                    "action_name": resolution.name,
                    "action_type": resolution.category,
                    "status": "error",
                },
            }
            yield {"kind": "result", "payload": payload}
            return
        yield {
            "kind": "progress",
            "event_name": "structured_tool_progress",
            "payload": {
                "phase": "desktop_action_dispatch" if resolution.category == "desktop_action" else "action_dispatch",
                "subtype": "system_action",
                "action_name": resolution.name,
                "action_type": resolution.category,
            },
        }
        payload = self.execute(
            message=message,
            session_id=session_id,
            request_id=request_id,
            request_origin=request_origin,
            cancel_event=cancel_event,
            explicit_intent=explicit_intent,
            allow_desktop_automation_compatibility=allow_desktop_automation_compatibility,
        )
        summary = str(payload.get("summary") or payload.get("assistant_text") or "").strip()
        if summary:
            yield {"kind": "text_delta", "text": summary}
        yield {
            "kind": "progress",
            "event_name": "structured_tool_progress",
            "payload": {
                "phase": "desktop_action_result" if resolution.category == "desktop_action" else "action_result",
                "subtype": "system_action",
                "action_name": resolution.name,
                "action_type": resolution.category,
                "status": "ok" if bool(payload.get("success", False)) else "error",
            },
        }
        yield {"kind": "result", "payload": payload}

    def supported_backend_actions(self) -> list[str]:
        return self._registry.supported_backend_actions()

    def supported_desktop_actions(self) -> list[str]:
        return self._registry.supported_desktop_actions()

    def _execute_backend_action(
        self,
        *,
        resolution: SystemActionResolution,
        session_id: str,
        request_id: str,
        request_origin: str,
    ) -> dict[str, Any]:
        result = self._runtime_action_handler(resolution.name, {"request_id": request_id, "session_id": session_id})
        ok = bool(result.get("ok", False))
        message = str(result.get("message") or resolution.summary).strip()
        detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
        summary = message or resolution.summary
        return {
            "assistant_text": summary,
            "assistant_html": "",
            "summary": summary,
            "sources": [],
            "warnings": [],
            "structured": {
                "card_type": "generic_info",
                "title": "System action",
                "summary": summary,
                "fields": [
                    {"label": "Action", "value": resolution.name},
                    {"label": "Scope", "value": "backend"},
                    {"label": "Status", "value": "ok" if ok else "failed"},
                ],
                "system_action": {
                    "name": resolution.name,
                    "type": resolution.category,
                    "ok": ok,
                    "detail": detail,
                },
            },
            "skill_name": "system_action",
            "success": ok,
            "changed_files": [],
            "commands_run": [],
            "validations": [],
            "cancelled": False,
            "request_origin": request_origin,
            "request_id": request_id,
            "session_id": session_id,
            "errors": [] if ok else [{"code": "system_action_failed", "message": summary}],
            "_runtime_system_action_type": resolution.category,
            "_runtime_system_action_name": resolution.name,
        }

    def _execute_desktop_action(
        self,
        *,
        resolution: SystemActionResolution,
        session_id: str,
        request_id: str,
        request_origin: str,
        cancel_event: Any | None = None,
    ) -> dict[str, Any]:
        bridge_result = self._desktop_bridge_client.execute_desktop_action(
            resolution.name,
            payload={
                "request_id": request_id,
                "session_id": session_id,
                **dict(resolution.payload or {}),
            },
            cancel_event=cancel_event,
        )
        status = str(bridge_result.get("status") or "error").strip().lower()
        ok = status == "ok"
        message = str(bridge_result.get("message") or resolution.summary).strip()
        detail = bridge_result.get("data") if isinstance(bridge_result.get("data"), dict) else {}
        summary = message or resolution.summary
        return {
            "assistant_text": summary,
            "assistant_html": "",
            "summary": summary,
            "sources": [],
            "warnings": [] if ok else ["desktop_action_failed"],
            "structured": {
                "card_type": "generic_info",
                "title": "Desktop action",
                "summary": summary,
                "fields": [
                    {"label": "Action", "value": resolution.name},
                    {"label": "Scope", "value": "desktop"},
                    {"label": "Status", "value": "ok" if ok else "failed"},
                ],
                "system_action": {
                    "name": resolution.name,
                    "type": resolution.category,
                    "ok": ok,
                    "detail": detail,
                },
            },
            "skill_name": "system_action",
            "success": ok,
            "changed_files": [],
            "commands_run": [],
            "validations": [],
            "cancelled": bool(detail.get("cancelled", False)),
            "request_origin": request_origin,
            "request_id": request_id,
            "session_id": session_id,
            "errors": [] if ok else [{"code": "desktop_action_failed", "message": summary}],
            "_runtime_system_action_type": resolution.category,
            "_runtime_system_action_name": resolution.name,
            "_runtime_executor_path": "desktop_bridge",
            "_runtime_desktop_bridge_result": bridge_result,
        }

    def _unknown_action_payload(
        self,
        *,
        resolution: SystemActionResolution,
        session_id: str,
        request_id: str,
        request_origin: str,
    ) -> dict[str, Any]:
        summary = "I could not match that request to a supported backend or desktop system action."
        return {
            "assistant_text": summary,
            "assistant_html": "",
            "summary": summary,
            "sources": [],
            "warnings": ["unsupported_system_action"],
            "structured": {
                "card_type": "generic_info",
                "title": "Unsupported system action",
                "summary": summary,
                "fields": [
                    {"label": "Reason", "value": resolution.reason or "unknown"},
                    {"label": "Matched action", "value": resolution.name},
                ],
                "system_action": {
                    "name": resolution.name,
                    "type": resolution.category,
                },
            },
            "skill_name": "system_action",
            "success": False,
            "changed_files": [],
            "commands_run": [],
            "validations": [],
            "cancelled": False,
            "request_origin": request_origin,
            "request_id": request_id,
            "session_id": session_id,
            "errors": [{"code": "unsupported_system_action", "message": summary}],
            "_runtime_system_action_type": resolution.category,
            "_runtime_system_action_name": resolution.name,
        }
