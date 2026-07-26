from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any, cast

from pydantic import BaseModel

from fairy_core.contracts.realtime import (
    GameMemoryIdInput,
    GameMemoryListInput,
    GameMemorySaveInput,
    RealtimeSessionIdInput,
    RealtimeSessionListInput,
    RealtimeSessionReportInput,
    RealtimeSessionStartInput,
    RealtimeSessionStopInput,
    RealtimeTranscriptAppendInput,
    RealtimeTranscriptListInput,
)
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory
from fairy_core.realtime.application import RealtimeApplication

Handler = Callable[[BaseModel], Any]


class RealtimeService:
    def __init__(
        self,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        *,
        conversation_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._application = RealtimeApplication(unit_of_work_factory)
        self._conversation_factory = conversation_factory
        self.handlers: Mapping[str, Handler] = MappingProxyType(
            {
                "realtime.sessions.start": self.start,
                "realtime.sessions.get": self.get,
                "realtime.sessions.list": self.list,
                "realtime.sessions.report": self.report,
                "realtime.sessions.stop": self.stop,
                "realtime.memories.save": self.save_memory,
                "realtime.memories.list": self.list_memories,
                "realtime.memories.delete": self.delete_memory,
                "realtime.transcript.append": self.append_transcript,
                "realtime.transcript.list": self.list_transcript,
            }
        )

    def start(self, request: BaseModel):
        validated = cast(RealtimeSessionStartInput, request)
        if validated.conversation_id is None and self._conversation_factory is not None:
            # Link the voice session to a fresh scratch conversation so it appears
            # in history. Captions stay ephemeral; only the association is saved.
            # Return an idempotent replay unchanged so a retry never leaks an
            # extra empty conversation.
            existing = self._application.find_session_by_idempotency_key(validated.idempotency_key)
            if existing is not None:
                return existing
            conversation = self._conversation_factory()
            validated = validated.model_copy(update={"conversation_id": conversation.id})
        return self._application.start(validated)

    def get(self, request: BaseModel):
        return self._application.get(cast(RealtimeSessionIdInput, request).session_id)

    def list(self, request: BaseModel):
        validated = cast(RealtimeSessionListInput, request)
        return {"items": self._application.list(limit=validated.limit)}

    def report(self, request: BaseModel):
        return self._application.report(cast(RealtimeSessionReportInput, request))

    def stop(self, request: BaseModel):
        validated = cast(RealtimeSessionStopInput, request)
        return self._application.request_stop(
            validated.session_id, expected_revision=validated.expected_revision
        )

    def save_memory(self, request: BaseModel):
        return self._application.save_memory(cast(GameMemorySaveInput, request))

    def list_memories(self, request: BaseModel):
        validated = cast(GameMemoryListInput, request)
        return {"items": self._application.list_memories(limit=validated.limit)}

    def delete_memory(self, request: BaseModel):
        validated = cast(GameMemoryIdInput, request)
        return {
            "memory_id": validated.memory_id,
            "deleted": self._application.delete_memory(validated.memory_id),
        }

    def append_transcript(self, request: BaseModel):
        return self._application.append_transcript(
            cast(RealtimeTranscriptAppendInput, request)
        )

    def list_transcript(self, request: BaseModel):
        validated = cast(RealtimeTranscriptListInput, request)
        return {
            "items": self._application.list_transcript(
                validated.conversation_id, limit=validated.limit
            )
        }


__all__ = ["RealtimeService"]
