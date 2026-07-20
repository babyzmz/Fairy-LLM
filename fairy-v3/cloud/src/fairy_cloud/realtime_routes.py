from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any
from uuid import UUID

from fairy_core.contracts.realtime import (
    GameMemoryDeleteResult,
    GameMemoryDigestModel,
    GameMemoryIdInput,
    GameMemoryListInput,
    GameMemoryPageModel,
    GameMemorySaveInput,
    RealtimeSessionIdInput,
    RealtimeSessionListInput,
    RealtimeSessionModel,
    RealtimeSessionPageModel,
    RealtimeSessionReportInput,
    RealtimeSessionStartInput,
    RealtimeSessionStopInput,
)
from fastapi import APIRouter, Header

SyncInvoke = Callable[[str, dict[str, Any]], dict[str, Any]]
IdempotencyGuard = Callable[[str, str], None]


def install_realtime_routes(
    router: APIRouter,
    *,
    invoke: SyncInvoke,
    guard: IdempotencyGuard,
) -> None:
    @router.post(
        "/realtime/sessions",
        operation_id="realtime.sessions.start",
        response_model=RealtimeSessionModel,
    )
    def start_session(
        request: RealtimeSessionStartInput,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=512),
        ],
    ) -> dict[str, Any]:
        guard(request.idempotency_key, idempotency_key)
        return invoke("realtime.sessions.start", request.model_dump(mode="json"))

    @router.get(
        "/realtime/sessions",
        operation_id="realtime.sessions.list",
        response_model=RealtimeSessionPageModel,
    )
    def list_sessions(limit: int = 50) -> dict[str, Any]:
        request = RealtimeSessionListInput(limit=limit)
        return invoke("realtime.sessions.list", request.model_dump(mode="json"))

    @router.get(
        "/realtime/sessions/{session_id}",
        operation_id="realtime.sessions.get",
        response_model=RealtimeSessionModel,
    )
    def get_session(session_id: UUID) -> dict[str, Any]:
        request = RealtimeSessionIdInput(session_id=session_id)
        return invoke("realtime.sessions.get", request.model_dump(mode="json"))

    @router.post(
        "/realtime/sessions/{session_id}/stop",
        operation_id="realtime.sessions.stop",
        response_model=RealtimeSessionModel,
    )
    def stop_session(
        session_id: UUID,
        request: RealtimeSessionStopInput,
    ) -> dict[str, Any]:
        if request.session_id != session_id:
            raise ValueError("realtime session id mismatch")
        return invoke("realtime.sessions.stop", request.model_dump(mode="json"))

    @router.post(
        "/realtime/sessions/{session_id}/report",
        operation_id="realtime.sessions.report",
        response_model=RealtimeSessionModel,
    )
    def report_session(
        session_id: UUID,
        request: RealtimeSessionReportInput,
    ) -> dict[str, Any]:
        if request.session_id != session_id:
            raise ValueError("realtime session id mismatch")
        return invoke("realtime.sessions.report", request.model_dump(mode="json"))

    @router.post(
        "/realtime/memories",
        operation_id="realtime.memories.save",
        response_model=GameMemoryDigestModel,
    )
    def save_memory(request: GameMemorySaveInput) -> dict[str, Any]:
        return invoke("realtime.memories.save", request.model_dump(mode="json"))

    @router.get(
        "/realtime/memories",
        operation_id="realtime.memories.list",
        response_model=GameMemoryPageModel,
    )
    def list_memories(limit: int = 50) -> dict[str, Any]:
        request = GameMemoryListInput(limit=limit)
        return invoke("realtime.memories.list", request.model_dump(mode="json"))

    @router.delete(
        "/realtime/memories/{memory_id}",
        operation_id="realtime.memories.delete",
        response_model=GameMemoryDeleteResult,
    )
    def delete_memory(memory_id: UUID) -> dict[str, Any]:
        request = GameMemoryIdInput(memory_id=memory_id)
        return invoke("realtime.memories.delete", request.model_dump(mode="json"))


__all__ = ["install_realtime_routes"]
