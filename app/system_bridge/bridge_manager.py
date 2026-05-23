from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Any, Callable

from app.system_bridge.system_actions import SystemActionName, SystemActionResult
from app.system_bridge.system_events import SystemEvent
from app.system_bridge.system_state import SystemState


logger = logging.getLogger(__name__)

ActionHandler = Callable[[dict[str, Any] | None], SystemActionResult | dict[str, Any]]


class SystemBridgeManager:
    def __init__(self, *, max_events: int = 60) -> None:
        self._lock = threading.Lock()
        self._events: deque[SystemEvent] = deque(maxlen=max_events)
        self._state = SystemState()
        self._cancel_request_id: str | None = None
        self._cancel_handler: Callable[[], bool] | None = None
        self._action_handlers: dict[str, ActionHandler] = {}

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._state.recent_events = list(self._events)
            return self._state.to_dict()

    def list_events(self, *, limit: int = 25) -> list[dict[str, Any]]:
        with self._lock:
            return [event.to_dict() for event in list(self._events)[-max(limit, 1) :]]

    def update_capabilities(self, capabilities: dict[str, Any]) -> None:
        with self._lock:
            self._state.capabilities = dict(capabilities or {})

    def update_runtime_state(
        self,
        *,
        current_state: str,
        trace: list[dict[str, Any]] | None = None,
        request_id: str | None = None,
        session_id: str | None = None,
        reason: str = "",
        fairy: dict[str, Any] | None = None,
        emit_event: bool = True,
    ) -> None:
        with self._lock:
            self._state.current_state = str(current_state or self._state.current_state or "booting")
            if fairy is not None:
                self._state.fairy = dict(fairy or {})
            if trace is not None:
                self._state.runtime_state_trace = [dict(item or {}) for item in trace]
            if emit_event:
                self._events.append(
                    SystemEvent(
                        event="runtime_state_changed",
                        request_id=request_id,
                        session_id=session_id,
                        detail={"state": self._state.current_state, "reason": reason},
                    )
                )

    def set_backend_status(
        self,
        status: str,
        *,
        message: str = "",
        request_id: str | None = None,
        session_id: str | None = None,
        emit_event: bool = True,
    ) -> None:
        with self._lock:
            self._state.backend_status = str(status or "unknown")
            if message:
                self._state.last_error = message if status == "error" else self._state.last_error
            if emit_event:
                event_name = {
                    "ready": "backend_started",
                    "stopped": "backend_stopped",
                    "error": "backend_error",
                    "starting": "backend_starting",
                }.get(status, "backend_status_changed")
                self._events.append(
                    SystemEvent(
                        event=event_name,
                        request_id=request_id,
                        session_id=session_id,
                        detail={"status": status, "message": message},
                    )
                )

    def set_last_error(self, message: str, *, request_id: str | None = None, session_id: str | None = None) -> None:
        with self._lock:
            self._state.last_error = str(message or "").strip() or None
            if message:
                self._events.append(
                    SystemEvent(
                        event="backend_error",
                        request_id=request_id,
                        session_id=session_id,
                        detail={"message": message},
                    )
                )

    def register_stream(
        self,
        *,
        request_id: str,
        session_id: str,
        cancel_handler: Callable[[], bool] | None = None,
    ) -> None:
        with self._lock:
            self._state.active_session = session_id
            self._state.active_stream_request = request_id
            self._state.is_streaming = True
            self._cancel_request_id = request_id
            self._cancel_handler = cancel_handler
            self._events.append(
                SystemEvent(
                    event="stream_started",
                    request_id=request_id,
                    session_id=session_id,
                )
            )

    def finish_stream(
        self,
        *,
        request_id: str,
        session_id: str,
        status: str = "finished",
        detail: dict[str, Any] | None = None,
    ) -> None:
        event_name = {
            "finished": "stream_finished",
            "cancelled": "stream_cancelled",
            "error": "backend_error",
        }.get(status, "stream_finished")
        with self._lock:
            if self._state.active_stream_request == request_id:
                self._state.active_stream_request = None
                self._state.is_streaming = False
            self._cancel_request_id = None
            self._cancel_handler = None
            if status == "finished":
                self._state.last_error = None
            if status == "error":
                self._state.last_error = str((detail or {}).get("message") or "").strip() or self._state.last_error
            self._events.append(
                SystemEvent(
                    event=event_name,
                    request_id=request_id,
                    session_id=session_id,
                    detail=dict(detail or {}),
                )
            )

    def register_action_handler(self, action: SystemActionName, handler: ActionHandler) -> None:
        self._action_handlers[str(action)] = handler

    def perform_action(self, action: SystemActionName, payload: dict[str, Any] | None = None) -> SystemActionResult:
        normalized_action = str(action)
        if normalized_action == "cancel_current_request":
            return self._cancel_current_request()
        handler = self._action_handlers.get(normalized_action)
        if handler is None:
            return SystemActionResult(
                action=action,
                ok=False,
                message=f"No handler registered for action: {normalized_action}",
            )
        try:
            result = handler(dict(payload or {}))
            if isinstance(result, SystemActionResult):
                return result
            if isinstance(result, dict):
                return SystemActionResult(
                    action=action,
                    ok=bool(result.get("ok", True)),
                    message=str(result.get("message") or ""),
                    detail=dict(result.get("detail") or {}),
                )
            return SystemActionResult(action=action, ok=True, message=str(result or "ok"))
        except Exception as exc:  # noqa: BLE001
            logger.exception("system_bridge_action_failed action=%s", normalized_action)
            return SystemActionResult(action=action, ok=False, message=str(exc))

    def _cancel_current_request(self) -> SystemActionResult:
        with self._lock:
            request_id = self._cancel_request_id
            cancel_handler = self._cancel_handler
            session_id = self._state.active_session
        if cancel_handler is None or not request_id:
            return SystemActionResult(
                action="cancel_current_request",
                ok=False,
                message="No active stream request to cancel.",
            )
        cancelled = bool(cancel_handler())
        if cancelled:
            self.finish_stream(
                request_id=request_id,
                session_id=session_id or "",
                status="cancelled",
                detail={"message": "Cancelled by system bridge action."},
            )
        return SystemActionResult(
            action="cancel_current_request",
            ok=cancelled,
            message="Cancelled active stream request." if cancelled else "Cancel handler returned false.",
            detail={"request_id": request_id},
        )
