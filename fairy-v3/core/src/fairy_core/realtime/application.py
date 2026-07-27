from __future__ import annotations

from fairy_core.commanding import EventVisibility
from fairy_core.contracts.realtime import (
    GameMemorySaveInput,
    RealtimeAssistanceRequestInput,
    RealtimeProviderSelection,
    RealtimeSessionReportInput,
    RealtimeSessionStartInput,
    RealtimeTranscriptAppendInput,
)
from fairy_core.domain.errors import InvalidTransitionError, VersionConflictError
from fairy_core.persistence.unit_of_work import CoreUnitOfWork, CoreUnitOfWorkFactory
from fairy_core.realtime.models import (
    GameMemoryDigest,
    RealtimeAssistance,
    RealtimeAssistanceStatus,
    RealtimeMemoryMode,
    RealtimeProvider,
    RealtimeSession,
    RealtimeSessionStatus,
    RealtimeTranscriptEntry,
)

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
            if session.status not in {
                RealtimeSessionStatus.COMPLETED,
                RealtimeSessionStatus.FAILED,
                RealtimeSessionStatus.CANCELLED,
                RealtimeSessionStatus.INTERRUPTED,
            }:
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
                accepted=True,
            )
            saved = unit_of_work.realtime.add_memory(memory)
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


__all__ = ["RealtimeApplication"]
