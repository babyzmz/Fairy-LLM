from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from typing import TypeVar

from fairy_core.assistant.models import (
    AssistantTurnStatus,
    MessageRole,
    MessageVisibility,
)
from fairy_core.contracts.models import (
    TranscriptSegmentModel,
    VoiceAudioModel,
    VoiceSynthesizeInput,
    VoiceTranscribeInput,
    VoiceTranscriptModel,
)
from fairy_core.domain.errors import CapabilityUnavailableError
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.providers import CancellationToken, ProviderError
from fairy_core.voice.models import (
    TranscriptionRequest,
    VoiceSynthesisRequest,
)
from fairy_core.voice.registry import VoiceRegistry

VoiceResult = TypeVar("VoiceResult")


class VoiceApplication:
    def __init__(
        self,
        *,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        registry: VoiceRegistry,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._registry = registry

    def transcribe(self, request: VoiceTranscribeInput) -> VoiceTranscriptModel:
        with self._unit_of_work_factory() as unit_of_work:
            if unit_of_work.state.get_conversation(request.conversation_id) is None:
                raise KeyError(f"conversation not found: {request.conversation_id}")
        audio = _decode_base64(request.audio_base64)
        result = self._provider_call(
            lambda: self._registry.transcribe(
                TranscriptionRequest.create(
                    profile_id=request.profile_id,
                    media_type=request.media_type,
                    audio=audio,
                    language=request.language,
                ),
                CancellationToken(),
            )
        )
        return VoiceTranscriptModel(
            conversation_id=request.conversation_id,
            profile_id=result.profile_id,
            text=result.text,
            language=result.language,
            segments=tuple(
                TranscriptSegmentModel(
                    index=segment.index,
                    text=segment.text,
                    start_seconds=segment.start_seconds,
                    end_seconds=segment.end_seconds,
                )
                for segment in result.segments
            ),
        )

    def synthesize(self, request: VoiceSynthesizeInput) -> VoiceAudioModel:
        with self._unit_of_work_factory() as unit_of_work:
            task = unit_of_work.state.get_task(request.task_id)
            turn = unit_of_work.assistant.get_turn(request.turn_id)
            message = unit_of_work.assistant.get_message(request.message_id)
        if task is None:
            raise KeyError(f"task not found: {request.task_id}")
        if turn is None:
            raise KeyError(f"Assistant Turn not found: {request.turn_id}")
        if message is None:
            raise KeyError(f"assistant message not found: {request.message_id}")
        if (
            turn.status is not AssistantTurnStatus.COMPLETED
            or turn.task_id != task.id
            or turn.conversation_id != task.conversation_id
            or message.task_id != task.id
            or message.turn_id != turn.id
            or message.conversation_id != task.conversation_id
            or message.role is not MessageRole.ASSISTANT
            or message.visibility is not MessageVisibility.USER
        ):
            raise ValueError("voice synthesis requires a public completed assistant message")
        if request.end_offset > len(message.content):
            raise ValueError("voice synthesis range exceeds the assistant message")
        text = message.content[request.start_offset : request.end_offset]
        if not text.strip():
            raise ValueError("voice synthesis range must contain spoken text")
        result = self._provider_call(
            lambda: self._registry.synthesize(
                VoiceSynthesisRequest.create(
                    profile_id=request.profile_id,
                    voice=request.voice,
                    text=text,
                ),
                CancellationToken(),
            )
        )
        return VoiceAudioModel(
            task_id=task.id,
            turn_id=turn.id,
            message_id=message.id,
            profile_id=result.profile_id,
            start_offset=request.start_offset,
            end_offset=request.end_offset,
            media_type="audio/wav",
            audio_base64=base64.b64encode(result.wav).decode("ascii"),
            sample_rate=result.sample_rate,
            channels=result.channels,
            frames=result.frames,
            content_hash=result.content_hash,
        )

    @staticmethod
    def _provider_call(operation: Callable[[], VoiceResult]) -> VoiceResult:
        try:
            return operation()
        except ProviderError as error:
            raise CapabilityUnavailableError(str(error)) from error


def _decode_base64(value: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("voice audio must be canonical base64") from error


__all__ = ["VoiceApplication"]
