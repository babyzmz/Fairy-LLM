from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from fairy_core.commanding import EventVisibility
from fairy_core.contracts.realtime import (
    CompanionDigestCreateInput,
    GameMemorySaveInput,
    RealtimeAssistanceRequestInput,
    RealtimeCloudUsageInput,
    RealtimeMemoryProposalActionInput,
    RealtimeProviderSelection,
    RealtimeSessionReportInput,
    RealtimeSessionStartInput,
    RealtimeTranscriptAppendInput,
)
from fairy_core.domain.errors import InvalidTransitionError, VersionConflictError
from fairy_core.persistence.unit_of_work import CoreUnitOfWork, CoreUnitOfWorkFactory
from fairy_core.realtime.memory import (
    USER_MEMORY_ACTOR,
    build_memory_proposals,
    promote_memory_proposal,
)
from fairy_core.realtime.models import (
    CompanionDigestActivity,
    CompanionSessionDigest,
    GameMemoryDigest,
    RealtimeAssistance,
    RealtimeAssistanceStatus,
    RealtimeCaptionSpeaker,
    RealtimeMemoryMode,
    RealtimeMemoryProposal,
    RealtimeMemoryProposalDecision,
    RealtimeMemoryProposalStatus,
    RealtimeProvider,
    RealtimeSession,
    RealtimeSessionStatus,
    RealtimeTranscriptEntry,
)

_DIGEST_POLICY_VERSION = "companion-digest-v1"
_TERMINAL_SESSION_STATUSES = {
    RealtimeSessionStatus.COMPLETED,
    RealtimeSessionStatus.FAILED,
    RealtimeSessionStatus.CANCELLED,
    RealtimeSessionStatus.INTERRUPTED,
}

_MODEL_BY_PROVIDER = {
    RealtimeProvider.LOCAL_MINI_CPM_O45: "openbmb/minicpm-o-4.5-fairy-beta@4.5-q4-502eec5",
    RealtimeProvider.GEMINI_LIVE: "gemini-3.1-flash-live-preview",
    RealtimeProvider.GLM_REALTIME_FLASH: "glm-realtime-flash",
    RealtimeProvider.GLM_REALTIME_AIR: "glm-realtime-air",
}


class RealtimeApplication:
    def __init__(self, unit_of_work_factory: CoreUnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def start(self, request: RealtimeSessionStartInput) -> RealtimeSession:
        with self._unit_of_work_factory() as unit_of_work:
            saved = self.start_in_unit_of_work(request, unit_of_work)
            unit_of_work.commit()
            return saved

    def start_in_unit_of_work(
        self,
        request: RealtimeSessionStartInput,
        unit_of_work: CoreUnitOfWork,
    ) -> RealtimeSession:
        provider = _resolve_provider(request.provider, request.locale)
        candidate = RealtimeSession.create(
            device_id=request.device_id,
            idempotency_key=request.idempotency_key,
            conversation_id=request.conversation_id,
            provider=provider,
            model_id=_MODEL_BY_PROVIDER[provider],
            voice_mode=request.voice_mode,
            memory_mode=request.memory_mode,
            microphone_consent=request.microphone_consent,
            screen_consent=request.screen_consent,
            game_audio_consent=request.game_audio_consent,
        )
        return unit_of_work.realtime.add_session(candidate)

    def get(self, session_id):
        with self._unit_of_work_factory() as unit_of_work:
            session = unit_of_work.realtime.get_session(session_id)
        if session is None:
            raise KeyError(f"realtime session not found: {session_id}")
        return session

    def list(self, *, limit: int) -> tuple[RealtimeSession, ...]:
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.realtime.list_sessions(limit=limit)

    def cloud_usage(self, request: RealtimeCloudUsageInput) -> int:
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.realtime.cloud_wall_time_ms(
                day_start=datetime.fromtimestamp(request.day_start_ms / 1_000, tz=UTC),
                day_end=datetime.fromtimestamp(request.day_end_ms / 1_000, tz=UTC),
                observed_at=datetime.now(UTC),
            )

    def request_assistance(
        self,
        request: RealtimeAssistanceRequestInput,
    ) -> RealtimeAssistance:
        candidate = RealtimeAssistance.create(
            session_id=request.session_id,
            conversation_id=request.conversation_id,
            request_id=request.request_id,
            segment_id=request.segment_id,
            context_epoch=request.context_epoch,
            question=request.question,
            activity_profile=request.activity_profile,
            application_title=request.application_title,
            observed_facts=request.observed_facts,
            allow_network=request.allow_network,
            locale=request.locale,
        )
        with self._unit_of_work_factory() as unit_of_work:
            session = unit_of_work.realtime.get_session(request.session_id)
            if session is None:
                raise KeyError(f"realtime session not found: {request.session_id}")
            if session.conversation_id != request.conversation_id:
                raise ValueError("Realtime Assistance conversation does not match the session")
            if session.status not in {
                RealtimeSessionStatus.STARTING,
                RealtimeSessionStatus.ACTIVE,
            }:
                raise InvalidTransitionError(
                    "Realtime Assistance requires an active realtime session"
                )
            conversation = unit_of_work.state.get_conversation(request.conversation_id)
            if conversation is None:
                raise KeyError(f"conversation not found: {request.conversation_id}")
            existing = unit_of_work.realtime.get_assistance_by_request(
                request.session_id,
                candidate.request_id,
            )
            if existing is not None:
                if not existing.same_request(candidate):
                    # Keep the repository's public conflict semantics without
                    # attempting an insert that could obscure scope validation.
                    unit_of_work.realtime.add_assistance(candidate)
                return existing
            active = unit_of_work.realtime.nonterminal_assistance_for_session(request.session_id)
            if active is not None:
                raise InvalidTransitionError(
                    "a Realtime Assistance request is already active for this session"
                )
            saved = unit_of_work.realtime.add_assistance(candidate)
            unit_of_work.commands.append_domain_event(
                event_type="realtime.assistance.requested",
                visibility=EventVisibility.USER,
                message="Realtime Assistance requested",
                payload={
                    "assistance_id": str(saved.id),
                    "session_id": str(saved.session_id),
                    "request_id": saved.request_id,
                    "status": saved.status.value,
                    "allow_network": saved.allow_network,
                },
                actor="realtime",
                conversation_id=saved.conversation_id,
            )
            unit_of_work.commit()
            return saved

    def get_assistance(
        self,
        session_id,
        request_id: str,
    ) -> RealtimeAssistance:
        with self._unit_of_work_factory() as unit_of_work:
            assistance = unit_of_work.realtime.get_assistance_by_request(
                session_id,
                request_id,
            )
        if assistance is None:
            raise KeyError(f"realtime assistance not found: {session_id}/{request_id}")
        return assistance

    def update_assistance(
        self,
        assistance: RealtimeAssistance,
        *,
        expected_revision: int,
    ) -> RealtimeAssistance:
        with self._unit_of_work_factory() as unit_of_work:
            saved = unit_of_work.realtime.update_assistance(
                assistance,
                expected_revision=expected_revision,
            )
            unit_of_work.commands.append_domain_event(
                event_type="realtime.assistance.state.changed",
                visibility=EventVisibility.USER,
                message="Realtime Assistance state changed",
                payload={
                    "assistance_id": str(saved.id),
                    "session_id": str(saved.session_id),
                    "request_id": saved.request_id,
                    "status": saved.status.value,
                    "requires_user_confirmation": saved.requires_user_confirmation,
                    "error_code": saved.error_code,
                },
                actor="core",
                conversation_id=saved.conversation_id,
                task_id=saved.task_id,
            )
            unit_of_work.commit()
            return saved

    def cancel_assistance(
        self,
        session_id,
        request_id: str,
        *,
        expected_revision: int,
    ) -> RealtimeAssistance:
        current = self.get_assistance(session_id, request_id)
        if current.status in {
            RealtimeAssistanceStatus.COMPLETED,
            RealtimeAssistanceStatus.FAILED,
            RealtimeAssistanceStatus.CANCELLED,
        }:
            return current
        if current.revision != expected_revision:
            raise VersionConflictError("realtime assistance revision changed")
        return self.update_assistance(
            current.cancel(),
            expected_revision=current.revision,
        )

    def request_stop(self, session_id, *, expected_revision: int) -> RealtimeSession:
        with self._unit_of_work_factory() as unit_of_work:
            current = unit_of_work.realtime.get_session(session_id)
            if current is None:
                raise KeyError(f"realtime session not found: {session_id}")
            if current.status in {
                RealtimeSessionStatus.STOPPING,
                RealtimeSessionStatus.COMPLETED,
                RealtimeSessionStatus.FAILED,
                RealtimeSessionStatus.CANCELLED,
                RealtimeSessionStatus.INTERRUPTED,
            }:
                return current
            if current.revision != expected_revision:
                raise VersionConflictError("realtime session revision changed")
            stopped = current.transition_to(
                RealtimeSessionStatus.CANCELLED
                if current.status is RealtimeSessionStatus.STARTING
                else RealtimeSessionStatus.STOPPING
            )
            unit_of_work.realtime.update_session(stopped, expected_revision=expected_revision)
            unit_of_work.commit()
            return stopped

    def report(self, request: RealtimeSessionReportInput) -> RealtimeSession:
        with self._unit_of_work_factory() as unit_of_work:
            current = unit_of_work.realtime.get_session(request.session_id)
            if current is None:
                raise KeyError(f"realtime session not found: {request.session_id}")
            if current.revision != request.expected_revision:
                raise VersionConflictError("realtime session revision changed")
            updated = current.with_usage(
                audio_input_ms=request.audio_input_ms,
                audio_output_ms=request.audio_output_ms,
                video_frame_count=request.video_frame_count,
                interruption_count=request.interruption_count,
                tool_call_count=request.tool_call_count,
            )
            if request.status is not current.status:
                updated = updated.transition_to(request.status, error_code=request.error_code)
            elif request.error_code is not None:
                raise InvalidTransitionError("error_code requires a failed status transition")
            unit_of_work.realtime.update_session(updated, expected_revision=current.revision)
            unit_of_work.commit()
            return updated

    def save_memory(self, request: GameMemorySaveInput) -> GameMemoryDigest:
        with self._unit_of_work_factory() as unit_of_work:
            session = unit_of_work.realtime.get_session(request.session_id)
            if session is None:
                raise KeyError(f"realtime session not found: {request.session_id}")
            if session.memory_mode is RealtimeMemoryMode.NONE:
                raise ValueError("memory persistence is disabled for this session")
            if session.status not in _TERMINAL_SESSION_STATUSES:
                raise InvalidTransitionError("game memory can only be saved after the session ends")
            memory = GameMemoryDigest.create(
                session_id=session.id,
                game_title=request.game_title,
                played_at=request.played_at,
                duration_seconds=request.duration_seconds,
                activities=request.activities,
                progress_summary=request.progress_summary,
                next_goal=request.next_goal,
                notable_outcome=request.notable_outcome,
                accepted=False,
            )
            saved = unit_of_work.realtime.add_memory(memory)
            unit_of_work.commit()
            return saved

    def create_digest(
        self,
        request: CompanionDigestCreateInput,
    ) -> CompanionSessionDigest:
        with self._unit_of_work_factory() as unit_of_work:
            session = unit_of_work.realtime.get_session(request.session_id)
            if session is None:
                raise KeyError(f"realtime session not found: {request.session_id}")
            if session.memory_mode is RealtimeMemoryMode.NONE:
                raise ValueError("memory persistence is disabled for this session")
            if session.status not in _TERMINAL_SESSION_STATUSES or session.ended_at is None:
                raise InvalidTransitionError(
                    "companion digest can only be created after the session ends"
                )
            if session.conversation_id is None:
                raise ValueError("realtime session has no linked conversation")
            entries = unit_of_work.realtime.list_transcript_by_session(
                session.id,
                limit=2_000,
            )
            if not entries:
                raise ValueError("companion digest requires stable public transcript entries")
            if any(entry.conversation_id != session.conversation_id for entry in entries):
                raise ValueError("realtime transcript is outside the session conversation")
            summary = _deterministic_digest_summary(entries)
            source_digest = _transcript_source_digest(entries)
            candidate = CompanionSessionDigest.create(
                session_id=session.id,
                conversation_id=session.conversation_id,
                request_id=request.request_id,
                activity=request.activity,
                subject_title=request.subject_title,
                started_at=session.started_at,
                ended_at=session.ended_at,
                activities=summary["activities"],
                progress_summary=summary["progress_summary"],
                unresolved_issue=summary["unresolved_issue"],
                next_goal=summary["next_goal"],
                notable_outcome=summary["notable_outcome"],
                source_first_sequence=entries[0].sequence,
                source_last_sequence=entries[-1].sequence,
                source_digest=source_digest,
                policy_version=_DIGEST_POLICY_VERSION,
            )
            saved = unit_of_work.realtime.add_digest(candidate)
            if saved.id == candidate.id:
                unit_of_work.commands.append_domain_event(
                    event_type="realtime.digest.created",
                    visibility=EventVisibility.USER,
                    message="Realtime companion session digest created",
                    payload={
                        "session_id": str(session.id),
                        "conversation_id": str(session.conversation_id),
                        "digest_id": str(saved.id),
                        "activity": saved.activity.value,
                        "source_first_sequence": saved.source_first_sequence,
                        "source_last_sequence": saved.source_last_sequence,
                        "source_digest": saved.source_digest,
                        "policy_version": saved.policy_version,
                    },
                    actor="realtime",
                    conversation_id=session.conversation_id,
                )
                proposal_ids: list = []
                if unit_of_work.memory_settings.get().enabled:
                    for proposal in build_memory_proposals(
                        digest=saved,
                        entries=entries,
                        device_id=session.device_id,
                        unit_of_work=unit_of_work,
                    ):
                        persisted = unit_of_work.realtime.add_memory_proposal(proposal)
                        if (
                            persisted.status is RealtimeMemoryProposalStatus.PENDING
                            and persisted.policy_decision
                            is RealtimeMemoryProposalDecision.AUTO_PROMOTE
                        ):
                            persisted = promote_memory_proposal(
                                unit_of_work=unit_of_work,
                                proposal=persisted,
                                device_id=session.device_id,
                                decision_idempotency_key=f"auto:{persisted.id}",
                                explicit_user=False,
                            )
                        proposal_ids.append(persisted.id)
                    if proposal_ids:
                        updated = saved.with_proposals(tuple(proposal_ids))
                        saved = unit_of_work.realtime.update_digest(
                            updated,
                            expected_revision=saved.revision,
                        )
            unit_of_work.commit()
            return saved

    def get_digest(self, digest_id) -> CompanionSessionDigest:
        with self._unit_of_work_factory() as unit_of_work:
            digest = unit_of_work.realtime.get_digest(digest_id)
            if digest is not None:
                return digest
            legacy = unit_of_work.realtime.get_memory(digest_id)
            if legacy is None:
                raise KeyError(f"companion digest not found: {digest_id}")
            session = unit_of_work.realtime.get_session(legacy.session_id)
            if session is None or session.conversation_id is None:
                raise KeyError(f"realtime session not found: {legacy.session_id}")
            return _project_game_memory(legacy, session.conversation_id)

    def list_digests(
        self,
        *,
        session_id=None,
        limit: int,
    ) -> tuple[CompanionSessionDigest, ...]:
        with self._unit_of_work_factory() as unit_of_work:
            persisted = list(
                unit_of_work.realtime.list_digests(
                    session_id=session_id,
                    limit=limit,
                )
            )
            legacy = unit_of_work.realtime.list_memories(limit=limit)
            for memory in legacy:
                if session_id is not None and memory.session_id != session_id:
                    continue
                session = unit_of_work.realtime.get_session(memory.session_id)
                if session is None or session.conversation_id is None:
                    continue
                persisted.append(_project_game_memory(memory, session.conversation_id))
            persisted.sort(key=lambda value: (value.created_at, str(value.id)), reverse=True)
            return tuple(persisted[:limit])

    def list_memory_proposals(
        self,
        *,
        session_id=None,
        digest_id=None,
        pending_only: bool,
        limit: int,
    ) -> tuple[RealtimeMemoryProposal, ...]:
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.realtime.list_memory_proposals(
                session_id=session_id,
                digest_id=digest_id,
                status=(RealtimeMemoryProposalStatus.PENDING if pending_only else None),
                limit=limit,
            )

    def accept_memory_proposal(
        self,
        request: RealtimeMemoryProposalActionInput,
    ) -> RealtimeMemoryProposal:
        if not request.user_confirmed:
            raise ValueError("realtime memory acceptance requires explicit confirmation")
        with self._unit_of_work_factory() as unit_of_work:
            if not unit_of_work.memory_settings.get().enabled:
                raise RuntimeError("Memory is disabled by Core settings")
            proposal = unit_of_work.realtime.get_memory_proposal(request.proposal_id)
            if proposal is None:
                raise KeyError(f"realtime memory proposal not found: {request.proposal_id}")
            if (
                proposal.status is RealtimeMemoryProposalStatus.PROMOTED
                and proposal.decision_idempotency_key == request.idempotency_key
            ):
                return proposal
            if proposal.revision != request.expected_revision:
                raise VersionConflictError("realtime memory proposal revision changed")
            session = unit_of_work.realtime.get_session(proposal.session_id)
            if session is None:
                raise KeyError(f"realtime session not found: {proposal.session_id}")
            promoted = promote_memory_proposal(
                unit_of_work=unit_of_work,
                proposal=proposal,
                device_id=session.device_id,
                decision_idempotency_key=request.idempotency_key,
                explicit_user=True,
            )
            unit_of_work.commit()
            return promoted

    def reject_memory_proposal(
        self,
        request: RealtimeMemoryProposalActionInput,
    ) -> RealtimeMemoryProposal:
        if not request.user_confirmed:
            raise ValueError("realtime memory rejection requires explicit confirmation")
        with self._unit_of_work_factory() as unit_of_work:
            proposal = unit_of_work.realtime.get_memory_proposal(request.proposal_id)
            if proposal is None:
                raise KeyError(f"realtime memory proposal not found: {request.proposal_id}")
            if (
                proposal.status is RealtimeMemoryProposalStatus.REJECTED
                and proposal.decision_idempotency_key == request.idempotency_key
            ):
                return proposal
            if proposal.revision != request.expected_revision:
                raise VersionConflictError("realtime memory proposal revision changed")
            rejected = proposal.reject(
                decision_idempotency_key=request.idempotency_key,
            )
            saved = unit_of_work.realtime.update_memory_proposal(
                rejected,
                expected_revision=proposal.revision,
            )
            unit_of_work.commands.append_domain_event(
                event_type="realtime.memory.rejected",
                visibility=EventVisibility.USER,
                message="Realtime memory proposal rejected",
                payload={
                    "proposal_id": str(saved.id),
                    "digest_id": str(saved.digest_id),
                    "session_id": str(saved.session_id),
                    "kind": saved.kind.value,
                },
                actor=USER_MEMORY_ACTOR,
                conversation_id=saved.conversation_id,
            )
            unit_of_work.commit()
            return saved

    def list_memories(self, *, limit: int) -> tuple[GameMemoryDigest, ...]:
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.realtime.list_memories(limit=limit)

    def append_transcript(self, request: RealtimeTranscriptAppendInput) -> RealtimeTranscriptEntry:
        with self._unit_of_work_factory() as unit_of_work:
            session = unit_of_work.realtime.get_session(request.session_id)
            if session is None:
                raise KeyError(f"realtime session not found: {request.session_id}")
            if session.conversation_id is None:
                raise ValueError("realtime session has no linked conversation")
            if session.status in _TERMINAL_SESSION_STATUSES:
                raise InvalidTransitionError(
                    "stable transcript cannot be appended after the session ends"
                )
            entry = unit_of_work.realtime.append_transcript(
                session_id=session.id,
                conversation_id=session.conversation_id,
                speaker=request.speaker,
                text=request.text,
            )
            unit_of_work.commands.append_domain_event(
                event_type="realtime.transcript.appended",
                visibility=EventVisibility.USER,
                message="Realtime transcript entry appended",
                payload={
                    "session_id": str(session.id),
                    "conversation_id": str(session.conversation_id),
                    "entry_id": str(entry.id),
                    "sequence": entry.sequence,
                },
                actor="realtime",
                conversation_id=session.conversation_id,
            )
            unit_of_work.commit()
            return entry

    def list_transcript(
        self, conversation_id, *, limit: int
    ) -> tuple[RealtimeTranscriptEntry, ...]:
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.realtime.list_transcript_by_conversation(
                conversation_id, limit=limit
            )

    def delete_memory(self, memory_id) -> bool:
        with self._unit_of_work_factory() as unit_of_work:
            deleted = unit_of_work.realtime.delete_memory(memory_id)
            unit_of_work.commit()
            return deleted


def _resolve_provider(selection: RealtimeProviderSelection, locale: str) -> RealtimeProvider:
    if selection is RealtimeProviderSelection.AUTO:
        return (
            RealtimeProvider.GLM_REALTIME_FLASH
            if locale.casefold().startswith("zh")
            else RealtimeProvider.GEMINI_LIVE
        )
    return RealtimeProvider(selection.value)


def _truncate(value: str, maximum: int) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= maximum:
        return normalized
    return normalized[: maximum - 1].rstrip() + "…"


def _deterministic_digest_summary(
    entries: tuple[RealtimeTranscriptEntry, ...],
) -> dict[str, object]:
    user_entries = tuple(
        entry.text for entry in entries if entry.speaker is RealtimeCaptionSpeaker.USER
    )
    source = user_entries or tuple(entry.text for entry in entries)
    activities = tuple(_truncate(value, 160) for value in source[-12:])
    progress_summary = _truncate(" ".join(source[-6:]), 1_200)
    unresolved_issue = next(
        (
            _truncate(value, 500)
            for value in reversed(user_entries)
            if "?" in value or "\uff1f" in value
        ),
        None,
    )
    next_goal = next(
        (
            _truncate(value, 500)
            for value in reversed(user_entries)
            if any(
                marker in value.casefold()
                for marker in ("next", "goal", "接下来", "下一步", "下次", "目标")
            )
        ),
        None,
    )
    notable_outcome = next(
        (
            _truncate(value, 500)
            for value in reversed(tuple(entry.text for entry in entries))
            if any(
                marker in value.casefold()
                for marker in ("complete", "finished", "passed", "success", "完成", "通过", "成功")
            )
        ),
        None,
    )
    return {
        "activities": activities,
        "progress_summary": progress_summary,
        "unresolved_issue": unresolved_issue,
        "next_goal": next_goal,
        "notable_outcome": notable_outcome,
    }


def _transcript_source_digest(entries: tuple[RealtimeTranscriptEntry, ...]) -> str:
    canonical = tuple(
        {
            "sequence": entry.sequence,
            "speaker": entry.speaker.value,
            "text": entry.text,
        }
        for entry in entries
    )
    return sha256(
        json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _project_game_memory(
    memory: GameMemoryDigest,
    conversation_id,
) -> CompanionSessionDigest:
    source_digest = sha256(
        json.dumps(
            {
                "legacy_game_memory_id": str(memory.id),
                "session_id": str(memory.session_id),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    request_id = f"legacy:{memory.id}"
    request_fingerprint = sha256(request_id.encode("utf-8")).hexdigest()
    return CompanionSessionDigest(
        id=memory.id,
        session_id=memory.session_id,
        conversation_id=conversation_id,
        request_id=request_id,
        request_fingerprint=request_fingerprint,
        activity=CompanionDigestActivity.GAME,
        subject_title=memory.game_title,
        started_at=memory.played_at,
        ended_at=memory.played_at + timedelta(seconds=memory.duration_seconds),
        duration_seconds=memory.duration_seconds,
        activities=memory.activities,
        progress_summary=memory.progress_summary,
        unresolved_issue=None,
        next_goal=memory.next_goal,
        notable_outcome=memory.notable_outcome,
        source_first_sequence=1,
        source_last_sequence=1,
        source_digest=source_digest,
        policy_version="legacy-game-memory-v1",
        proposal_ids=(),
        created_at=memory.created_at,
        revision=1,
    )


__all__ = ["RealtimeApplication"]
