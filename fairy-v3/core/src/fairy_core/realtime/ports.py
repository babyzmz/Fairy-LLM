from __future__ import annotations

from typing import Protocol
from uuid import UUID

from fairy_core.realtime.models import (
    GameMemoryDigest,
    RealtimeCaptionSpeaker,
    RealtimeSession,
    RealtimeTranscriptEntry,
)


class RealtimeRepository(Protocol):
    def add_session(self, session: RealtimeSession) -> RealtimeSession: ...

    def get_session(self, session_id: UUID) -> RealtimeSession | None: ...

    def get_session_by_idempotency_key(self, key: str) -> RealtimeSession | None: ...

    def update_session(
        self, session: RealtimeSession, *, expected_revision: int
    ) -> RealtimeSession: ...

    def list_sessions(self, *, limit: int = 50) -> tuple[RealtimeSession, ...]: ...

    def add_memory(self, memory: GameMemoryDigest) -> GameMemoryDigest: ...

    def get_memory(self, memory_id: UUID) -> GameMemoryDigest | None: ...

    def list_memories(self, *, limit: int = 50) -> tuple[GameMemoryDigest, ...]: ...

    def delete_memory(self, memory_id: UUID) -> bool: ...

    def append_transcript(
        self,
        *,
        session_id: UUID,
        conversation_id: UUID,
        speaker: RealtimeCaptionSpeaker,
        text: str,
    ) -> RealtimeTranscriptEntry: ...

    def list_transcript_by_conversation(
        self, conversation_id: UUID, *, limit: int = 500
    ) -> tuple[RealtimeTranscriptEntry, ...]: ...


__all__ = ["RealtimeRepository"]
