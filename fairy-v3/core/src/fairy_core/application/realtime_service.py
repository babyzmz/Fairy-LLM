from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from contextlib import suppress
from threading import RLock
from types import MappingProxyType
from typing import Any, cast
from uuid import UUID

from pydantic import BaseModel

from fairy_core.assistant.models import AssistantTurnStatus, MessageRole
from fairy_core.contracts.model_routing import ModelSelectionSnapshotInput
from fairy_core.contracts.models import (
    AssistantTurnCreateInput,
    ExecutionTarget,
    TaskCreate,
)
from fairy_core.contracts.persona import (
    RealtimePersonaSnapshotInput,
    RealtimePersonaSnapshotModel,
)
from fairy_core.contracts.realtime import (
    CompanionDigestCreateInput,
    CompanionDigestGetInput,
    CompanionDigestListInput,
    GameMemoryIdInput,
    GameMemoryListInput,
    GameMemorySaveInput,
    RealtimeAssistanceCancelInput,
    RealtimeAssistanceGetInput,
    RealtimeAssistanceRequestInput,
    RealtimeSessionIdInput,
    RealtimeSessionListInput,
    RealtimeSessionReportInput,
    RealtimeSessionStartInput,
    RealtimeSessionStopInput,
    RealtimeTranscriptAppendInput,
    RealtimeTranscriptListInput,
)
from fairy_core.domain.errors import VersionConflictError
from fairy_core.domain.models import OperationMode, TaskStatus
from fairy_core.model_catalog.models import ModelSelectionPreference
from fairy_core.persistence.unit_of_work import CoreUnitOfWork, CoreUnitOfWorkFactory
from fairy_core.persona import (
    PersonaAuthority,
    load_default_persona_authority,
    project_realtime_persona_snapshot,
)
from fairy_core.realtime.application import RealtimeApplication
from fairy_core.realtime.models import (
    RealtimeAssistance,
    RealtimeAssistanceCitation,
    RealtimeAssistanceStatus,
)

Handler = Callable[[BaseModel], Any]
TaskFactory = Callable[[TaskCreate], Any]
TurnFactory = Callable[[AssistantTurnCreateInput], Any]
TurnStarter = Callable[[UUID], Any]
TurnCanceller = Callable[[UUID, int], Any]
SelectionProvider = Callable[[], ModelSelectionPreference]


class RealtimeService:
    def __init__(
        self,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        *,
        persona_authority: PersonaAuthority | None = None,
        scratch_conversation_factory: Callable[[CoreUnitOfWork], Any] | None = None,
        scratch_conversation_cleanup: Callable[[Any], None] | None = None,
        task_factory: TaskFactory,
        turn_factory: TurnFactory,
        turn_starter: TurnStarter,
        turn_canceller: TurnCanceller,
        selection_provider: SelectionProvider,
    ) -> None:
        if (scratch_conversation_factory is None) != (scratch_conversation_cleanup is None):
            raise ValueError("scratch conversation factory and cleanup must be configured together")
        self._unit_of_work_factory = unit_of_work_factory
        self._application = RealtimeApplication(unit_of_work_factory)
        self._persona_authority = persona_authority or load_default_persona_authority()
        self._scratch_conversation_factory = scratch_conversation_factory
        self._scratch_conversation_cleanup = scratch_conversation_cleanup
        self._task_factory = task_factory
        self._turn_factory = turn_factory
        self._turn_starter = turn_starter
        self._turn_canceller = turn_canceller
        self._selection_provider = selection_provider
        self._assistance_lock = RLock()
        self.handlers: Mapping[str, Handler] = MappingProxyType(
            {
                "realtime.assistance.cancel": self.cancel_assistance,
                "realtime.assistance.get": self.get_assistance,
                "realtime.assistance.request": self.request_assistance,
                "realtime.digests.create": self.create_digest,
                "realtime.digests.get": self.get_digest,
                "realtime.digests.list": self.list_digests,
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
                "realtime.persona.snapshot": self.persona_snapshot,
            }
        )

    def request_assistance(self, request: BaseModel) -> RealtimeAssistance:
        validated = cast(RealtimeAssistanceRequestInput, request)
        with self._assistance_lock:
            assistance = self._application.request_assistance(validated)
            if assistance.status is not RealtimeAssistanceStatus.QUEUED:
                return self._reconcile_assistance(assistance)
            with self._unit_of_work_factory() as unit_of_work:
                conversation = unit_of_work.state.get_conversation(assistance.conversation_id)
                if conversation is None:
                    raise KeyError(f"conversation not found: {assistance.conversation_id}")
                active_task = (
                    unit_of_work.state.get_task(conversation.active_task_id)
                    if conversation.active_task_id is not None
                    else None
                )
                busy = active_task is not None and active_task.status not in {
                    TaskStatus.READY,
                    TaskStatus.ACCEPTED,
                    TaskStatus.REJECTED,
                    TaskStatus.ARCHIVED,
                    TaskStatus.FAILED,
                }
            if busy:
                queued = assistance.with_queue_reason("ASSISTANCE_CONVERSATION_BUSY")
                return (
                    assistance
                    if queued is assistance
                    else self._application.update_assistance(
                        queued,
                        expected_revision=assistance.revision,
                    )
                )
            return self._start_assistance(assistance)

    def get_assistance(self, request: BaseModel) -> RealtimeAssistance:
        validated = cast(RealtimeAssistanceGetInput, request)
        return self._reconcile_assistance(
            self._application.get_assistance(
                validated.session_id,
                validated.request_id,
            )
        )

    def cancel_assistance(self, request: BaseModel) -> RealtimeAssistance:
        validated = cast(RealtimeAssistanceCancelInput, request)
        with self._assistance_lock:
            current = self._reconcile_assistance(
                self._application.get_assistance(
                    validated.session_id,
                    validated.request_id,
                )
            )
            if current.status in {
                RealtimeAssistanceStatus.COMPLETED,
                RealtimeAssistanceStatus.FAILED,
                RealtimeAssistanceStatus.CANCELLED,
            }:
                return current
            if current.revision != validated.expected_revision:
                raise VersionConflictError("realtime assistance revision changed")
            release_task = current.turn_id is None
            if current.turn_id is not None:
                with self._unit_of_work_factory() as unit_of_work:
                    turn = unit_of_work.assistant.get_turn(current.turn_id)
                release_task = turn is None or turn.status in {
                    AssistantTurnStatus.CREATED,
                    AssistantTurnStatus.WAITING_FOR_TOOL,
                }
                if turn is not None and turn.status not in {
                    AssistantTurnStatus.COMPLETED,
                    AssistantTurnStatus.FAILED,
                    AssistantTurnStatus.CANCELLED,
                }:
                    self._turn_canceller(turn.id, turn.cancellation_revision)
            if release_task and current.task_id is not None:
                self._release_failed_assistance_task(current.task_id)
            return self._application.cancel_assistance(
                current.session_id,
                current.request_id,
                expected_revision=current.revision,
            )

    def _start_assistance(
        self,
        assistance: RealtimeAssistance,
    ) -> RealtimeAssistance:
        context = None
        turn = None
        try:
            selection = self._selection_provider()
            context = self._task_factory(
                TaskCreate(
                    conversation_id=assistance.conversation_id,
                    user_request=_assistance_user_request(assistance),
                    operation_mode=OperationMode.ANSWER,
                    execution_target=ExecutionTarget.LOCAL,
                    idempotency_key=(
                        f"realtime-assistance:{assistance.session_id}:{assistance.request_id}"
                    ),
                )
            )
            turn = self._turn_factory(
                AssistantTurnCreateInput(
                    task_id=context.task.id,
                    model_selection=ModelSelectionSnapshotInput(
                        mode=selection.mode,
                        model_id=selection.model_id,
                        revision=selection.revision,
                    ),
                    idempotency_key=(
                        f"realtime-assistance:{assistance.session_id}:{assistance.request_id}:turn"
                    ),
                )
            )
            linked = assistance.start(task_id=context.task.id, turn_id=turn.id)
            saved = self._application.update_assistance(
                linked,
                expected_revision=assistance.revision,
            )
            self._turn_starter(turn.id)
            return saved
        except Exception as error:
            if turn is not None and turn.status not in {
                AssistantTurnStatus.COMPLETED,
                AssistantTurnStatus.FAILED,
                AssistantTurnStatus.CANCELLED,
            }:
                with suppress(Exception):
                    self._turn_canceller(turn.id, turn.cancellation_revision)
            if context is not None:
                self._release_failed_assistance_task(context.task.id)
            code = str(getattr(error, "error_code", "ASSISTANCE_START_FAILED"))
            latest = self._application.get_assistance(
                assistance.session_id,
                assistance.request_id,
            )
            if latest.status in {
                RealtimeAssistanceStatus.QUEUED,
                RealtimeAssistanceStatus.RUNNING,
                RealtimeAssistanceStatus.AWAITING_APPROVAL,
            }:
                failed = latest.fail(code[:128] or "ASSISTANCE_START_FAILED")
                return self._application.update_assistance(
                    failed,
                    expected_revision=latest.revision,
                )
            return latest

    def _release_failed_assistance_task(self, task_id: UUID) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            task = unit_of_work.state.get_task(task_id)
            if task is None:
                return
            if task.status not in {
                TaskStatus.READY,
                TaskStatus.ACCEPTED,
                TaskStatus.REJECTED,
                TaskStatus.ARCHIVED,
                TaskStatus.FAILED,
            }:
                if task.status is TaskStatus.CREATED:
                    task.transition_to(TaskStatus.RESOLVING_SCOPE)
                task.transition_to(TaskStatus.FAILED)
                unit_of_work.state.save_task(task)
            conversation = unit_of_work.state.get_conversation(task.conversation_id)
            if conversation is not None:
                changed = False
                if conversation.active_task_id == task.id:
                    conversation.active_task_id = None
                    changed = True
                if (
                    task.project_id is None
                    and conversation.active_draft_version_id == task.target_version_id
                ):
                    conversation.active_draft_version_id = None
                    changed = True
                if changed:
                    unit_of_work.state.save_conversation(conversation)
            unit_of_work.commit()

    def _reconcile_assistance(
        self,
        assistance: RealtimeAssistance,
    ) -> RealtimeAssistance:
        if assistance.turn_id is None or assistance.status in {
            RealtimeAssistanceStatus.QUEUED,
            RealtimeAssistanceStatus.COMPLETED,
            RealtimeAssistanceStatus.FAILED,
            RealtimeAssistanceStatus.CANCELLED,
        }:
            return assistance
        with self._unit_of_work_factory() as unit_of_work:
            turn = unit_of_work.assistant.get_turn(assistance.turn_id)
            message = unit_of_work.assistant.message_for_turn(
                assistance.turn_id,
                MessageRole.ASSISTANT,
            )
        if turn is None:
            updated = assistance.fail("ASSISTANCE_TURN_UNAVAILABLE")
        elif turn.status is AssistantTurnStatus.WAITING_FOR_TOOL:
            updated = (
                assistance
                if assistance.status is RealtimeAssistanceStatus.AWAITING_APPROVAL
                else assistance.await_approval()
            )
        elif turn.status in {
            AssistantTurnStatus.CREATED,
            AssistantTurnStatus.RUNNING,
        }:
            updated = (
                assistance.resume()
                if assistance.status is RealtimeAssistanceStatus.AWAITING_APPROVAL
                else assistance
            )
        elif turn.status is AssistantTurnStatus.COMPLETED and message is not None:
            updated = assistance.complete(
                message_id=message.id,
                spoken_summary=_spoken_summary(message.content),
                display_markdown=message.content,
                citations=_citations(message.content),
                freshness="completed_at_request_time",
            )
        elif turn.status is AssistantTurnStatus.CANCELLED:
            updated = assistance.cancel()
        elif turn.status is AssistantTurnStatus.FAILED:
            updated = assistance.fail(turn.error_code or "ASSISTANCE_TURN_FAILED")
        else:
            updated = assistance
        if updated is assistance:
            return assistance
        return self._application.update_assistance(
            updated,
            expected_revision=assistance.revision,
        )

    def start(self, request: BaseModel):
        validated = cast(RealtimeSessionStartInput, request)
        if validated.conversation_id is None and self._scratch_conversation_factory is not None:
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
                    linked = validated.model_copy(update={"conversation_id": conversation.id})
                    started = self._application.start_in_unit_of_work(
                        linked,
                        unit_of_work,
                    )
                    unit_of_work.commit()
                    return started
            except BaseException:
                if conversation is not None and self._scratch_conversation_cleanup is not None:
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
        with self._unit_of_work_factory() as unit_of_work:
            assistance = unit_of_work.realtime.nonterminal_assistance_for_session(
                validated.session_id
            )
        if assistance is not None:
            current = self._reconcile_assistance(assistance)
            if current.status in {
                RealtimeAssistanceStatus.QUEUED,
                RealtimeAssistanceStatus.RUNNING,
                RealtimeAssistanceStatus.AWAITING_APPROVAL,
            }:
                self.cancel_assistance(
                    RealtimeAssistanceCancelInput(
                        session_id=current.session_id,
                        request_id=current.request_id,
                        expected_revision=current.revision,
                    )
                )
        return self._application.request_stop(
            validated.session_id, expected_revision=validated.expected_revision
        )

    def save_memory(self, request: BaseModel):
        return self._application.save_memory(cast(GameMemorySaveInput, request))

    def create_digest(self, request: BaseModel):
        return self._application.create_digest(cast(CompanionDigestCreateInput, request))

    def get_digest(self, request: BaseModel):
        validated = cast(CompanionDigestGetInput, request)
        return self._application.get_digest(validated.digest_id)

    def list_digests(self, request: BaseModel):
        validated = cast(CompanionDigestListInput, request)
        return {
            "items": self._application.list_digests(
                session_id=validated.session_id,
                limit=validated.limit,
            )
        }

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
        return self._application.append_transcript(cast(RealtimeTranscriptAppendInput, request))

    def list_transcript(self, request: BaseModel):
        validated = cast(RealtimeTranscriptListInput, request)
        return {
            "items": self._application.list_transcript(
                validated.conversation_id, limit=validated.limit
            )
        }

    def persona_snapshot(self, request: BaseModel) -> RealtimePersonaSnapshotModel:
        validated = cast(RealtimePersonaSnapshotInput, request)
        snapshot = project_realtime_persona_snapshot(
            authority=self._persona_authority,
            locale=validated.locale,
            activity_profile=validated.activity_profile,
            interaction_intensity=validated.interaction_intensity,
            current_goal=validated.current_goal,
            subject_title=validated.subject_title,
            recent_progress=validated.recent_progress,
        )
        return RealtimePersonaSnapshotModel.model_validate(snapshot, from_attributes=True)


_MARKDOWN_LINK = re.compile(r"\[([^\]\r\n]{1,300})\]\((https?://[^\s)]+)\)")
_MARKDOWN_MARKER = re.compile(r"(?m)^\s{0,3}(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s*)")
_INLINE_MARKDOWN = re.compile(r"[*_`~]+")


def _assistance_user_request(assistance: RealtimeAssistance) -> str:
    context = []
    if assistance.application_title is not None:
        context.append(f"- Application: {assistance.application_title}")
    context.extend(f"- {fact}" for fact in assistance.observed_facts)
    context.append(
        "- Public web research: "
        + ("enabled for this request" if assistance.allow_network else "disabled")
    )
    return f"{assistance.question}\n\nRealtime context (public and user-visible):\n" + "\n".join(
        context
    )


def _spoken_summary(markdown: str) -> str:
    paragraphs = tuple(
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", markdown.strip())
        if paragraph.strip()
    )
    source = paragraphs[0] if paragraphs else markdown
    source = _MARKDOWN_LINK.sub(r"\1", source)
    source = _MARKDOWN_MARKER.sub("", source)
    source = _INLINE_MARKDOWN.sub("", source)
    normalized = " ".join(source.split())
    if not normalized:
        return "The complete Assistance result is ready in the main chat."
    if len(normalized) <= 320:
        return normalized
    return normalized[:317].rstrip() + "..."


def _citations(markdown: str) -> tuple[RealtimeAssistanceCitation, ...]:
    seen: set[str] = set()
    citations = []
    for title, url in _MARKDOWN_LINK.findall(markdown):
        if url in seen:
            continue
        seen.add(url)
        citations.append(
            RealtimeAssistanceCitation.create(
                title=" ".join(title.split()),
                url=url,
            )
        )
        if len(citations) == 32:
            break
    return tuple(citations)


__all__ = ["RealtimeService"]
