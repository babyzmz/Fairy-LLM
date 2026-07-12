from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from fairy_core.contracts.models import (
    VoiceAudioModel,
    VoiceSynthesizeInput,
    VoiceTranscribeInput,
    VoiceTranscriptModel,
)
from fairy_core.contracts.voice_sessions import (
    VoiceSessionIdInput,
    VoiceSessionModel,
    VoiceSessionStartInput,
)
from fastapi import APIRouter

SyncInvoke = Callable[[str, dict[str, Any]], dict[str, Any]]
AsyncInvoke = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


def install_voice_routes(
    router: APIRouter,
    *,
    invoke: SyncInvoke,
    invoke_async: AsyncInvoke,
) -> None:
    @router.post(
        "/voice/transcriptions",
        operation_id="voice.transcribe",
        response_model=VoiceTranscriptModel,
    )
    async def transcribe_voice(request: VoiceTranscribeInput) -> dict[str, Any]:
        return await invoke_async("voice.transcribe", request.model_dump(mode="json"))

    @router.post(
        "/voice/speech",
        operation_id="voice.synthesize",
        response_model=VoiceAudioModel,
    )
    async def synthesize_voice(request: VoiceSynthesizeInput) -> dict[str, Any]:
        return await invoke_async("voice.synthesize", request.model_dump(mode="json"))

    @router.post(
        "/voice/sessions",
        operation_id="voice.sessions.start",
        response_model=VoiceSessionModel,
    )
    def start_voice_session(request: VoiceSessionStartInput) -> dict[str, Any]:
        return invoke("voice.sessions.start", request.model_dump(mode="json"))

    @router.get(
        "/voice/sessions/{session_id}",
        operation_id="voice.sessions.get",
        response_model=VoiceSessionModel,
    )
    def get_voice_session(session_id: UUID) -> dict[str, Any]:
        request = VoiceSessionIdInput(session_id=session_id)
        return invoke("voice.sessions.get", request.model_dump(mode="json"))

    @router.delete(
        "/voice/sessions/{session_id}",
        operation_id="voice.sessions.cancel",
        response_model=VoiceSessionModel,
    )
    def cancel_voice_session(session_id: UUID) -> dict[str, Any]:
        request = VoiceSessionIdInput(session_id=session_id)
        return invoke("voice.sessions.cancel", request.model_dump(mode="json"))


__all__ = ["install_voice_routes"]
