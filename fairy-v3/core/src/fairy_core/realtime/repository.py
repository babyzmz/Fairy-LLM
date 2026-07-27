from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from fairy_core.domain.errors import IdempotencyConflictError, VersionConflictError
from fairy_core.persistence.tenant import normalize_tenant_id
from fairy_core.realtime.models import (
    GameMemoryDigest,
    RealtimeAssistance,
    RealtimeAssistanceCitation,
    RealtimeAssistanceStatus,
    RealtimeCaptionSpeaker,
    RealtimeMemoryMode,
    RealtimeProvider,
    RealtimeSession,
    RealtimeSessionStatus,
    RealtimeTranscriptEntry,
    RealtimeVoiceMode,
)
from fairy_core.storage.schema import (
    game_memory_observations,
    realtime_assistance,
    realtime_sessions,
    realtime_transcript_entries,
)


class SqlAlchemyRealtimeRepository:
    def __init__(self, connection: Connection, *, tenant_id: str) -> None:
        self._connection = connection
        self._tenant_id = normalize_tenant_id(tenant_id)

    def add_session(self, session: RealtimeSession) -> RealtimeSession:
        try:
            self._connection.execute(
                insert(realtime_sessions).values(**_session_values(self._tenant_id, session))
            )
        except IntegrityError as error:
            existing = self.get_session_by_idempotency_key(session.idempotency_key)
            if existing is not None and _same_session_request(existing, session):
                return existing
            raise IdempotencyConflictError(
                "realtime session idempotency key is already in use"
            ) from error
        return session

    def get_session(self, session_id: UUID) -> RealtimeSession | None:
        row = (
            self._connection.execute(
                select(realtime_sessions).where(
                    realtime_sessions.c.tenant_id == self._tenant_id,
                    realtime_sessions.c.id == str(session_id),
                )
            )
            .mappings()
            .one_or_none()
        )
        return _session_from_row(row) if row is not None else None

    def get_session_by_idempotency_key(self, key: str) -> RealtimeSession | None:
        row = (
            self._connection.execute(
                select(realtime_sessions).where(
                    realtime_sessions.c.tenant_id == self._tenant_id,
                    realtime_sessions.c.idempotency_key == key,
                )
            )
            .mappings()
            .one_or_none()
        )
        return _session_from_row(row) if row is not None else None

    def update_session(
        self, session: RealtimeSession, *, expected_revision: int
    ) -> RealtimeSession:
        values = _session_values(self._tenant_id, session)
        values.pop("tenant_id")
        values.pop("id")
        changed = self._connection.execute(
            update(realtime_sessions)
            .where(
                realtime_sessions.c.tenant_id == self._tenant_id,
                realtime_sessions.c.id == str(session.id),
                realtime_sessions.c.revision == expected_revision,
            )
            .values(**values)
        )
        if changed.rowcount != 1:
            raise VersionConflictError("realtime session revision changed")
        return session

    def list_sessions(self, *, limit: int = 50) -> tuple[RealtimeSession, ...]:
        if limit < 1 or limit > 200:
            raise ValueError("realtime session limit must be between 1 and 200")
        rows = self._connection.execute(
            select(realtime_sessions)
            .where(realtime_sessions.c.tenant_id == self._tenant_id)
            .order_by(realtime_sessions.c.started_at.desc())
            .limit(limit)
        ).mappings()
        return tuple(_session_from_row(row) for row in rows)

    def add_assistance(self, assistance: RealtimeAssistance) -> RealtimeAssistance:
        try:
            self._connection.execute(
                insert(realtime_assistance).values(
                    **_assistance_values(self._tenant_id, assistance)
                )
            )
        except IntegrityError as error:
            existing = self.get_assistance_by_request(
                assistance.session_id,
                assistance.request_id,
            )
            if existing is not None and existing.same_request(assistance):
                return existing
            raise IdempotencyConflictError(
                "realtime assistance request id is already in use"
            ) from error
        return assistance

    def get_assistance(self, assistance_id: UUID) -> RealtimeAssistance | None:
        row = (
            self._connection.execute(
                select(realtime_assistance).where(
                    realtime_assistance.c.tenant_id == self._tenant_id,
                    realtime_assistance.c.id == str(assistance_id),
                )
            )
            .mappings()
            .one_or_none()
        )
        return _assistance_from_row(row) if row is not None else None

    def get_assistance_by_request(
        self,
        session_id: UUID,
        request_id: str,
    ) -> RealtimeAssistance | None:
        row = (
            self._connection.execute(
                select(realtime_assistance).where(
                    realtime_assistance.c.tenant_id == self._tenant_id,
                    realtime_assistance.c.session_id == str(session_id),
                    realtime_assistance.c.request_id == request_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        return _assistance_from_row(row) if row is not None else None

    def nonterminal_assistance_for_session(
        self,
        session_id: UUID,
    ) -> RealtimeAssistance | None:
        row = (
            self._connection.execute(
                select(realtime_assistance)
                .where(
                    realtime_assistance.c.tenant_id == self._tenant_id,
                    realtime_assistance.c.session_id == str(session_id),
                    realtime_assistance.c.status.in_(
                        (
                            RealtimeAssistanceStatus.QUEUED.value,
                            RealtimeAssistanceStatus.RUNNING.value,
                            RealtimeAssistanceStatus.AWAITING_APPROVAL.value,
                        )
                    ),
                )
                .order_by(realtime_assistance.c.created_at, realtime_assistance.c.id)
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        return _assistance_from_row(row) if row is not None else None

    def assistance_for_task(self, task_id: UUID) -> RealtimeAssistance | None:
        row = (
            self._connection.execute(
                select(realtime_assistance).where(
                    realtime_assistance.c.tenant_id == self._tenant_id,
                    realtime_assistance.c.task_id == str(task_id),
                )
            )
            .mappings()
            .one_or_none()
        )
        return _assistance_from_row(row) if row is not None else None

    def update_assistance(
        self,
        assistance: RealtimeAssistance,
        *,
        expected_revision: int,
    ) -> RealtimeAssistance:
        values = _assistance_values(self._tenant_id, assistance)
        values.pop("tenant_id")
        values.pop("id")
        changed = self._connection.execute(
            update(realtime_assistance)
            .where(
                realtime_assistance.c.tenant_id == self._tenant_id,
                realtime_assistance.c.id == str(assistance.id),
                realtime_assistance.c.revision == expected_revision,
            )
            .values(**values)
        )
        if changed.rowcount != 1:
            raise VersionConflictError("realtime assistance revision changed")
        return assistance

    def add_memory(self, memory: GameMemoryDigest) -> GameMemoryDigest:
        self._connection.execute(
            insert(game_memory_observations).values(
                tenant_id=self._tenant_id,
                id=str(memory.id),
                session_id=str(memory.session_id),
                game_title=memory.game_title,
                played_at=memory.played_at,
                duration_seconds=memory.duration_seconds,
                activities=list(memory.activities),
                progress_summary=memory.progress_summary,
                next_goal=memory.next_goal,
                notable_outcome=memory.notable_outcome,
                accepted=memory.accepted,
                created_at=memory.created_at,
            )
        )
        return memory

    def get_memory(self, memory_id: UUID) -> GameMemoryDigest | None:
        row = (
            self._connection.execute(
                select(game_memory_observations).where(
                    game_memory_observations.c.tenant_id == self._tenant_id,
                    game_memory_observations.c.id == str(memory_id),
                )
            )
            .mappings()
            .one_or_none()
        )
        return _memory_from_row(row) if row is not None else None

    def list_memories(self, *, limit: int = 50) -> tuple[GameMemoryDigest, ...]:
        if limit < 1 or limit > 200:
            raise ValueError("game memory limit must be between 1 and 200")
        rows = self._connection.execute(
            select(game_memory_observations)
            .where(game_memory_observations.c.tenant_id == self._tenant_id)
            .order_by(game_memory_observations.c.played_at.desc())
            .limit(limit)
        ).mappings()
        return tuple(_memory_from_row(row) for row in rows)

    def delete_memory(self, memory_id: UUID) -> bool:
        changed = self._connection.execute(
            delete(game_memory_observations).where(
                game_memory_observations.c.tenant_id == self._tenant_id,
                game_memory_observations.c.id == str(memory_id),
            )
        )
        return changed.rowcount == 1

    def append_transcript(
        self,
        *,
        session_id: UUID,
        conversation_id: UUID,
        speaker: RealtimeCaptionSpeaker,
        text: str,
    ) -> RealtimeTranscriptEntry:
        last_error: IntegrityError | None = None
        for _ in range(6):
            sequence = self._next_transcript_sequence(session_id)
            entry = RealtimeTranscriptEntry.create(
                session_id=session_id,
                conversation_id=conversation_id,
                sequence=sequence,
                speaker=speaker,
                text=text,
            )
            try:
                self._connection.execute(
                    insert(realtime_transcript_entries).values(
                        **_transcript_values(self._tenant_id, entry)
                    )
                )
            except IntegrityError as error:
                # A concurrent append took this per-session sequence; retry.
                last_error = error
                continue
            return entry
        raise IdempotencyConflictError("realtime transcript sequence contention") from last_error

    def list_transcript_by_conversation(
        self, conversation_id: UUID, *, limit: int = 500
    ) -> tuple[RealtimeTranscriptEntry, ...]:
        if limit < 1 or limit > 2_000:
            raise ValueError("transcript limit must be between 1 and 2000")
        rows = self._connection.execute(
            select(realtime_transcript_entries)
            .where(
                realtime_transcript_entries.c.tenant_id == self._tenant_id,
                realtime_transcript_entries.c.conversation_id == str(conversation_id),
            )
            .order_by(
                realtime_transcript_entries.c.created_at,
                realtime_transcript_entries.c.sequence,
                realtime_transcript_entries.c.id,
            )
            .limit(limit)
        ).mappings()
        return tuple(_transcript_from_row(row) for row in rows)

    def _next_transcript_sequence(self, session_id: UUID) -> int:
        current = self._connection.execute(
            select(func.coalesce(func.max(realtime_transcript_entries.c.sequence), 0)).where(
                realtime_transcript_entries.c.tenant_id == self._tenant_id,
                realtime_transcript_entries.c.session_id == str(session_id),
            )
        ).scalar_one()
        return int(current) + 1


def _session_values(tenant_id: str, session: RealtimeSession) -> dict[str, object]:
    return {
        "tenant_id": tenant_id,
        "id": str(session.id),
        "idempotency_key": session.idempotency_key,
        "device_id": session.device_id,
        "conversation_id": str(session.conversation_id) if session.conversation_id else None,
        "provider": session.provider.value,
        "model_id": session.model_id,
        "voice_mode": session.voice_mode.value,
        "memory_mode": session.memory_mode.value,
        "status": session.status.value,
        "microphone_consent": session.microphone_consent,
        "screen_consent": session.screen_consent,
        "game_audio_consent": session.game_audio_consent,
        "audio_input_ms": session.audio_input_ms,
        "audio_output_ms": session.audio_output_ms,
        "video_frame_count": session.video_frame_count,
        "interruption_count": session.interruption_count,
        "tool_call_count": session.tool_call_count,
        "last_error_code": session.last_error_code,
        "started_at": session.started_at,
        "ended_at": session.ended_at,
        "revision": session.revision,
    }


def _assistance_values(
    tenant_id: str,
    assistance: RealtimeAssistance,
) -> dict[str, object]:
    return {
        "tenant_id": tenant_id,
        "id": str(assistance.id),
        "session_id": str(assistance.session_id),
        "conversation_id": str(assistance.conversation_id),
        "request_id": assistance.request_id,
        "request_fingerprint": assistance.request_fingerprint,
        "segment_id": assistance.segment_id,
        "context_epoch": assistance.context_epoch,
        "question": assistance.question,
        "activity_profile": assistance.activity_profile,
        "application_title": assistance.application_title,
        "observed_facts": list(assistance.observed_facts),
        "allow_network": assistance.allow_network,
        "locale": assistance.locale,
        "status": assistance.status.value,
        "task_id": str(assistance.task_id) if assistance.task_id is not None else None,
        "turn_id": str(assistance.turn_id) if assistance.turn_id is not None else None,
        "message_id": str(assistance.message_id) if assistance.message_id is not None else None,
        "spoken_summary": assistance.spoken_summary,
        "display_markdown": assistance.display_markdown,
        "citations": [
            {"title": citation.title, "url": citation.url} for citation in assistance.citations
        ],
        "freshness": assistance.freshness,
        "requires_user_confirmation": assistance.requires_user_confirmation,
        "error_code": assistance.error_code,
        "created_at": assistance.created_at,
        "updated_at": assistance.updated_at,
        "revision": assistance.revision,
    }


def _same_session_request(left: RealtimeSession, right: RealtimeSession) -> bool:
    return (
        left.device_id,
        left.conversation_id,
        left.provider,
        left.model_id,
        left.voice_mode,
        left.memory_mode,
        left.microphone_consent,
        left.screen_consent,
        left.game_audio_consent,
    ) == (
        right.device_id,
        right.conversation_id,
        right.provider,
        right.model_id,
        right.voice_mode,
        right.memory_mode,
        right.microphone_consent,
        right.screen_consent,
        right.game_audio_consent,
    )


def _session_from_row(row: Mapping[str, object]) -> RealtimeSession:
    return RealtimeSession(
        id=UUID(str(row["id"])),
        idempotency_key=str(row["idempotency_key"]),
        device_id=str(row["device_id"]),
        conversation_id=UUID(str(row["conversation_id"])) if row["conversation_id"] else None,
        provider=RealtimeProvider(str(row["provider"])),
        model_id=str(row["model_id"]),
        voice_mode=RealtimeVoiceMode(str(row["voice_mode"])),
        memory_mode=RealtimeMemoryMode(str(row["memory_mode"])),
        status=RealtimeSessionStatus(str(row["status"])),
        microphone_consent=bool(row["microphone_consent"]),
        screen_consent=bool(row["screen_consent"]),
        game_audio_consent=bool(row["game_audio_consent"]),
        audio_input_ms=int(row["audio_input_ms"]),
        audio_output_ms=int(row["audio_output_ms"]),
        video_frame_count=int(row["video_frame_count"]),
        interruption_count=int(row["interruption_count"]),
        tool_call_count=int(row["tool_call_count"]),
        last_error_code=str(row["last_error_code"]) if row["last_error_code"] else None,
        started_at=row["started_at"],  # type: ignore[arg-type]
        ended_at=row["ended_at"],  # type: ignore[arg-type]
        revision=int(row["revision"]),
    )


def _assistance_from_row(row: Mapping[str, object]) -> RealtimeAssistance:
    citations = row["citations"]
    return RealtimeAssistance(
        id=UUID(str(row["id"])),
        session_id=UUID(str(row["session_id"])),
        conversation_id=UUID(str(row["conversation_id"])),
        request_id=str(row["request_id"]),
        request_fingerprint=str(row["request_fingerprint"]),
        segment_id=str(row["segment_id"]),
        context_epoch=int(row["context_epoch"]),
        question=str(row["question"]),
        activity_profile=str(row["activity_profile"]),
        application_title=(str(row["application_title"]) if row["application_title"] else None),
        observed_facts=tuple(str(value) for value in row["observed_facts"]),  # type: ignore[union-attr]
        allow_network=bool(row["allow_network"]),
        locale=str(row["locale"]),
        status=RealtimeAssistanceStatus(str(row["status"])),
        task_id=UUID(str(row["task_id"])) if row["task_id"] else None,
        turn_id=UUID(str(row["turn_id"])) if row["turn_id"] else None,
        message_id=UUID(str(row["message_id"])) if row["message_id"] else None,
        spoken_summary=(str(row["spoken_summary"]) if row["spoken_summary"] else None),
        display_markdown=(str(row["display_markdown"]) if row["display_markdown"] else None),
        citations=tuple(
            RealtimeAssistanceCitation(
                title=str(value["title"]),
                url=str(value["url"]),
            )
            for value in citations  # type: ignore[union-attr]
        ),
        freshness=str(row["freshness"]) if row["freshness"] else None,
        requires_user_confirmation=bool(row["requires_user_confirmation"]),
        error_code=str(row["error_code"]) if row["error_code"] else None,
        created_at=row["created_at"],  # type: ignore[arg-type]
        updated_at=row["updated_at"],  # type: ignore[arg-type]
        revision=int(row["revision"]),
    )


def _transcript_values(tenant_id: str, entry: RealtimeTranscriptEntry) -> dict[str, object]:
    return {
        "tenant_id": tenant_id,
        "id": str(entry.id),
        "session_id": str(entry.session_id),
        "conversation_id": str(entry.conversation_id),
        "sequence": entry.sequence,
        "speaker": entry.speaker.value,
        "text": entry.text,
        "created_at": entry.created_at,
    }


def _transcript_from_row(row: Mapping[str, object]) -> RealtimeTranscriptEntry:
    return RealtimeTranscriptEntry(
        id=UUID(str(row["id"])),
        session_id=UUID(str(row["session_id"])),
        conversation_id=UUID(str(row["conversation_id"])),
        sequence=int(row["sequence"]),
        speaker=RealtimeCaptionSpeaker(str(row["speaker"])),
        text=str(row["text"]),
        created_at=row["created_at"],  # type: ignore[arg-type]
    )


def _memory_from_row(row: Mapping[str, object]) -> GameMemoryDigest:
    return GameMemoryDigest(
        id=UUID(str(row["id"])),
        session_id=UUID(str(row["session_id"])),
        game_title=str(row["game_title"]),
        played_at=row["played_at"],  # type: ignore[arg-type]
        duration_seconds=int(row["duration_seconds"]),
        activities=tuple(str(item) for item in row["activities"]),  # type: ignore[union-attr]
        progress_summary=str(row["progress_summary"]),
        next_goal=str(row["next_goal"]) if row["next_goal"] else None,
        notable_outcome=str(row["notable_outcome"]) if row["notable_outcome"] else None,
        accepted=bool(row["accepted"]),
        created_at=row["created_at"],  # type: ignore[arg-type]
    )


__all__ = ["SqlAlchemyRealtimeRepository"]
