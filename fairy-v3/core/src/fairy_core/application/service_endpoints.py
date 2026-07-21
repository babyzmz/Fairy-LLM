from __future__ import annotations

from typing import Any, cast

from pydantic import BaseModel

from fairy_core.application.runtime import RuntimeApplication
from fairy_core.commanding import EventVisibility
from fairy_core.contracts.approvals import ApprovalListInput
from fairy_core.contracts.media import MediaJobListInput
from fairy_core.contracts.methods import EventListInput, EventSubscribeInput
from fairy_core.contracts.models import (
    ArtifactIdInput,
    ArtifactListInput,
    AssistantTurnRetryInput,
    AssistantTurnRunInput,
    AssistantTurnStartInput,
    ChangesetProposal,
    DocumentDeleteInput,
    DocumentIdInput,
    DocumentImportInput,
    DocumentListInput,
    DocumentSearchInput,
    ExecutionSettingsUpdateInput,
    MemoryClaimGetInput,
    MemoryClaimPromoteInput,
    MemoryClaimQuery,
    MemoryClaimResolveInput,
    MemoryClaimSupersedeInput,
    MemoryForgetInput,
    MemoryObservationQuery,
    MemoryObserveInput,
    MemoryProjectionHealthInput,
    MemoryProposalActionInput,
    MemoryProposalListInput,
    MemorySearchInput,
    MemorySettingsUpdateInput,
    MemorySnapshotGetInput,
    MessageListInput,
    TaskCreate,
    TaskIdInput,
    VersionAcceptInput,
    VersionIdInput,
    VersionListInput,
)
from fairy_core.contracts.workspaces import WorkspaceFileMutateInput
from fairy_core.documents.application import DocumentApplication
from fairy_core.domain.errors import MemoryScopeViolationError
from fairy_core.media.service import media_job_model
from fairy_core.runtime.models import RuntimeExecutorError


class CoreServiceEndpointsMixin:
    def _run_assistant_turn(self, request: BaseModel) -> Any:
        self._extension_service.refresh_registry()
        turn_id = cast(AssistantTurnRunInput, request).turn_id
        return self._assistant_scheduler.run(turn_id)

    def _start_assistant_turn(self, request: BaseModel) -> Any:
        self._extension_service.refresh_registry()
        turn_id = cast(AssistantTurnStartInput, request).turn_id
        return self._assistant_scheduler.start(turn_id)

    def _retry_assistant_turn(self, request: BaseModel) -> Any:
        validated = cast(AssistantTurnRetryInput, request)
        return self._assistant_ledger.retry_turn(
            turn_id=validated.turn_id,
            idempotency_key=validated.idempotency_key,
        )

    def _list_messages(self, request: BaseModel) -> Any:
        validated = cast(MessageListInput, request)
        return self._assistant_ledger.list_messages(
            conversation_id=validated.conversation_id,
            limit=validated.limit,
            cursor=validated.cursor,
        )

    def _import_document(self, request: BaseModel) -> Any:
        return self._documents().import_document(cast(DocumentImportInput, request))

    def _get_document(self, request: BaseModel) -> Any:
        return self._documents().get_document(cast(DocumentIdInput, request))

    def _list_documents(self, request: BaseModel) -> Any:
        return self._documents().list_documents(cast(DocumentListInput, request))

    def _search_documents(self, request: BaseModel) -> Any:
        return self._documents().search_documents(cast(DocumentSearchInput, request))

    def _delete_document(self, request: BaseModel) -> Any:
        return self._documents().delete_document(cast(DocumentDeleteInput, request))

    def _documents(self) -> DocumentApplication:
        if self._document_application is None:
            raise RuntimeError("Managed document capability is unavailable")
        return self._document_application

    def _observe_memory(self, request: BaseModel) -> Any:
        return self._memory_application.observe(cast(MemoryObserveInput, request))

    def _list_memory_observations(self, request: BaseModel) -> dict[str, Any]:
        return {
            "items": self._memory_application.list_observations(
                cast(MemoryObservationQuery, request)
            )
        }

    def _promote_memory_claim(self, request: BaseModel) -> Any:
        return self._memory_application.promote_claim(cast(MemoryClaimPromoteInput, request))

    def _get_memory_claim(self, request: BaseModel) -> Any:
        return self._memory_application.get_claim(cast(MemoryClaimGetInput, request))

    def _list_memory_claims(self, request: BaseModel) -> dict[str, Any]:
        return {"items": self._memory_application.list_claims(cast(MemoryClaimQuery, request))}

    def _supersede_memory_claim(self, request: BaseModel) -> Any:
        return self._memory_application.supersede_claim(cast(MemoryClaimSupersedeInput, request))

    def _resolve_memory_conflict(self, request: BaseModel) -> Any:
        return self._memory_application.resolve_conflict(cast(MemoryClaimResolveInput, request))

    def _forget_memory(self, request: BaseModel) -> Any:
        return self._memory_application.forget(cast(MemoryForgetInput, request))

    def _search_memory(self, request: BaseModel) -> dict[str, Any]:
        validated = cast(MemorySearchInput, request)
        with self._unit_of_work_factory() as unit_of_work:
            task = unit_of_work.state.get_task(validated.task_id)
            if task is None:
                raise KeyError(f"task not found: {validated.task_id}")
            scope = self._application.scope_for_task(unit_of_work.state, task)
            hits = unit_of_work.memory_search.search(
                scope=scope,
                query=validated.query,
                generation=1,
                limit=validated.limit,
            )
        return {"items": hits}

    def _get_memory_settings(self, _request: BaseModel) -> Any:
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.memory_settings.get()

    def _update_memory_settings(self, request: BaseModel) -> Any:
        validated = cast(MemorySettingsUpdateInput, request)
        with self._unit_of_work_factory() as unit_of_work:
            changed = unit_of_work.memory_settings.update(
                enabled=validated.enabled,
                retention_days=validated.retention_days,
                export_to_obsidian=validated.export_to_obsidian,
                sync_normalized_content=validated.sync_normalized_content,
                expected_revision=validated.expected_revision,
                idempotency_key=validated.idempotency_key,
            )
            unit_of_work.commit()
        self._memory_retention.run_if_due(force=True)
        return changed

    def _get_memory_snapshot(self, request: BaseModel) -> Any:
        validated = cast(MemorySnapshotGetInput, request)
        with self._unit_of_work_factory() as unit_of_work:
            task = unit_of_work.state.get_task(validated.task_id)
            if task is None:
                raise KeyError(f"task not found: {validated.task_id}")
            if task.memory_snapshot_id != validated.snapshot_id:
                raise MemoryScopeViolationError("Snapshot is not bound to the requested Task")
            snapshot = unit_of_work.snapshots.get(
                validated.snapshot_id,
                task_id=validated.task_id,
            )
            if snapshot is None:
                raise MemoryScopeViolationError("Task-bound Snapshot is unavailable")
            return snapshot

    def _memory_projection_health(self, request: BaseModel) -> dict[str, Any]:
        validated = cast(MemoryProjectionHealthInput, request)
        with self._unit_of_work_factory() as unit_of_work:
            task = unit_of_work.state.get_task(validated.task_id)
            if task is None:
                raise KeyError(f"task not found: {validated.task_id}")
            self._application.scope_for_task(unit_of_work.state, task)
            source_watermark_cursor = unit_of_work.commands.current_cursor()
            health = unit_of_work.memory_search.health(
                generation=1,
                source_watermark_cursor=source_watermark_cursor,
            )
        return {
            "generation": health.generation,
            "state": health.state,
            "source_watermark_cursor": health.source_watermark_cursor,
            "projected_watermark_cursor": health.projected_watermark_cursor,
            "lag": max(0, health.source_watermark_cursor - health.projected_watermark_cursor),
            "last_error_code": health.last_error_code,
            "updated_at": health.updated_at,
        }

    def _list_memory_proposals(self, request: BaseModel) -> dict[str, Any]:
        return {
            "items": self._memory_application.list_proposals(cast(MemoryProposalListInput, request))
        }

    def _accept_memory_proposal(self, request: BaseModel) -> Any:
        return self._memory_application.accept_proposal(cast(MemoryProposalActionInput, request))

    def _reject_memory_proposal(self, request: BaseModel) -> Any:
        return self._memory_application.reject_proposal(cast(MemoryProposalActionInput, request))

    def _create_task(self, request: BaseModel) -> Any:
        return self._application.create_task(cast(TaskCreate, request))

    def _get_task(self, request: BaseModel) -> Any:
        return self._application.get_task(cast(TaskIdInput, request).task_id)

    def _review_task(self, request: BaseModel) -> Any:
        task_id = cast(TaskIdInput, request).task_id
        if self._project_execution_application is not None:
            self._project_execution_application.run_review_suite(task_id)
        if self._runtime_review_application is not None:
            with self._unit_of_work_factory() as unit_of_work:
                preview = unit_of_work.state.preview_for_task(task_id, include_terminal=True)
            if preview is not None and preview.status.value == "ready":
                self._runtime_review_application.review_task(task_id)
        return self._application.review_task(task_id)

    def _propose_changeset(self, request: BaseModel) -> Any:
        return self._application.propose_changeset(cast(ChangesetProposal, request))

    def _mutate_workspace_files(self, request: BaseModel) -> Any:
        context = self._application.workspace_mutations.mutate(
            cast(WorkspaceFileMutateInput, request)
        )
        return {
            "workspace": context.workspace,
            "conversation": context.conversation,
            "task": context.task,
            "target_version": context.target_version,
            "changeset": context.changeset,
            "approval": context.approval,
        }

    def _get_version(self, request: BaseModel) -> Any:
        return self._application.get_version(cast(VersionIdInput, request).version_id)

    def _list_versions(self, request: BaseModel) -> Any:
        validated = cast(VersionListInput, request)
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.state.list_versions(
                workspace_id=validated.workspace_id,
                project_id=validated.project_id,
                conversation_id=validated.conversation_id,
                task_id=validated.task_id,
                limit=validated.limit,
                cursor=validated.cursor,
            )

    def _list_approvals(self, request: BaseModel) -> Any:
        validated = cast(ApprovalListInput, request)
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.state.list_approvals(
                project_id=validated.project_id,
                conversation_id=validated.conversation_id,
                task_id=validated.task_id,
                limit=validated.limit,
                cursor=validated.cursor,
            )

    def _list_artifacts(self, request: BaseModel) -> dict[str, Any]:
        validated = cast(ArtifactListInput, request)
        with self._unit_of_work_factory() as unit_of_work:
            task = unit_of_work.state.get_task(validated.task_id)
            if task is None:
                raise KeyError(f"task not found: {validated.task_id}")
            items = unit_of_work.state.artifacts_for_task(task.id)
        return {"items": items}

    def _list_media_jobs(self, request: BaseModel) -> dict[str, Any]:
        validated = cast(MediaJobListInput, request)
        with self._unit_of_work_factory() as unit_of_work:
            task = unit_of_work.state.get_task(validated.task_id)
            if task is None:
                raise KeyError(f"task not found: {validated.task_id}")
            items = [media_job_model(job) for job in unit_of_work.state.list_media_jobs(task.id)]
        return {"items": items}

    def _read_artifact(self, request: BaseModel) -> Any:
        validated = cast(ArtifactIdInput, request)
        with self._unit_of_work_factory() as unit_of_work:
            artifact = unit_of_work.state.get_artifact(validated.artifact_id)
        if artifact is None:
            raise KeyError(f"Artifact not found: {validated.artifact_id}")
        return artifact

    def _runtime(self) -> RuntimeApplication:
        if self._runtime_application is None:
            raise RuntimeExecutorError(
                "Runtime execution is unavailable",
                error_code="SANDBOX_UNAVAILABLE",
            )
        return self._runtime_application

    def _accept_version(self, request: BaseModel) -> Any:
        validated = cast(VersionAcceptInput, request)
        return self._application.accept_task_version(
            task_id=validated.task_id,
            expected_project_revision=validated.expected_project_revision,
            user_confirmed=validated.user_confirmed,
        )

    def _discard_version(self, request: BaseModel) -> Any:
        return self._application.discard_task_version(cast(TaskIdInput, request).task_id)

    def _get_capabilities(self, _request: BaseModel) -> dict[str, Any]:
        self._extension_service.refresh_registry()
        with self._unit_of_work_factory() as unit_of_work:
            policy = self._execution_policy.resolve(
                unit_of_work.execution_settings,
                execution_target=self._default_execution_target,
            )
        operations = self._registry.capability_manifest(
            profile=policy.profile,
            sandbox_healthy=policy.sandbox_healthy,
            overrides=dict(policy.capability_overrides),
        )
        return {
            "profile": policy.profile,
            "operations": operations,
            "sandbox_healthy": policy.sandbox_healthy,
            "command_metadata": self._registry.frontend_metadata(),
            "slash_commands": self._registry.slash_command_metadata(operations),
            "schema_version": 3,
        }

    def _get_permissions(self, _request: BaseModel) -> Any:
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.execution_settings.get()

    def _update_permissions(self, request: BaseModel) -> Any:
        self._extension_service.refresh_registry()
        validated = cast(ExecutionSettingsUpdateInput, request)
        unknown = sorted(
            name for name in validated.capability_overrides if self._registry.get(name) is None
        )
        if unknown:
            raise ValueError(f"unknown capability override: {', '.join(unknown)}")
        with self._unit_of_work_factory() as unit_of_work:
            changed = unit_of_work.execution_settings.update(
                profile=validated.profile,
                capability_overrides=validated.capability_overrides,
                expected_revision=validated.expected_revision,
                idempotency_key=validated.idempotency_key,
            )
            unit_of_work.commit()
        return changed

    def _subscribe_events(self, request: BaseModel) -> dict[str, Any]:
        validated = cast(EventSubscribeInput, request)
        return self._event_page(cursor=validated.cursor, limit=500)

    def _list_events(self, request: BaseModel) -> dict[str, Any]:
        validated = cast(EventListInput, request)
        return self._event_page(cursor=validated.cursor, limit=validated.limit)

    def _event_page(self, *, cursor: int, limit: int) -> dict[str, Any]:
        with self._unit_of_work_factory() as unit_of_work:
            events = unit_of_work.commands.events_after(
                cursor=cursor,
                limit=limit,
                allowed_visibilities={EventVisibility.USER, EventVisibility.DEVELOPER},
            )
        return {"items": events, "next_cursor": events[-1].cursor if events else cursor}

    def _event_stream_state(self, _request: BaseModel) -> Any:
        with self._unit_of_work_factory() as unit_of_work:
            state = unit_of_work.commands.stream_state(
                allowed_visibilities={EventVisibility.USER, EventVisibility.DEVELOPER}
            )
            unit_of_work.commit()
        return state
