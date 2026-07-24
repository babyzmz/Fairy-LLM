from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fairy_core.application.runtime_contracts import (
    PreviewActivateRequest,
    PreviewActivationOutcome,
    PreviewActivationResult,
    PreviewStartRequest,
    PreviewStopRequest,
)
from fairy_core.domain.execution import PreviewSession, PreviewStatus
from fairy_core.domain.models import TaskStatus
from fairy_core.runtime.models import RuntimeExecutorError

PREVIEW_POOL_CAPACITY = 3
PREVIEW_IDLE_TIMEOUT = timedelta(minutes=10)
EVICTABLE_TASK_STATUSES = frozenset({TaskStatus.READY, TaskStatus.ACCEPTED})
ACTIVATABLE_TASK_STATUSES = frozenset(
    {
        TaskStatus.EXECUTING,
        TaskStatus.PREVIEWING,
        TaskStatus.REVIEWING,
        TaskStatus.READY,
        TaskStatus.ACCEPTED,
    }
)


@dataclass(frozen=True, slots=True)
class _PoolVictim:
    preview: PreviewSession
    workspace_revision: int


class RuntimePoolOperations:
    def activate_preview(self, request: PreviewActivateRequest) -> PreviewActivationResult:
        with self._operation_lock:
            return self._activate_preview(request)

    def _activate_preview(self, request: PreviewActivateRequest) -> PreviewActivationResult:
        now = datetime.now(UTC)
        with self._transaction() as (unit_of_work, _commands):
            state = unit_of_work.state
            task = self._require_bound_task(
                state,
                task_id=request.task_id,
                workspace_id=request.workspace_id,
                version_id=request.version_id,
                expected_workspace_revision=request.expected_workspace_revision,
            )
            active = tuple(state.active_previews())
            matching = next(
                (
                    preview
                    for preview in reversed(active)
                    if preview.task_id == task.id
                    and preview.workspace_id == request.workspace_id
                    and preview.version_id == request.version_id
                ),
                None,
            )
            if matching is not None:
                context = self._context_for_preview(state, matching)
                adapter = self._runtime_adapter(context.runtime)
                if matching.status is PreviewStatus.READY:
                    touched = state.touch_preview_access(matching.id, accessed_at=now)
                    context = self._context_for_preview(state, touched)
                    unit_of_work.commit()
                    return PreviewActivationResult(
                        outcome=PreviewActivationOutcome.READY,
                        context=context,
                        adapter=adapter,
                        capacity=PREVIEW_POOL_CAPACITY,
                        active_count=len(active),
                    )
                if matching.status is PreviewStatus.STARTING:
                    return PreviewActivationResult(
                        outcome=PreviewActivationOutcome.STARTING,
                        context=context,
                        adapter=adapter,
                        capacity=PREVIEW_POOL_CAPACITY,
                        active_count=len(active),
                    )
                return PreviewActivationResult(
                    outcome=PreviewActivationOutcome.WAITING_FOR_SLOT,
                    context=context,
                    adapter=adapter,
                    capacity=PREVIEW_POOL_CAPACITY,
                    active_count=len(active),
                    public_reason="The selected Preview is stopping.",
                )

            scope = self._scope_resolver(state, task)
            try:
                template = self._select_template(scope)
            except RuntimeExecutorError as error:
                if error.error_code != "CAPABILITY_NOT_AVAILABLE":
                    raise
                return PreviewActivationResult(
                    outcome=PreviewActivationOutcome.NOT_RUNNABLE,
                    context=None,
                    adapter=None,
                    capacity=PREVIEW_POOL_CAPACITY,
                    active_count=len(active),
                    public_reason=str(error),
                )
            self._validate_template_dependencies(
                unit_of_work,
                task.id,
                scope,
                template,
            )
            self._require_executor_health(self._health_for(template.kind))
            previews_for_task = tuple(
                preview
                for preview in state.previews_for_conversation(task.conversation_id)
                if preview.task_id == task.id and preview.version_id == request.version_id
            )
            latest = previews_for_task[-1] if previews_for_task else None
            if latest is not None and latest.status is PreviewStatus.FAILED:
                return PreviewActivationResult(
                    outcome=PreviewActivationOutcome.FAILED,
                    context=self._context_for_preview(state, latest),
                    adapter=template.adapter.value,
                    capacity=PREVIEW_POOL_CAPACITY,
                    active_count=len(active),
                    public_reason="Preview startup previously failed. Retry it explicitly.",
                )

            candidates: list[_PoolVictim] = []
            for preview in active:
                if (
                    preview.conversation_id == task.conversation_id
                    or preview.status is not PreviewStatus.READY
                ):
                    continue
                candidate_task = state.get_task(preview.task_id)
                workspace = state.get_workspace(preview.workspace_id)
                if (
                    candidate_task is None
                    or candidate_task.status not in EVICTABLE_TASK_STATUSES
                    or workspace is None
                ):
                    continue
                candidates.append(
                    _PoolVictim(
                        preview=preview,
                        workspace_revision=workspace.revision,
                    )
                )
            candidates.sort(key=lambda item: (item.preview.last_accessed_at, str(item.preview.id)))
            victim = candidates[0] if len(active) >= PREVIEW_POOL_CAPACITY and candidates else None
            if len(active) >= PREVIEW_POOL_CAPACITY and victim is None:
                return PreviewActivationResult(
                    outcome=PreviewActivationOutcome.WAITING_FOR_SLOT,
                    context=None,
                    adapter=template.adapter.value,
                    capacity=PREVIEW_POOL_CAPACITY,
                    active_count=len(active),
                    public_reason="All Preview slots are currently protected.",
                )
            if task.status not in ACTIVATABLE_TASK_STATUSES:
                return PreviewActivationResult(
                    outcome=PreviewActivationOutcome.WAITING_FOR_SLOT,
                    context=None,
                    adapter=template.adapter.value,
                    capacity=PREVIEW_POOL_CAPACITY,
                    active_count=len(active),
                    public_reason="The selected Task is not ready to start its Preview.",
                )
            start_key = (
                latest.idempotency_key
                if latest is not None and latest.status is PreviewStatus.INTERRUPTED
                else f"{request.idempotency_key}:preview:{len(previews_for_task)}"
            )

        evicted_preview_id = None
        if victim is not None:
            self._stop_preview(
                PreviewStopRequest(
                    preview_id=victim.preview.id,
                    task_id=victim.preview.task_id,
                    workspace_id=victim.preview.workspace_id,
                    version_id=victim.preview.version_id,
                    expected_workspace_revision=victim.workspace_revision,
                    idempotency_key=(
                        f"preview-pool:evict:{victim.preview.id}:{victim.preview.revision}"
                    ),
                )
            )
            evicted_preview_id = victim.preview.id

        context = self._start_preview(
            PreviewStartRequest(
                task_id=request.task_id,
                workspace_id=request.workspace_id,
                version_id=request.version_id,
                expected_workspace_revision=request.expected_workspace_revision,
                idempotency_key=start_key,
            )
        )
        with self._transaction() as (unit_of_work, _commands):
            active_count = len(unit_of_work.state.active_previews())
        return PreviewActivationResult(
            outcome=(
                PreviewActivationOutcome.READY
                if context.preview.status is PreviewStatus.READY
                else PreviewActivationOutcome.STARTING
            ),
            context=context,
            adapter=template.adapter.value,
            capacity=PREVIEW_POOL_CAPACITY,
            active_count=active_count,
            evicted_preview_id=evicted_preview_id,
        )

    def reap_idle_previews(
        self,
        *,
        now: datetime | None = None,
    ) -> tuple[UUID, ...]:
        with self._operation_lock:
            return self._reap_idle_previews(now=now or datetime.now(UTC))

    def _reap_idle_previews(self, *, now: datetime) -> tuple[UUID, ...]:
        cutoff = now - PREVIEW_IDLE_TIMEOUT
        with self._transaction() as (unit_of_work, _commands):
            state = unit_of_work.state
            candidates: list[_PoolVictim] = []
            for preview in state.active_previews():
                if preview.status is not PreviewStatus.READY or preview.last_accessed_at > cutoff:
                    continue
                task = state.get_task(preview.task_id)
                workspace = state.get_workspace(preview.workspace_id)
                if task is None or task.status not in EVICTABLE_TASK_STATUSES or workspace is None:
                    continue
                candidates.append(
                    _PoolVictim(
                        preview=preview,
                        workspace_revision=workspace.revision,
                    )
                )
        stopped: list[UUID] = []
        for candidate in candidates:
            self._stop_preview(
                PreviewStopRequest(
                    preview_id=candidate.preview.id,
                    task_id=candidate.preview.task_id,
                    workspace_id=candidate.preview.workspace_id,
                    version_id=candidate.preview.version_id,
                    expected_workspace_revision=candidate.workspace_revision,
                    idempotency_key=(
                        f"preview-pool:idle:{candidate.preview.id}:{candidate.preview.revision}"
                    ),
                )
            )
            stopped.append(candidate.preview.id)
        return tuple(stopped)
