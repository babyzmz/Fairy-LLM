from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from threading import RLock
from typing import TypeVar
from uuid import UUID

from fairy_core.assistant.models import (
    AssistantTurn,
    AssistantTurnStatus,
    Message,
    MessageRole,
    MessageVisibility,
)
from fairy_core.commanding import EventVisibility
from fairy_core.contracts.models import (
    TranscriptSegmentModel,
    VoiceAudioModel,
    VoiceSynthesizeInput,
    VoiceTranscribeInput,
    VoiceTranscriptModel,
)
from fairy_core.contracts.voice_sessions import (
    VoiceSessionIdInput,
    VoiceSessionModel,
    VoiceSessionStartInput,
    VoiceSessionStatus,
)
from fairy_core.domain.errors import CapabilityUnavailableError
from fairy_core.domain.ids import new_id
from fairy_core.domain.models import Task
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
        self._session_lock = RLock()
        self._sessions: dict[UUID, VoiceSessionModel] = {}
        self._session_idempotency: dict[str, UUID] = {}

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
        task, turn, message, text = self._validated_message_range(
            task_id=request.task_id,
            turn_id=request.turn_id,
            message_id=request.message_id,
            start_offset=request.start_offset,
            end_offset=request.end_offset,
        )
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

    def start_session(self, request: VoiceSessionStartInput) -> VoiceSessionModel:
        with self._session_lock:
            existing_id = self._session_idempotency.get(request.idempotency_key)
            if existing_id is not None:
                existing = self._sessions[existing_id]
                if (
                    existing.task_id != request.task_id
                    or existing.turn_id != request.turn_id
                    or existing.message_id != request.message_id
                    or existing.start_offset != request.start_offset
                    or existing.end_offset != request.end_offset
                ):
                    raise ValueError("voice session idempotency key was reused")
                return existing
        task, turn, message_id, text, source_cursor = self._validated_session_range(
            task_id=request.task_id,
            turn_id=request.turn_id,
            message_id=request.message_id,
            start_offset=request.start_offset,
            end_offset=request.end_offset,
        )
        digest_payload = json.dumps(
            {
                "conversation_id": str(task.conversation_id),
                "end_offset": request.end_offset,
                "message_id": str(message_id) if message_id is not None else None,
                "source_cursor": source_cursor,
                "start_offset": request.start_offset,
                "task_id": str(task.id),
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "turn_id": str(turn.id),
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        session = VoiceSessionModel(
            id=new_id(),
            task_id=task.id,
            conversation_id=task.conversation_id,
            turn_id=turn.id,
            message_id=message_id,
            start_offset=request.start_offset,
            end_offset=request.end_offset,
            validated_text=text,
            source_cursor=source_cursor,
            scope_digest=hashlib.sha256(digest_payload.encode("ascii")).hexdigest(),
            status=VoiceSessionStatus.PREPARED,
            created_at=datetime.now(UTC),
        )
        with self._session_lock:
            existing_id = self._session_idempotency.get(request.idempotency_key)
            if existing_id is not None:
                return self._sessions[existing_id]
            self._sessions[session.id] = session
            self._session_idempotency[request.idempotency_key] = session.id
        return session

    def _validated_session_range(
        self,
        *,
        task_id: UUID,
        turn_id: UUID,
        message_id: UUID | None,
        start_offset: int,
        end_offset: int,
    ) -> tuple[Task, AssistantTurn, UUID | None, str, int]:
        if message_id is not None:
            task, turn, message, text = self._validated_message_range(
                task_id=task_id,
                turn_id=turn_id,
                message_id=message_id,
                start_offset=start_offset,
                end_offset=end_offset,
            )
            with self._unit_of_work_factory() as unit_of_work:
                source_cursor = unit_of_work.commands.current_cursor()
            return task, turn, message.id, text, source_cursor

        with self._unit_of_work_factory() as unit_of_work:
            task = unit_of_work.state.get_task(task_id)
            turn = unit_of_work.assistant.get_turn(turn_id)
            events = unit_of_work.commands.events_after(
                cursor=0,
                allowed_visibilities={EventVisibility.USER},
            )
        if task is None:
            raise KeyError(f"task not found: {task_id}")
        if turn is None:
            raise KeyError(f"Assistant Turn not found: {turn_id}")
        if (
            turn.task_id != task.id
            or turn.conversation_id != task.conversation_id
            or turn.status
            not in {
                AssistantTurnStatus.RUNNING,
                AssistantTurnStatus.WAITING_FOR_TOOL,
                AssistantTurnStatus.COMPLETED,
            }
        ):
            raise ValueError("voice streaming requires a public active assistant turn")
        deltas = [
            event
            for event in events
            if event.task_id == task.id
            and event.conversation_id == task.conversation_id
            and event.event_type == "assistant.message.delta"
            and event.payload.get("turn_id") == str(turn.id)
            and isinstance(event.payload.get("model_round"), int)
            and isinstance(event.payload.get("chunk_index"), int)
            and isinstance(event.payload.get("text"), str)
        ]
        deltas.sort(
            key=lambda event: (
                int(event.payload["model_round"]),
                int(event.payload["chunk_index"]),
                event.cursor,
            )
        )
        seen: set[tuple[int, int]] = set()
        chunks: list[str] = []
        source_cursor = 0
        for event in deltas:
            coordinate = (
                int(event.payload["model_round"]),
                int(event.payload["chunk_index"]),
            )
            if coordinate in seen:
                continue
            seen.add(coordinate)
            chunks.append(str(event.payload["text"]))
            source_cursor = max(source_cursor, event.cursor)
        content = "".join(chunks)
        if end_offset > len(content):
            raise ValueError("voice session range exceeds durable assistant output")
        text = content[start_offset:end_offset]
        if not text.strip():
            raise ValueError("voice session range must contain spoken text")
        return task, turn, None, text, source_cursor

    def get_session(self, request: VoiceSessionIdInput) -> VoiceSessionModel:
        with self._session_lock:
            session = self._sessions.get(request.session_id)
        if session is None:
            raise KeyError(f"voice session not found: {request.session_id}")
        return session

    def cancel_session(self, request: VoiceSessionIdInput) -> VoiceSessionModel:
        with self._session_lock:
            session = self._sessions.get(request.session_id)
            if session is None:
                raise KeyError(f"voice session not found: {request.session_id}")
            if session.status is VoiceSessionStatus.CANCELLED:
                return session
            cancelled = session.model_copy(
                update={
                    "status": VoiceSessionStatus.CANCELLED,
                    "cancelled_at": datetime.now(UTC),
                }
            )
            self._sessions[request.session_id] = cancelled
            return cancelled

    def _validated_message_range(
        self,
        *,
        task_id: UUID,
        turn_id: UUID,
        message_id: UUID,
        start_offset: int,
        end_offset: int,
    ) -> tuple[Task, AssistantTurn, Message, str]:
        with self._unit_of_work_factory() as unit_of_work:
            task = unit_of_work.state.get_task(task_id)
            turn = unit_of_work.assistant.get_turn(turn_id)
            message = unit_of_work.assistant.get_message(message_id)
        if task is None:
            raise KeyError(f"task not found: {task_id}")
        if turn is None:
            raise KeyError(f"Assistant Turn not found: {turn_id}")
        if message is None:
            raise KeyError(f"assistant message not found: {message_id}")
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
        if end_offset > len(message.content):
            raise ValueError("voice synthesis range exceeds the assistant message")
        text = message.content[start_offset:end_offset]
        if not text.strip():
            raise ValueError("voice synthesis range must contain spoken text")
        return task, turn, message, text

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
