from __future__ import annotations

import json
from collections.abc import Mapping
from uuid import UUID

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from fairy_core.domain.errors import IdempotencyConflictError, VersionConflictError
from fairy_core.memory.models import MemoryNamespace, MemorySensitivity
from fairy_core.persistence.tenant import normalize_tenant_id
from fairy_core.realtime.models import (
    CompanionDigestActivity,
    CompanionSessionDigest,
    GameMemoryDigest,
    RealtimeAssistance,
    RealtimeAssistanceCitation,
    RealtimeAssistanceStatus,
    RealtimeCaptionSpeaker,
    RealtimeMemoryMode,
    RealtimeMemoryProposal,
    RealtimeMemoryProposalDecision,
    RealtimeMemoryProposalKind,
    RealtimeMemoryProposalStatus,
    RealtimeProvider,
    RealtimeSession,
    RealtimeSessionStatus,
    RealtimeTranscriptEntry,
    RealtimeVoiceMode,
)
from fairy_core.storage.schema import (
    companion_session_digests,
    game_memory_observations,
    realtime_assistance,
    realtime_memory_proposals,
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

    def add_digest(self, digest: CompanionSessionDigest) -> CompanionSessionDigest:
        try:
            self._connection.execute(
                insert(companion_session_digests).values(**_digest_values(self._tenant_id, digest))
            )
        except IntegrityError as error:
            existing = self.get_digest_by_request(digest.session_id, digest.request_id)
            if existing is not None and existing.same_request(digest):
                return existing
            raise IdempotencyConflictError(
                "companion digest request id is already in use"
            ) from error
        return digest

    def get_digest(self, digest_id: UUID) -> CompanionSessionDigest | None:
        row = (
            self._connection.execute(
                select(companion_session_digests).where(
                    companion_session_digests.c.tenant_id == self._tenant_id,
                    companion_session_digests.c.id == str(digest_id),
                )
            )
            .mappings()
            .one_or_none()
        )
        return _digest_from_row(row) if row is not None else None

    def get_digest_by_request(
        self,
        session_id: UUID,
        request_id: str,
    ) -> CompanionSessionDigest | None:
        row = (
            self._connection.execute(
                select(companion_session_digests).where(
                    companion_session_digests.c.tenant_id == self._tenant_id,
                    companion_session_digests.c.session_id == str(session_id),
                    companion_session_digests.c.request_id == request_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        return _digest_from_row(row) if row is not None else None

    def list_digests(
        self,
        *,
        session_id: UUID | None = None,
        limit: int = 50,
    ) -> tuple[CompanionSessionDigest, ...]:
        if limit < 1 or limit > 200:
            raise ValueError("companion digest limit must be between 1 and 200")
        query = select(companion_session_digests).where(
            companion_session_digests.c.tenant_id == self._tenant_id
        )
        if session_id is not None:
            query = query.where(companion_session_digests.c.session_id == str(session_id))
        rows = self._connection.execute(
            query.order_by(
                companion_session_digests.c.created_at.desc(),
                companion_session_digests.c.id.desc(),
            ).limit(limit)
        ).mappings()
        return tuple(_digest_from_row(row) for row in rows)

    def update_digest(
        self,
        digest: CompanionSessionDigest,
        *,
        expected_revision: int,
    ) -> CompanionSessionDigest:
        values = _digest_values(self._tenant_id, digest)
        values.pop("tenant_id")
        values.pop("id")
        changed = self._connection.execute(
            update(companion_session_digests)
            .where(
                companion_session_digests.c.tenant_id == self._tenant_id,
                companion_session_digests.c.id == str(digest.id),
                companion_session_digests.c.revision == expected_revision,
            )
            .values(**values)
        )
        if changed.rowcount != 1:
            raise VersionConflictError("companion digest revision changed")
        return digest

    def add_memory_proposal(
        self,
        proposal: RealtimeMemoryProposal,
    ) -> RealtimeMemoryProposal:
        try:
            self._connection.execute(
                insert(realtime_memory_proposals).values(
                    **_proposal_values(self._tenant_id, proposal)
                )
            )
        except IntegrityError as error:
            existing = self._proposal_by_evidence(
                proposal.digest_id,
                proposal.kind,
                proposal.evidence_digest,
            )
            if existing is not None and _same_proposal(existing, proposal):
                return existing
            raise IdempotencyConflictError(
                "realtime memory proposal evidence is already in use"
            ) from error
        return proposal

    def get_memory_proposal(
        self,
        proposal_id: UUID,
    ) -> RealtimeMemoryProposal | None:
        row = (
            self._connection.execute(
                select(realtime_memory_proposals).where(
                    realtime_memory_proposals.c.tenant_id == self._tenant_id,
                    realtime_memory_proposals.c.id == str(proposal_id),
                )
            )
            .mappings()
            .one_or_none()
        )
        return _proposal_from_row(row) if row is not None else None

    def list_memory_proposals(
        self,
        *,
        session_id: UUID | None = None,
        digest_id: UUID | None = None,
        status: RealtimeMemoryProposalStatus | None = None,
        limit: int = 100,
    ) -> tuple[RealtimeMemoryProposal, ...]:
        if limit < 1 or limit > 500:
            raise ValueError("realtime memory proposal limit must be between 1 and 500")
        query = select(realtime_memory_proposals).where(
            realtime_memory_proposals.c.tenant_id == self._tenant_id
        )
        if session_id is not None:
            query = query.where(realtime_memory_proposals.c.session_id == str(session_id))
        if digest_id is not None:
            query = query.where(realtime_memory_proposals.c.digest_id == str(digest_id))
        if status is not None:
            query = query.where(realtime_memory_proposals.c.status == status.value)
        rows = self._connection.execute(
            query.order_by(
                realtime_memory_proposals.c.created_at.desc(),
                realtime_memory_proposals.c.id.desc(),
            ).limit(limit)
        ).mappings()
        return tuple(_proposal_from_row(row) for row in rows)

    def update_memory_proposal(
        self,
        proposal: RealtimeMemoryProposal,
        *,
        expected_revision: int,
    ) -> RealtimeMemoryProposal:
        values = _proposal_values(self._tenant_id, proposal)
        values.pop("tenant_id")
        values.pop("id")
        changed = self._connection.execute(
            update(realtime_memory_proposals)
            .where(
                realtime_memory_proposals.c.tenant_id == self._tenant_id,
                realtime_memory_proposals.c.id == str(proposal.id),
                realtime_memory_proposals.c.revision == expected_revision,
            )
            .values(**values)
        )
        if changed.rowcount != 1:
            raise VersionConflictError("realtime memory proposal revision changed")
        return proposal

    def _proposal_by_evidence(
        self,
        digest_id: UUID,
        kind: RealtimeMemoryProposalKind,
        evidence_digest: str,
    ) -> RealtimeMemoryProposal | None:
        row = (
            self._connection.execute(
                select(realtime_memory_proposals).where(
                    realtime_memory_proposals.c.tenant_id == self._tenant_id,
                    realtime_memory_proposals.c.digest_id == str(digest_id),
                    realtime_memory_proposals.c.kind == kind.value,
                    realtime_memory_proposals.c.evidence_digest == evidence_digest,
                )
            )
            .mappings()
            .one_or_none()
        )
        return _proposal_from_row(row) if row is not None else None

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

    def list_transcript_by_session(
        self,
        session_id: UUID,
        *,
        limit: int = 2_000,
    ) -> tuple[RealtimeTranscriptEntry, ...]:
        if limit < 1 or limit > 2_000:
            raise ValueError("transcript limit must be between 1 and 2000")
        rows = self._connection.execute(
            select(realtime_transcript_entries)
            .where(
                realtime_transcript_entries.c.tenant_id == self._tenant_id,
                realtime_transcript_entries.c.session_id == str(session_id),
            )
            .order_by(
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


def _digest_values(
    tenant_id: str,
    digest: CompanionSessionDigest,
) -> dict[str, object]:
    return {
        "tenant_id": tenant_id,
        "id": str(digest.id),
        "session_id": str(digest.session_id),
        "conversation_id": str(digest.conversation_id),
        "request_id": digest.request_id,
        "request_fingerprint": digest.request_fingerprint,
        "activity": digest.activity.value,
        "subject_title": digest.subject_title,
        "started_at": digest.started_at,
        "ended_at": digest.ended_at,
        "duration_seconds": digest.duration_seconds,
        "activities": list(digest.activities),
        "progress_summary": digest.progress_summary,
        "unresolved_issue": digest.unresolved_issue,
        "next_goal": digest.next_goal,
        "notable_outcome": digest.notable_outcome,
        "source_first_sequence": digest.source_first_sequence,
        "source_last_sequence": digest.source_last_sequence,
        "source_digest": digest.source_digest,
        "policy_version": digest.policy_version,
        "proposal_ids": [str(value) for value in digest.proposal_ids],
        "created_at": digest.created_at,
        "revision": digest.revision,
    }


def _proposal_values(
    tenant_id: str,
    proposal: RealtimeMemoryProposal,
) -> dict[str, object]:
    return {
        "tenant_id": tenant_id,
        "id": str(proposal.id),
        "digest_id": str(proposal.digest_id),
        "session_id": str(proposal.session_id),
        "conversation_id": str(proposal.conversation_id),
        "kind": proposal.kind.value,
        "subject": proposal.subject,
        "predicate": proposal.predicate,
        "value": proposal.value,
        "normalized_text": proposal.normalized_text,
        "target_namespace": proposal.target_namespace.value,
        "confidence": proposal.confidence,
        "sensitivity": proposal.sensitivity.value,
        "source_first_sequence": proposal.source_first_sequence,
        "source_last_sequence": proposal.source_last_sequence,
        "evidence_digest": proposal.evidence_digest,
        "policy_decision": proposal.policy_decision.value,
        "policy_reason": proposal.policy_reason,
        "status": proposal.status.value,
        "claim_id": str(proposal.claim_id) if proposal.claim_id is not None else None,
        "decision_idempotency_key": proposal.decision_idempotency_key,
        "created_at": proposal.created_at,
        "updated_at": proposal.updated_at,
        "revision": proposal.revision,
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


def _digest_from_row(row: Mapping[str, object]) -> CompanionSessionDigest:
    return CompanionSessionDigest(
        id=UUID(str(row["id"])),
        session_id=UUID(str(row["session_id"])),
        conversation_id=UUID(str(row["conversation_id"])),
        request_id=str(row["request_id"]),
        request_fingerprint=str(row["request_fingerprint"]),
        activity=CompanionDigestActivity(str(row["activity"])),
        subject_title=str(row["subject_title"]) if row["subject_title"] else None,
        started_at=row["started_at"],  # type: ignore[arg-type]
        ended_at=row["ended_at"],  # type: ignore[arg-type]
        duration_seconds=int(row["duration_seconds"]),
        activities=tuple(str(value) for value in row["activities"]),  # type: ignore[union-attr]
        progress_summary=str(row["progress_summary"]),
        unresolved_issue=(str(row["unresolved_issue"]) if row["unresolved_issue"] else None),
        next_goal=str(row["next_goal"]) if row["next_goal"] else None,
        notable_outcome=(str(row["notable_outcome"]) if row["notable_outcome"] else None),
        source_first_sequence=int(row["source_first_sequence"]),
        source_last_sequence=int(row["source_last_sequence"]),
        source_digest=str(row["source_digest"]),
        policy_version=str(row["policy_version"]),
        proposal_ids=tuple(UUID(str(value)) for value in row["proposal_ids"]),  # type: ignore[union-attr]
        created_at=row["created_at"],  # type: ignore[arg-type]
        revision=int(row["revision"]),
    )


def _proposal_from_row(row: Mapping[str, object]) -> RealtimeMemoryProposal:
    return RealtimeMemoryProposal(
        id=UUID(str(row["id"])),
        digest_id=UUID(str(row["digest_id"])),
        session_id=UUID(str(row["session_id"])),
        conversation_id=UUID(str(row["conversation_id"])),
        kind=RealtimeMemoryProposalKind(str(row["kind"])),
        subject=str(row["subject"]),
        predicate=str(row["predicate"]),
        _value_json=json.dumps(
            row["value"],
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        normalized_text=str(row["normalized_text"]),
        target_namespace=MemoryNamespace(str(row["target_namespace"])),
        confidence=float(row["confidence"]),
        sensitivity=MemorySensitivity(str(row["sensitivity"])),
        source_first_sequence=int(row["source_first_sequence"]),
        source_last_sequence=int(row["source_last_sequence"]),
        evidence_digest=str(row["evidence_digest"]),
        policy_decision=RealtimeMemoryProposalDecision(str(row["policy_decision"])),
        policy_reason=str(row["policy_reason"]),
        status=RealtimeMemoryProposalStatus(str(row["status"])),
        claim_id=UUID(str(row["claim_id"])) if row["claim_id"] else None,
        decision_idempotency_key=(
            str(row["decision_idempotency_key"]) if row["decision_idempotency_key"] else None
        ),
        created_at=row["created_at"],  # type: ignore[arg-type]
        updated_at=row["updated_at"],  # type: ignore[arg-type]
        revision=int(row["revision"]),
    )


def _same_proposal(
    left: RealtimeMemoryProposal,
    right: RealtimeMemoryProposal,
) -> bool:
    return (
        left.digest_id,
        left.session_id,
        left.conversation_id,
        left.kind,
        left.subject,
        left.predicate,
        left.value,
        left.normalized_text,
        left.target_namespace,
        left.confidence,
        left.sensitivity,
        left.source_first_sequence,
        left.source_last_sequence,
        left.evidence_digest,
        left.policy_decision,
        left.policy_reason,
    ) == (
        right.digest_id,
        right.session_id,
        right.conversation_id,
        right.kind,
        right.subject,
        right.predicate,
        right.value,
        right.normalized_text,
        right.target_namespace,
        right.confidence,
        right.sensitivity,
        right.source_first_sequence,
        right.source_last_sequence,
        right.evidence_digest,
        right.policy_decision,
        right.policy_reason,
    )


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
