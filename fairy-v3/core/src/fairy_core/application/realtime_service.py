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
from fairy_core.persistence.unit_of_work import CoreUnitOfWork, CoreUnitOfWorkFactory
from fairy_core.realtime.application import RealtimeApplication

Handler = Callable[[BaseModel], Any]


class RealtimeService:
    def __init__(
        self,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        *,
        scratch_conversation_factory: Callable[[CoreUnitOfWork], Any] | None = None,
        scratch_conversation_cleanup: Callable[[Any], None] | None = None,
    ) -> None:
        if (scratch_conversation_factory is None) != (
            scratch_conversation_cleanup is None
        ):
            raise ValueError(
                "scratch conversation factory and cleanup must be configured together"
            )
        self._unit_of_work_factory = unit_of_work_factory
        self._application = RealtimeApplication(unit_of_work_factory)
        self._scratch_conversation_factory = scratch_conversation_factory
        self._scratch_conversation_cleanup = scratch_conversation_cleanup
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
        if (
            validated.conversation_id is None
            and self._scratch_conversation_factory is not None
        ):
            # Link the voice session to a fresh scratch conversation so it appears
            # in history. Conversation, Workspace, Version, and Session share one
            # transaction; the new filesystem workspace is compensated on failure.
            conversation = None
            try:
                with self._unit_of_work_factory() as unit_of_work:
                    existing = unit_of_work.realtime.get_session_by_idempotency_key(
                        validated.idempotency_key
                    )
                    if existing is not None:
                        return existing
                    conversation = self._scratch_conversation_factory(unit_of_work)
                    linked = validated.model_copy(
                        update={"conversation_id": conversation.id}
                    )
                    started = self._application.start_in_unit_of_work(
                        linked,
                        unit_of_work,
                    )
                    unit_of_work.commit()
                    return started
            except BaseException:
                if (
                    conversation is not None
                    and self._scratch_conversation_cleanup is not None
                ):
                    self._scratch_conversation_cleanup(conversation)
                raise
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
