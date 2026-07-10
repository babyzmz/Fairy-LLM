from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from fairy_core.application.errors import ApprovalRequiredError
from fairy_core.commanding import CommandLedger, CommandRun, CommandStatus
from fairy_core.commanding.bus import CommandBus, CommandRequest
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import ToolRegistry
from fairy_core.commanding.types import PermissionProfile
from fairy_core.contracts.models import ChangesetProposal, TaskCreate
from fairy_core.domain.errors import IdempotencyConflictError, InvalidTransitionError
from fairy_core.domain.execution import (
    Approval,
    ApprovalDecision,
    Changeset,
    ChangesetStatus,
    Checkpoint,
)
from fairy_core.domain.ids import new_id
from fairy_core.domain.models import (
    Conversation,
    OperationMode,
    Project,
    ProjectResidency,
    ScopeContract,
    Task,
    TaskStatus,
    Version,
    VersionVisibility,
    WorkspaceType,
)
from fairy_core.memory.retrieval_ports import MemorySnapshotBuilder
from fairy_core.memory.snapshot_builder import DeterministicMemorySnapshotBuilder
from fairy_core.persistence.unit_of_work import CoreUnitOfWork, CoreUnitOfWorkFactory
from fairy_core.storage import StateStore
from fairy_core.workspace.ports import WorkspaceProvisioner


@dataclass(frozen=True, slots=True)
class ProjectContext:
    project: Project
    initial_version: Version


@dataclass(frozen=True, slots=True)
class TaskContext:
    task: Task
    target_version: Version | None
    scope: ScopeContract


@dataclass(frozen=True, slots=True)
class PendingChangeset:
    changeset: Changeset
    approval: Approval


@dataclass(frozen=True, slots=True)
class _TaskIntent:
    task: Task
    conversation: Conversation
    target_version: Version | None
    running: CommandRun


SnapshotBuilderFactory = Callable[[CoreUnitOfWork], MemorySnapshotBuilder]


class CoreApplication:
    def __init__(
        self,
        *,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        workspace_provisioner: WorkspaceProvisioner,
        registry: ToolRegistry,
        policy: PolicyEngine,
        snapshot_builder_factory: SnapshotBuilderFactory | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._workspaces = workspace_provisioner
        self._registry = registry
        self._policy = policy
        self._snapshot_builder_factory = snapshot_builder_factory or self._default_snapshot_builder

    def create_project(
        self,
        *,
        name: str,
        residency: ProjectResidency,
        source: Path | None = None,
    ) -> ProjectContext:
        project = Project.create(name=name, residency=residency)
        version_id = new_id()
        setup_conversation = Conversation.create(
            project_id=project.id,
            workspace_type=WorkspaceType.PROJECT_CHAT,
            base_version_id=None,
        )
        setup_task = Task.create(
            project_id=project.id,
            conversation_id=setup_conversation.id,
            user_request="Import project" if source is not None else "Create project",
            operation_mode=OperationMode.CREATE_NEW_VERSION,
            base_version_id=None,
            execution_target="local",
        )
        setup_task.bind_target_version(version_id)
        setup_task.transition_to(TaskStatus.RESOLVING_SCOPE)
        root_hint = self._workspaces.version_path(project.id, version_id)
        scope = ScopeContract.create(
            workspace_type=WorkspaceType.PROJECT_CHAT,
            project_id=project.id,
            conversation_id=setup_conversation.id,
            task_id=setup_task.id,
            operation_mode=setup_task.operation_mode,
            base_version_id=None,
            target_version_id=version_id,
            project_root=root_hint,
            allowed_write_paths=(root_hint,),
            forbidden_write_paths=(),
            execution_target="local",
            network_policy="project_safe",
            memory_read_scope=(),
            memory_write_scope=("current_conversation_draft",),
        )
        tool_name = "workspace.import" if source is not None else "workspace.create_empty"
        payload: dict[str, object] = {
            "project_id": str(project.id),
            "version_id": str(version_id),
        }
        if source is not None:
            payload["source"] = str(source.resolve(strict=True))

        with self._transaction() as (unit_of_work, commands):
            unit_of_work.state.save_project(project)
            unit_of_work.state.save_conversation(setup_conversation)
            unit_of_work.state.save_task(
                setup_task,
                idempotency_key=f"project:{project.id}:initialize",
            )
            running = self._start_command(
                commands,
                tool_name=tool_name,
                scope=scope,
                payload=payload,
                idempotency_key=f"project:{project.id}:workspace",
            )
            unit_of_work.commit()

        try:
            root = self._workspaces.create_initial_version(
                project.id,
                version_id,
                source=source,
            )
        except Exception as error:
            with self._transaction() as (unit_of_work, commands):
                failed_task = self._require_task(unit_of_work.state, setup_task.id)
                commands.fail(
                    running.id,
                    error_code=str(getattr(error, "error_code", "WORKER_INTERRUPTED")),
                    lease_owner=running.lease_owner,
                    lease_fence=running.lease_fence,
                )
                failed_task.transition_to(TaskStatus.FAILED)
                unit_of_work.state.save_task(failed_task)
                unit_of_work.commit()
            raise

        with self._transaction() as (unit_of_work, commands):
            persisted_project = self._require_project(unit_of_work.state, project.id)
            persisted_task = self._require_task(unit_of_work.state, setup_task.id)
            persisted_conversation = self._require_conversation(
                unit_of_work.state,
                setup_conversation.id,
            )
            version = Version.create(
                version_id=version_id,
                project_id=persisted_project.id,
                source_conversation_id=None,
                source_task_id=None,
                parent_version_id=None,
                project_root=root,
                visibility=VersionVisibility.PROJECT_ACTIVE,
            )
            persisted_project.accept_version(version.id, expected_revision=0)
            for status in (
                TaskStatus.BUILDING_WORKSPACE,
                TaskStatus.PLANNING,
                TaskStatus.EXECUTING,
                TaskStatus.REVIEWING,
                TaskStatus.READY,
                TaskStatus.ACCEPTED,
            ):
                persisted_task.transition_to(status)
            persisted_conversation.base_version_id = version.id
            unit_of_work.state.save_version(version)
            unit_of_work.state.save_project(persisted_project)
            unit_of_work.state.save_task(persisted_task)
            unit_of_work.state.save_conversation(persisted_conversation)
            commands.complete(
                running.id,
                output={"root": str(root)},
                lease_owner=running.lease_owner,
                lease_fence=running.lease_fence,
            )
            unit_of_work.commit()
        return ProjectContext(project=persisted_project, initial_version=version)

    def create_conversation(
        self,
        *,
        project_id: UUID | None,
        workspace_type: WorkspaceType,
    ) -> Conversation:
        with self._transaction() as (unit_of_work, _commands):
            base_version_id = None
            if workspace_type is WorkspaceType.PROJECT_CHAT:
                if project_id is None:
                    raise ValueError("project_chat requires project_id")
                project = self._require_project(unit_of_work.state, project_id)
                base_version_id = project.active_version_id
            conversation = Conversation.create(
                project_id=project_id,
                workspace_type=workspace_type,
                base_version_id=base_version_id,
            )
            unit_of_work.state.save_conversation(conversation)
            unit_of_work.commit()
        return conversation

    def create_task(self, request: TaskCreate) -> TaskContext:
        idempotency_key = self._normalize_idempotency_key(request.idempotency_key)
        prepared = self._prepare_task_intent(
            request,
            idempotency_key=idempotency_key,
        )
        if isinstance(prepared, TaskContext):
            return prepared
        task = prepared.task
        conversation = prepared.conversation
        target_version = prepared.target_version
        running = prepared.running

        try:
            if target_version is not None:
                assert task.project_id is not None and task.base_version_id is not None
                root = self._workspaces.fork_version(
                    project_id=task.project_id,
                    version_id=target_version.id,
                    parent_version_id=task.base_version_id,
                )
            else:
                root = self._workspaces.create_scratch(conversation.id, task.id)
        except Exception as error:
            with self._transaction() as (unit_of_work, commands):
                failed_task = self._require_task(unit_of_work.state, task.id)
                commands.fail(
                    running.id,
                    error_code=str(getattr(error, "error_code", "WORKER_INTERRUPTED")),
                    lease_owner=running.lease_owner,
                    lease_fence=running.lease_fence,
                )
                failed_task.transition_to(TaskStatus.FAILED)
                unit_of_work.state.save_task(failed_task)
                unit_of_work.commit()
            raise

        with self._transaction() as (unit_of_work, commands):
            persisted_task = self._require_task(unit_of_work.state, task.id)
            persisted_conversation = self._require_conversation(
                unit_of_work.state,
                conversation.id,
            )
            persisted_target = (
                self._require_version(unit_of_work.state, target_version.id)
                if target_version is not None
                else None
            )
            if persisted_target is not None:
                persisted_target.project_root = root.resolve(strict=False)
                unit_of_work.state.save_version(persisted_target)
            persisted_task.transition_to(TaskStatus.BUILDING_WORKSPACE)
            persisted_task.transition_to(TaskStatus.PLANNING)
            unit_of_work.state.save_task(persisted_task)
            commands.complete(
                running.id,
                output={"root": str(root)},
                lease_owner=running.lease_owner,
                lease_fence=running.lease_fence,
            )
            unit_of_work.commit()
        return self._build_context(
            persisted_task,
            persisted_conversation,
            persisted_target,
        )

    def _prepare_task_intent(
        self,
        request: TaskCreate,
        *,
        idempotency_key: str,
    ) -> _TaskIntent | TaskContext:
        try:
            with self._transaction() as (unit_of_work, commands):
                existing = unit_of_work.state.find_task_by_idempotency_key(idempotency_key)
                if existing is not None:
                    self._validate_task_replay(existing, request)
                    return self._context_for(unit_of_work.state, existing)

                conversation = self._require_conversation(
                    unit_of_work.state,
                    request.conversation_id,
                )
                base_version_id = (
                    conversation.active_draft_version_id or conversation.base_version_id
                )
                task = Task.create(
                    project_id=conversation.project_id,
                    conversation_id=conversation.id,
                    user_request=request.user_request,
                    operation_mode=request.operation_mode,
                    base_version_id=base_version_id,
                    execution_target=request.execution_target.value,
                )
                target_version: Version | None = None
                if conversation.workspace_type is WorkspaceType.PROJECT_CHAT:
                    if conversation.project_id is None or base_version_id is None:
                        raise ValueError("project conversation has no base version")
                    parent = self._require_version(unit_of_work.state, base_version_id)
                    version_id = new_id()
                    root_hint = self._workspaces.version_path(
                        conversation.project_id,
                        version_id,
                    )
                    target_version = Version.create(
                        version_id=version_id,
                        project_id=conversation.project_id,
                        source_conversation_id=conversation.id,
                        source_task_id=task.id,
                        parent_version_id=parent.id,
                        project_root=root_hint,
                        visibility=VersionVisibility.CHAT_DRAFT,
                    )
                    task.bind_target_version(target_version.id)
                    conversation.active_draft_version_id = target_version.id

                task.transition_to(TaskStatus.RESOLVING_SCOPE)
                conversation.active_task_id = task.id
                if target_version is not None:
                    unit_of_work.state.save_version(target_version)
                    tool_name = "workspace.fork"
                    payload: dict[str, object] = {
                        "project_id": str(task.project_id),
                        "parent_version_id": str(task.base_version_id),
                        "version_id": str(target_version.id),
                    }
                else:
                    tool_name = "workspace.create_scratch"
                    payload = {
                        "conversation_id": str(conversation.id),
                        "task_id": str(task.id),
                    }
                unit_of_work.state.save_task(
                    task,
                    idempotency_key=idempotency_key,
                )
                unit_of_work.state.save_conversation(conversation)
                unbound_context = self._build_context(task, conversation, target_version)
                source_watermark_cursor = unit_of_work.commands.current_cursor()
                snapshot_running = self._start_command(
                    commands,
                    tool_name="memory.snapshot.build",
                    scope=unbound_context.scope,
                    payload={
                        "task_id": str(task.id),
                        "source_watermark_cursor": source_watermark_cursor,
                    },
                    idempotency_key=f"{idempotency_key}:memory-snapshot",
                )
                snapshot = self._snapshot_builder_factory(unit_of_work).build(
                    scope=unbound_context.scope,
                    query=task.user_request,
                    source_watermark_cursor=source_watermark_cursor,
                )
                persisted_snapshot = unit_of_work.snapshots.append(
                    snapshot,
                    request_fingerprint=self._snapshot_request_fingerprint(task.id),
                )
                task.bind_memory_snapshot(
                    persisted_snapshot.id,
                    persisted_snapshot.content_hash,
                )
                unit_of_work.state.save_task(task)
                commands.complete(
                    snapshot_running.id,
                    output={
                        "snapshot_id": str(persisted_snapshot.id),
                        "content_hash": persisted_snapshot.content_hash,
                    },
                    lease_owner=snapshot_running.lease_owner,
                    lease_fence=snapshot_running.lease_fence,
                )
                context = self._build_context(task, conversation, target_version)
                running = self._start_command(
                    commands,
                    tool_name=tool_name,
                    scope=context.scope,
                    payload=payload,
                    idempotency_key=f"{idempotency_key}:workspace",
                )
                unit_of_work.commit()
            return _TaskIntent(
                task=task,
                conversation=conversation,
                target_version=target_version,
                running=running,
            )
        except IntegrityError:
            with self._transaction() as (unit_of_work, _commands):
                existing = unit_of_work.state.find_task_by_idempotency_key(idempotency_key)
                if existing is None:
                    raise
                self._validate_task_replay(existing, request)
                return self._context_for(unit_of_work.state, existing)

    def propose_changeset(self, request: ChangesetProposal) -> PendingChangeset:
        idempotency_key = self._normalize_idempotency_key(request.idempotency_key)
        try:
            return self._propose_changeset_once(
                request,
                idempotency_key=idempotency_key,
            )
        except IntegrityError as conflict:
            with self._transaction() as (unit_of_work, _commands):
                existing = unit_of_work.state.find_changeset_by_idempotency_key(idempotency_key)
                if existing is None:
                    raise
                self._validate_changeset_replay(existing, request)
                approval = unit_of_work.state.find_approval_by_changeset_id(existing.id)
                if approval is None:
                    raise RuntimeError("changeset approval is missing") from conflict
                return PendingChangeset(changeset=existing, approval=approval)

    def _propose_changeset_once(
        self,
        request: ChangesetProposal,
        *,
        idempotency_key: str,
    ) -> PendingChangeset:
        with self._transaction() as (unit_of_work, commands):
            existing = unit_of_work.state.find_changeset_by_idempotency_key(idempotency_key)
            if existing is not None:
                self._validate_changeset_replay(existing, request)
                approval = unit_of_work.state.find_approval_by_changeset_id(existing.id)
                if approval is None:
                    raise RuntimeError("changeset approval is missing")
                return PendingChangeset(changeset=existing, approval=approval)
            task = self._require_task(unit_of_work.state, request.task_id)
            if task.status is not TaskStatus.PLANNING:
                raise InvalidTransitionError("Task must be planning before proposing a Changeset")
            if task.project_id is None or task.target_version_id is None:
                raise ValueError("scratch tasks cannot apply project Changesets")
            context = self._context_for(unit_of_work.state, task)
            changeset = Changeset.create(
                project_id=task.project_id,
                conversation_id=task.conversation_id,
                task_id=task.id,
                version_id=task.target_version_id,
                files=tuple(mutation.path for mutation in request.files),
                patches=tuple(mutation.content for mutation in request.files),
                reason=request.reason,
                risk_level="medium",
                idempotency_key=idempotency_key,
            )
            dispatch = commands.submit(
                CommandRequest(
                    tool_name="edit.apply_changeset",
                    actor="agent",
                    scope=context.scope,
                    payload={"files": list(changeset.files), "reason": changeset.reason},
                    idempotency_key=f"{idempotency_key}:apply",
                ),
                profile=PermissionProfile.STANDARD,
                capability_overrides={},
                sandbox_healthy=False,
            )
            if not dispatch.accepted or not dispatch.requires_approval or dispatch.run is None:
                raise RuntimeError(dispatch.error_code or "Changeset approval command was rejected")
            changeset.transition_to(ChangesetStatus.AWAITING_APPROVAL)
            approval = Approval.create(
                task_id=task.id,
                command_run_id=dispatch.run.id,
                changeset_id=changeset.id,
                requested_by="agent",
                reason=f"Write {len(changeset.files)} project file(s)",
            )
            task.transition_to(TaskStatus.AWAITING_APPROVAL)
            unit_of_work.state.save_changeset(changeset)
            unit_of_work.state.save_approval(approval)
            unit_of_work.state.save_task(task)
            unit_of_work.commit()
        return PendingChangeset(changeset=changeset, approval=approval)

    def decide_approval(
        self,
        *,
        approval_id: UUID,
        approved: bool,
        decided_by: str,
    ) -> Changeset:
        with self._transaction() as (unit_of_work, commands):
            approval = self._require_approval(unit_of_work.state, approval_id)
            if approval.changeset_id is None:
                raise ValueError("approval is not associated with a Changeset")
            changeset = self._require_changeset(
                unit_of_work.state,
                approval.changeset_id,
            )
            decision = ApprovalDecision.APPROVED if approved else ApprovalDecision.REJECTED
            if approval.decision is not ApprovalDecision.PENDING:
                if approval.decision is decision:
                    return changeset
                raise InvalidTransitionError("approval has already been decided differently")
            task = self._require_task(unit_of_work.state, approval.task_id)
            approval.decide(decision=decision, decided_by=decided_by)
            changeset.record_approval(decision)
            if not approved:
                commands.decide_approval(approval.command_run_id, approved=False)
                changeset.transition_to(ChangesetStatus.REJECTED)
                task.transition_to(TaskStatus.REJECTED)
                unit_of_work.state.save_approval(approval)
                unit_of_work.state.save_changeset(changeset)
                unit_of_work.state.save_task(task)
                unit_of_work.commit()
                return changeset

            commands.decide_approval(approval.command_run_id, approved=True)
            running = commands.start(approval.command_run_id)
            changeset.transition_to(ChangesetStatus.APPLYING)
            task.transition_to(TaskStatus.EXECUTING)
            unit_of_work.state.save_approval(approval)
            unit_of_work.state.save_changeset(changeset)
            unit_of_work.state.save_task(task)
            unit_of_work.commit()

        try:
            written = self._workspaces.apply_changeset(
                project_id=changeset.project_id,
                version_id=changeset.version_id,
                mutations=tuple(zip(changeset.files, changeset.patches, strict=True)),
            )
        except Exception as error:
            with self._transaction() as (unit_of_work, commands):
                failed_changeset = self._require_changeset(
                    unit_of_work.state,
                    changeset.id,
                )
                failed_task = self._require_task(unit_of_work.state, task.id)
                commands.fail(
                    running.id,
                    error_code=str(getattr(error, "error_code", "WORKER_INTERRUPTED")),
                    lease_owner=running.lease_owner,
                    lease_fence=running.lease_fence,
                )
                failed_changeset.transition_to(ChangesetStatus.FAILED)
                failed_task.transition_to(TaskStatus.FAILED)
                unit_of_work.state.save_changeset(failed_changeset)
                unit_of_work.state.save_task(failed_task)
                unit_of_work.commit()
            raise

        with self._transaction() as (unit_of_work, commands):
            applied = self._require_changeset(unit_of_work.state, changeset.id)
            commands.complete(
                running.id,
                output={"changed_files": [str(path) for path in written]},
                lease_owner=running.lease_owner,
                lease_fence=running.lease_fence,
            )
            applied.transition_to(ChangesetStatus.APPLIED)
            unit_of_work.state.save_changeset(applied)
            unit_of_work.commit()
        return applied

    def review_task(self, task_id: UUID) -> Checkpoint:
        with self._transaction() as (unit_of_work, commands):
            task = self._require_task(unit_of_work.state, task_id)
            if task.status is not TaskStatus.EXECUTING:
                raise InvalidTransitionError("Task must be executing before review")
            if task.project_id is None or task.target_version_id is None:
                raise ValueError("scratch tasks do not produce project checkpoints")
            task.transition_to(TaskStatus.REVIEWING)
            unit_of_work.state.save_task(task)
            context = self._context_for(unit_of_work.state, task)
            diff_running = self._start_command(
                commands,
                tool_name="workspace.diff",
                scope=context.scope,
                payload={
                    "project_id": str(task.project_id),
                    "version_id": str(task.target_version_id),
                },
                idempotency_key=f"task:{task.id}:diff",
            )
            unit_of_work.commit()

        try:
            diff = self._workspaces.diff(
                project_id=task.project_id,
                version_id=task.target_version_id,
            )
        except Exception as error:
            with self._transaction() as (unit_of_work, commands):
                failed_task = self._require_task(unit_of_work.state, task.id)
                commands.fail(
                    diff_running.id,
                    error_code=str(getattr(error, "error_code", "WORKER_INTERRUPTED")),
                    lease_owner=diff_running.lease_owner,
                    lease_fence=diff_running.lease_fence,
                )
                failed_task.transition_to(TaskStatus.FAILED)
                unit_of_work.state.save_task(failed_task)
                unit_of_work.commit()
            raise

        with self._transaction() as (unit_of_work, commands):
            commands.complete(
                diff_running.id,
                output={"has_changes": bool(diff), "bytes": len(diff)},
                lease_owner=diff_running.lease_owner,
                lease_fence=diff_running.lease_fence,
            )
            checkpoint_running = self._start_command(
                commands,
                tool_name="workspace.checkpoint",
                scope=context.scope,
                payload={
                    "project_id": str(task.project_id),
                    "version_id": str(task.target_version_id),
                },
                idempotency_key=f"task:{task.id}:checkpoint",
            )
            unit_of_work.commit()

        try:
            commit = self._workspaces.checkpoint(
                project_id=task.project_id,
                version_id=task.target_version_id,
                message=f"Fairy Task {task.id}",
            )
        except Exception as error:
            with self._transaction() as (unit_of_work, commands):
                failed_task = self._require_task(unit_of_work.state, task.id)
                commands.fail(
                    checkpoint_running.id,
                    error_code=str(getattr(error, "error_code", "WORKER_INTERRUPTED")),
                    lease_owner=checkpoint_running.lease_owner,
                    lease_fence=checkpoint_running.lease_fence,
                )
                failed_task.transition_to(TaskStatus.FAILED)
                unit_of_work.state.save_task(failed_task)
                unit_of_work.commit()
            raise

        with self._transaction() as (unit_of_work, commands):
            persisted_task = self._require_task(unit_of_work.state, task.id)
            changesets = unit_of_work.state.changesets_for_task(task.id)
            changed_files = tuple(dict.fromkeys(path for item in changesets for path in item.files))
            approvals = [
                approval
                for item in changesets
                if (approval := unit_of_work.state.find_approval_by_changeset_id(item.id))
                is not None
            ]
            checkpoint = Checkpoint.create(
                task_id=persisted_task.id,
                version_id=persisted_task.target_version_id,
                changed_files=changed_files,
                command_run_ids=tuple(approval.command_run_id for approval in approvals),
                preview_artifact_id=None,
            )
            unit_of_work.state.save_checkpoint(checkpoint)
            persisted_task.transition_to(TaskStatus.READY)
            unit_of_work.state.save_task(persisted_task)
            commands.complete(
                checkpoint_running.id,
                output={"commit": commit},
                lease_owner=checkpoint_running.lease_owner,
                lease_fence=checkpoint_running.lease_fence,
            )
            unit_of_work.commit()
        return checkpoint

    def discard_task_version(self, task_id: UUID) -> Task:
        with self._transaction() as (unit_of_work, commands):
            task = self._require_task(unit_of_work.state, task_id)
            if task.project_id is None or task.target_version_id is None:
                raise ValueError("scratch tasks do not have a project Version")
            project = self._require_project(unit_of_work.state, task.project_id)
            if project.active_version_id == task.target_version_id:
                raise InvalidTransitionError("the Active Version cannot be discarded")
            context = self._context_for(unit_of_work.state, task)
            version = self._require_version(
                unit_of_work.state,
                task.target_version_id,
            )
            conversation = self._require_conversation(
                unit_of_work.state,
                task.conversation_id,
            )
            unit_of_work.state.reserve_version_discard(
                project_id=project.id,
                version_id=version.id,
                expected_revision=project.revision,
            )
            reservation_revision = project.revision + 1
            version.visibility = VersionVisibility.REJECTED
            if task.status is not TaskStatus.REJECTED:
                task.transition_to(TaskStatus.REJECTED)
            conversation.active_draft_version_id = None
            conversation.active_task_id = None
            unit_of_work.state.save_version(version)
            unit_of_work.state.save_task(task)
            unit_of_work.state.save_conversation(conversation)
            running = self._start_command(
                commands,
                tool_name="workspace.discard",
                scope=context.scope,
                payload={
                    "project_id": str(task.project_id),
                    "version_id": str(task.target_version_id),
                },
                idempotency_key=(f"task:{task.id}:discard:revision:{reservation_revision}"),
            )
            unit_of_work.commit()

        try:
            self._workspaces.discard_version(
                project_id=task.project_id,
                version_id=task.target_version_id,
            )
        except Exception as error:
            with self._transaction() as (unit_of_work, commands):
                commands.fail(
                    running.id,
                    error_code=str(getattr(error, "error_code", "WORKER_INTERRUPTED")),
                    lease_owner=running.lease_owner,
                    lease_fence=running.lease_fence,
                )
                unit_of_work.commit()
            raise

        with self._transaction() as (unit_of_work, commands):
            persisted_task = self._require_task(unit_of_work.state, task.id)
            commands.complete(
                running.id,
                output={"discarded": True},
                lease_owner=running.lease_owner,
                lease_fence=running.lease_fence,
            )
            unit_of_work.commit()
        return persisted_task

    def get_project(self, project_id: UUID) -> Project:
        with self._transaction() as (unit_of_work, _commands):
            return self._require_project(unit_of_work.state, project_id)

    def get_task(self, task_id: UUID) -> Task:
        with self._transaction() as (unit_of_work, _commands):
            return self._require_task(unit_of_work.state, task_id)

    def get_version(self, version_id: UUID) -> Version:
        with self._transaction() as (unit_of_work, _commands):
            return self._require_version(unit_of_work.state, version_id)

    def scope_for_task(self, state: StateStore, task: Task) -> ScopeContract:
        """Resolve a Task Scope from state already bound to the caller transaction."""

        return self._context_for(state, task).scope

    def transition_task(self, task_id: UUID, status: TaskStatus) -> Task:
        with self._transaction() as (unit_of_work, _commands):
            task = self._require_task(unit_of_work.state, task_id)
            task.transition_to(status)
            unit_of_work.state.save_task(task)
            unit_of_work.commit()
        return task

    def accept_task_version(
        self,
        *,
        task_id: UUID,
        expected_project_revision: int,
        user_confirmed: bool,
    ) -> Project:
        if not user_confirmed:
            raise ApprovalRequiredError("Active Version promotion requires explicit confirmation")

        with self._transaction() as (unit_of_work, commands):
            task = self._require_task(unit_of_work.state, task_id)
            if task.status is not TaskStatus.READY:
                raise InvalidTransitionError("Task must be ready before accepting its Version")
            if task.project_id is None or task.target_version_id is None:
                raise ValueError("scratch tasks do not have promotable versions")
            context = self._context_for(unit_of_work.state, task)
            dispatch = commands.submit(
                CommandRequest(
                    tool_name="project.accept_version",
                    actor="user",
                    scope=context.scope,
                    payload={
                        "version_id": str(task.target_version_id),
                        "expected_project_revision": expected_project_revision,
                    },
                    idempotency_key=(f"task:{task.id}:accept:revision:{expected_project_revision}"),
                ),
                profile=PermissionProfile.STANDARD,
                capability_overrides={},
                sandbox_healthy=False,
            )
            if not dispatch.accepted or not dispatch.requires_approval or dispatch.run is None:
                raise RuntimeError(dispatch.error_code or "Version promotion command was rejected")
            queued = commands.decide_approval(dispatch.run.id, approved=True)
            running = commands.start(queued.id)
            unit_of_work.commit()

        try:
            with self._transaction() as (unit_of_work, commands):
                persisted_task = self._require_task(unit_of_work.state, task.id)
                if persisted_task.status is not TaskStatus.READY:
                    raise InvalidTransitionError(
                        "Task must remain ready while accepting its Version"
                    )
                version = self._require_version(
                    unit_of_work.state,
                    persisted_task.target_version_id,
                )
                if version.visibility not in {
                    VersionVisibility.CHAT_DRAFT,
                    VersionVisibility.PROJECT_CANDIDATE,
                }:
                    raise InvalidTransitionError("Version is no longer eligible for promotion")
                project = unit_of_work.state.accept_version(
                    project_id=persisted_task.project_id,
                    version_id=persisted_task.target_version_id,
                    expected_revision=expected_project_revision,
                )
                conversation = self._require_conversation(
                    unit_of_work.state,
                    persisted_task.conversation_id,
                )
                version.visibility = VersionVisibility.PROJECT_ACTIVE
                persisted_task.transition_to(TaskStatus.ACCEPTED)
                conversation.base_version_id = persisted_task.target_version_id
                conversation.active_draft_version_id = None
                conversation.active_task_id = None
                unit_of_work.state.save_version(version)
                unit_of_work.state.save_task(persisted_task)
                unit_of_work.state.save_conversation(conversation)
                commands.complete(
                    running.id,
                    output={
                        "project_revision": project.revision,
                        "version_id": str(version.id),
                    },
                    lease_owner=running.lease_owner,
                    lease_fence=running.lease_fence,
                )
                unit_of_work.commit()
        except Exception as error:
            with self._transaction() as (unit_of_work, commands):
                commands.fail(
                    running.id,
                    error_code=str(getattr(error, "code", "WORKER_INTERRUPTED")),
                    lease_owner=running.lease_owner,
                    lease_fence=running.lease_fence,
                )
                unit_of_work.commit()
            raise
        return project

    def _context_for(self, state: StateStore, task: Task) -> TaskContext:
        conversation = self._require_conversation(state, task.conversation_id)
        target = (
            self._require_version(state, task.target_version_id) if task.target_version_id else None
        )
        return self._build_context(task, conversation, target)

    def _build_context(
        self,
        task: Task,
        conversation: Conversation,
        target_version: Version | None,
    ) -> TaskContext:
        if target_version is not None:
            root = target_version.project_root
            read_scope = (
                "project_canonical",
                "current_conversation",
                "user_profile",
                "task_episode",
                "current_version",
            )
            network_policy = "project_safe"
        else:
            root = self._workspaces.scratch_path(
                conversation.id,
                task.id,
            ).resolve(strict=False)
            read_scope = ("current_conversation", "user_profile", "task_episode")
            network_policy = "open_web_safe"
        scope = ScopeContract.create(
            workspace_type=conversation.workspace_type,
            project_id=task.project_id,
            conversation_id=conversation.id,
            task_id=task.id,
            operation_mode=task.operation_mode,
            base_version_id=task.base_version_id,
            target_version_id=task.target_version_id,
            project_root=root,
            allowed_write_paths=(root,),
            forbidden_write_paths=(),
            execution_target=task.execution_target,
            network_policy=network_policy,
            memory_read_scope=read_scope,
            memory_write_scope=("current_conversation_draft",),
            memory_snapshot_id=task.memory_snapshot_id,
            memory_snapshot_hash=task.memory_snapshot_hash,
        )
        return TaskContext(task=task, target_version=target_version, scope=scope)

    def _start_command(
        self,
        commands: CommandBus,
        *,
        tool_name: str,
        scope: ScopeContract,
        payload: dict[str, object],
        idempotency_key: str,
    ) -> CommandRun:
        dispatch = commands.submit(
            CommandRequest(
                tool_name=tool_name,
                actor="core",
                scope=scope,
                payload=payload,
                idempotency_key=idempotency_key,
            ),
            profile=PermissionProfile.STANDARD,
            capability_overrides={},
            sandbox_healthy=False,
        )
        if not dispatch.accepted or dispatch.run is None:
            raise RuntimeError(dispatch.error_code or "command was rejected")
        if dispatch.requires_approval:
            raise ApprovalRequiredError(dispatch.reason or "command requires approval")
        if dispatch.run.status is not CommandStatus.QUEUED:
            raise RuntimeError(f"command cannot execute from {dispatch.run.status}")
        return commands.start(dispatch.run.id)

    @contextmanager
    def _transaction(self) -> Iterator[tuple[CoreUnitOfWork, CommandBus]]:
        with self._unit_of_work_factory() as unit_of_work:
            yield unit_of_work, self._command_bus(unit_of_work.commands)

    def _command_bus(self, ledger: CommandLedger) -> CommandBus:
        return CommandBus(
            registry=self._registry,
            policy=self._policy,
            ledger=ledger,
        )

    @staticmethod
    def _default_snapshot_builder(
        unit_of_work: CoreUnitOfWork,
    ) -> MemorySnapshotBuilder:
        return DeterministicMemorySnapshotBuilder(
            memory_repository=unit_of_work.memory,
            search_index=unit_of_work.memory_search,
        )

    @staticmethod
    def _snapshot_request_fingerprint(task_id: UUID) -> str:
        return hashlib.sha256(f"task:{task_id}:memory-snapshot:v1".encode()).hexdigest()

    @staticmethod
    def _require_project(state: StateStore, project_id: UUID) -> Project:
        project = state.get_project(project_id)
        if project is None:
            raise KeyError(f"project not found: {project_id}")
        return project

    @staticmethod
    def _require_conversation(
        state: StateStore,
        conversation_id: UUID,
    ) -> Conversation:
        conversation = state.get_conversation(conversation_id)
        if conversation is None:
            raise KeyError(f"conversation not found: {conversation_id}")
        return conversation

    @staticmethod
    def _require_task(state: StateStore, task_id: UUID) -> Task:
        task = state.get_task(task_id)
        if task is None:
            raise KeyError(f"task not found: {task_id}")
        return task

    @staticmethod
    def _require_version(state: StateStore, version_id: UUID | None) -> Version:
        if version_id is None:
            raise ValueError("version_id is required")
        version = state.get_version(version_id)
        if version is None:
            raise KeyError(f"version not found: {version_id}")
        return version

    @staticmethod
    def _require_changeset(state: StateStore, changeset_id: UUID) -> Changeset:
        changeset = state.get_changeset(changeset_id)
        if changeset is None:
            raise KeyError(f"changeset not found: {changeset_id}")
        return changeset

    @staticmethod
    def _require_approval(state: StateStore, approval_id: UUID) -> Approval:
        approval = state.get_approval(approval_id)
        if approval is None:
            raise KeyError(f"approval not found: {approval_id}")
        return approval

    @staticmethod
    def _normalize_idempotency_key(value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("idempotency_key is required")
        return normalized

    @staticmethod
    def _validate_task_replay(existing: Task, request: TaskCreate) -> None:
        if (
            existing.conversation_id != request.conversation_id
            or existing.user_request != request.user_request.strip()
            or existing.operation_mode is not request.operation_mode
            or existing.execution_target != request.execution_target.value
        ):
            raise IdempotencyConflictError(
                "task idempotency key was already used for a different request"
            )

    @staticmethod
    def _validate_changeset_replay(
        existing: Changeset,
        request: ChangesetProposal,
    ) -> None:
        if (
            existing.task_id != request.task_id
            or existing.files != tuple(mutation.path for mutation in request.files)
            or existing.patches != tuple(mutation.content for mutation in request.files)
            or existing.reason != request.reason.strip()
        ):
            raise IdempotencyConflictError(
                "changeset idempotency key was already used for different mutations"
            )
