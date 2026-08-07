from __future__ import annotations

from pathlib import Path
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from fairy_core.application import snapshot_support as snapshot
from fairy_core.application.approval import ApprovalApplication
from fairy_core.application.changeset_limits import validate_changeset_limits
from fairy_core.application.contexts import (
    PendingChangeset,
    ProjectContext,
    TaskContext,
    TaskIntent,
)
from fairy_core.application.core_context import CoreContextMixin
from fairy_core.application.core_support import (
    CoreSupportMixin,
    approve_changeset_by_policy,
)
from fairy_core.application.errors import command_rejected
from fairy_core.application.history import ConversationMoveContext, HistoryApplication
from fairy_core.application.recoverable_command import start_recoverable_core_command
from fairy_core.application.replay_validation import (
    normalize_idempotency_key,
    validate_changeset_replay,
    validate_task_replay,
)
from fairy_core.application.review_evidence import collect_checkpoint_evidence
from fairy_core.application.workspace_mutation import WorkspaceMutationApplication
from fairy_core.application.workspaces import WorkspaceApplication
from fairy_core.commanding import CommandStatus
from fairy_core.commanding.bus import CommandRequest
from fairy_core.commanding.policy import PolicyEngine
from fairy_core.commanding.registry import ToolRegistry
from fairy_core.commanding.settings import ExecutionPolicyResolver
from fairy_core.contracts.models import ChangesetProposal, TaskCreate
from fairy_core.domain.errors import InvalidTransitionError, VersionConflictError
from fairy_core.domain.execution import (
    Approval,
    Changeset,
    ChangesetStatus,
    Checkpoint,
    PreviewStatus,
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
    Workspace,
    WorkspaceType,
)
from fairy_core.execution.planning import ExecutionPlanningApplication
from fairy_core.execution.plans import (
    ExecutionPlanStatus,
    TaskStepKind,
    TaskStepStatus,
)
from fairy_core.persistence.unit_of_work import CoreUnitOfWork, CoreUnitOfWorkFactory
from fairy_core.workspace.index import ProjectIndexer
from fairy_core.workspace.mutations import (
    decode_mutation,
    encode_mutation,
    expected_workspace_revision,
)
from fairy_core.workspace.ports import WorkspaceProvisioner


class CoreApplication(CoreContextMixin, CoreSupportMixin):
    def __init__(
        self,
        *,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        workspace_provisioner: WorkspaceProvisioner,
        registry: ToolRegistry,
        policy: PolicyEngine,
        snapshot_builder_factory: snapshot.SnapshotBuilderFactory | None = None,
        project_indexer: ProjectIndexer | None = None,
        execution_policy: ExecutionPolicyResolver | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._workspaces = workspace_provisioner
        self._registry = registry
        self._policy = policy
        self._project_indexer = project_indexer or ProjectIndexer()
        self.workspace_access = WorkspaceApplication(
            unit_of_work_factory,
            self._project_indexer,
            workspace_provisioner,
        )
        self.execution_planning = ExecutionPlanningApplication(unit_of_work_factory)
        self._execution_policy = execution_policy or ExecutionPolicyResolver()
        self._approvals = ApprovalApplication(
            unit_of_work_factory=unit_of_work_factory,
        )
        self.history = HistoryApplication(unit_of_work_factory, workspace_provisioner)
        self._snapshot_builder_factory = snapshot_builder_factory or snapshot.build_default_snapshot
        self.workspace_mutations = WorkspaceMutationApplication(
            unit_of_work_factory=unit_of_work_factory,
            create_task=self.create_task,
            propose_changeset=self.propose_changeset,
            decide_approval=self.decide_approval,
            get_approval=self.get_approval,
        )

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
            workspace_id=project.workspace_id,
            workspace_type=WorkspaceType.PROJECT_CHAT,
            base_version_id=None,
        )
        setup_task = Task.create(
            project_id=project.id,
            workspace_id=project.workspace_id,
            conversation_id=setup_conversation.id,
            user_request="Import project" if source is not None else "Create project",
            operation_mode=OperationMode.CREATE_NEW_VERSION,
            base_version_id=None,
            execution_target="local",
        )
        setup_task.bind_target_version(version_id)
        setup_task.transition_to(TaskStatus.RESOLVING_SCOPE)
        root_hint = self._workspaces.version_path(project.workspace_id, version_id)
        scope = ScopeContract.create(
            workspace_type=WorkspaceType.PROJECT_CHAT,
            workspace_id=project.workspace_id,
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
            "project_id": str(project.workspace_id),
            "workspace_id": str(project.workspace_id),
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
                unit_of_work,
                commands,
                tool_name=tool_name,
                scope=scope,
                payload=payload,
                idempotency_key=f"project:{project.id}:workspace",
            )
            unit_of_work.commit()

        try:
            root = self._workspaces.create_initial_version(
                project.workspace_id,
                version_id,
                source=source,
            )
            initial_index = self._project_indexer.build(
                project_id=project.id,
                workspace_id=project.workspace_id,
                version_id=version_id,
                root=root,
                generation=1,
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
                workspace_id=persisted_project.workspace_id,
                source_conversation_id=None,
                source_task_id=None,
                parent_version_id=None,
                project_root=root,
                visibility=VersionVisibility.PROJECT_ACTIVE,
            )
            persisted_project.accept_version(version.id, expected_revision=0)
            workspace = unit_of_work.state.get_workspace(persisted_project.workspace_id)
            if workspace is None:
                raise RuntimeError("Project Workspace was not persisted")
            workspace.accept_version(version.id, expected_revision=0)
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
            unit_of_work.state.save_workspace(workspace)
            unit_of_work.workspaces.bind_once(
                task_id=persisted_task.id,
                project_id=persisted_project.id,
                workspace_id=persisted_project.workspace_id,
                conversation_id=persisted_conversation.id,
                version_id=version.id,
                root=root,
                editable_files=("*", "**/*"),
                reference_files=(),
                constraints={"max_read_bytes": 1_000_000},
            )
            unit_of_work.project_indexes.replace_generation(
                initial_index,
                expected_generation=0,
            )
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
        scratch_conversation = None
        try:
            with self._transaction() as (unit_of_work, _commands):
                if workspace_type is WorkspaceType.CHAT_SCRATCH:
                    scratch_conversation = self.create_scratch_conversation_in_unit_of_work(
                        unit_of_work
                    )
                    conversation = scratch_conversation
                else:
                    if project_id is None:
                        raise ValueError("project_chat requires project_id")
                    project = self._require_project(unit_of_work.state, project_id)
                    conversation = Conversation.create(
                        project_id=project_id,
                        workspace_id=project.workspace_id,
                        workspace_type=workspace_type,
                        base_version_id=project.active_version_id,
                    )
                    unit_of_work.state.save_conversation(conversation)
                unit_of_work.commit()
        except BaseException:
            if scratch_conversation is not None:
                self.purge_scratch_conversation(scratch_conversation)
            raise
        return conversation

    def create_scratch_conversation_in_unit_of_work(
        self,
        unit_of_work: CoreUnitOfWork,
    ) -> Conversation:
        conversation = Conversation.create(
            project_id=None,
            workspace_id=None,
            workspace_type=WorkspaceType.CHAT_SCRATCH,
            base_version_id=None,
        )
        assert conversation.workspace_id is not None
        try:
            unit_of_work.state.save_conversation(conversation)
            version_id = new_id()
            root = self._workspaces.create_initial_version(
                conversation.workspace_id,
                version_id,
                source=None,
            )
            version = Version.create(
                version_id=version_id,
                project_id=None,
                workspace_id=conversation.workspace_id,
                source_conversation_id=conversation.id,
                source_task_id=None,
                parent_version_id=None,
                project_root=root,
                visibility=VersionVisibility.PROJECT_ACTIVE,
            )
            conversation.base_version_id = version.id
            workspace = unit_of_work.state.get_workspace(conversation.workspace_id)
            if workspace is None:
                workspace = Workspace.create(workspace_id=conversation.workspace_id)
            workspace.accept_version(version.id, expected_revision=workspace.revision)
            unit_of_work.state.save_workspace(workspace)
            unit_of_work.state.save_version(version)
            unit_of_work.state.save_conversation(conversation)
            return conversation
        except BaseException:
            self._workspaces.purge_workspace(conversation.workspace_id)
            raise

    def purge_scratch_conversation(self, conversation: Conversation) -> None:
        if (
            conversation.workspace_type is not WorkspaceType.CHAT_SCRATCH
            or conversation.workspace_id is None
        ):
            raise ValueError("only a scratch conversation workspace can be purged")
        self._workspaces.purge_workspace(conversation.workspace_id)

    def create_task(
        self,
        request: TaskCreate,
        *,
        base_version_id_override: UUID | None = None,
    ) -> TaskContext:
        idempotency_key = normalize_idempotency_key(request.idempotency_key)
        prepared = self._prepare_task_intent(
            request,
            idempotency_key=idempotency_key,
            base_version_id_override=base_version_id_override,
        )
        if isinstance(prepared, TaskContext):
            return prepared
        task = prepared.task
        conversation = prepared.conversation
        target_version = prepared.target_version
        running = prepared.running

        try:
            assert target_version is not None and task.workspace_id is not None
            if task.base_version_id is None:
                root = self._workspaces.create_initial_version(
                    task.workspace_id,
                    target_version.id,
                )
            else:
                root = self._workspaces.fork_version(
                    project_id=task.workspace_id,
                    version_id=target_version.id,
                    parent_version_id=task.base_version_id,
                )
            project_index = self._project_indexer.build(
                project_id=task.project_id,
                workspace_id=task.workspace_id,
                version_id=target_version.id,
                root=root,
                generation=1,
            )
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
            unit_of_work.workspaces.bind_once(
                task_id=persisted_task.id,
                project_id=persisted_task.project_id,
                workspace_id=persisted_task.workspace_id,
                conversation_id=persisted_conversation.id,
                version_id=(persisted_target.id if persisted_target is not None else None),
                root=root,
                editable_files=("*", "**/*"),
                reference_files=(),
                constraints={"max_read_bytes": 1_000_000},
            )
            if project_index is not None:
                unit_of_work.project_indexes.replace_generation(
                    project_index,
                    expected_generation=0,
                )
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

    def update_conversation_metadata(
        self,
        *,
        conversation_id: UUID,
        title: str | None,
        pinned: bool | None,
        expected_revision: int,
    ) -> Conversation:
        return self.history.update_conversation(
            conversation_id=conversation_id,
            title=title,
            pinned=pinned,
            expected_revision=expected_revision,
        )

    def move_conversation_to_project(
        self,
        *,
        conversation_id: UUID,
        target_project_id: UUID,
        expected_revision: int,
        user_confirmed: bool,
        idempotency_key: str,
    ) -> ConversationMoveContext:
        return self.history.move_to_project(
            conversation_id=conversation_id,
            target_project_id=target_project_id,
            expected_revision=expected_revision,
            user_confirmed=user_confirmed,
            idempotency_key=idempotency_key,
        )

    def _prepare_task_intent(
        self,
        request: TaskCreate,
        *,
        idempotency_key: str,
        base_version_id_override: UUID | None = None,
    ) -> TaskIntent | TaskContext:
        try:
            with self._transaction() as (unit_of_work, commands):
                existing = unit_of_work.state.find_task_by_idempotency_key(idempotency_key)
                if existing is not None:
                    validate_task_replay(existing, request)
                    if (
                        base_version_id_override is not None
                        and existing.base_version_id != base_version_id_override
                    ):
                        raise VersionConflictError(
                            "scheduled Task idempotency key is bound to another base Version"
                        )
                    return self._context_for(unit_of_work.state, existing)

                conversation = self._require_conversation(
                    unit_of_work.state,
                    request.conversation_id,
                )
                base_version_id = base_version_id_override or (
                    conversation.active_draft_version_id or conversation.base_version_id
                )
                task = Task.create(
                    project_id=conversation.project_id,
                    workspace_id=conversation.workspace_id,
                    conversation_id=conversation.id,
                    user_request=request.user_request,
                    operation_mode=request.operation_mode,
                    base_version_id=base_version_id,
                    execution_target=request.execution_target.value,
                )
                if conversation.workspace_id is None:
                    raise ValueError("conversation has no Workspace")
                target_version: Version | None = None
                parent: Version | None = None
                if base_version_id is not None:
                    parent = self._require_version(unit_of_work.state, base_version_id)
                    if (
                        parent.workspace_id != conversation.workspace_id
                        or parent.project_id != conversation.project_id
                    ):
                        raise VersionConflictError(
                            "scheduled base Version does not belong to the conversation Workspace"
                        )
                version_id = new_id()
                root_hint = self._workspaces.version_path(
                    conversation.workspace_id,
                    version_id,
                )
                target_version = Version.create(
                    version_id=version_id,
                    project_id=conversation.project_id,
                    workspace_id=conversation.workspace_id,
                    source_conversation_id=conversation.id,
                    source_task_id=task.id,
                    parent_version_id=parent.id if parent is not None else None,
                    project_root=root_hint,
                    visibility=VersionVisibility.CHAT_DRAFT,
                )
                task.bind_target_version(target_version.id)
                conversation.active_draft_version_id = target_version.id

                task.transition_to(TaskStatus.RESOLVING_SCOPE)
                conversation.active_task_id = task.id
                unit_of_work.state.save_version(target_version)
                if parent is not None:
                    tool_name = "workspace.fork"
                    payload: dict[str, object] = {
                        "project_id": str(task.workspace_id),
                        "workspace_id": str(task.workspace_id),
                        "parent_version_id": str(parent.id),
                        "version_id": str(target_version.id),
                    }
                else:
                    tool_name = "workspace.create_empty"
                    payload = {
                        "workspace_id": str(task.workspace_id),
                        "version_id": str(target_version.id),
                    }
                unit_of_work.state.save_task(
                    task,
                    idempotency_key=idempotency_key,
                )
                unit_of_work.state.save_conversation(conversation)
                unbound_context = self._build_context(task, conversation, target_version)
                source_watermark_cursor = unit_of_work.commands.current_cursor()
                snapshot_running = self._start_command(
                    unit_of_work,
                    commands,
                    tool_name="memory.snapshot.build",
                    scope=unbound_context.scope,
                    payload={
                        "task_id": str(task.id),
                        "source_watermark_cursor": source_watermark_cursor,
                    },
                    idempotency_key=f"{idempotency_key}:memory-snapshot",
                )
                built_snapshot = self._snapshot_builder_factory(unit_of_work).build(
                    scope=unbound_context.scope,
                    query=task.user_request,
                    source_watermark_cursor=source_watermark_cursor,
                )
                persisted_snapshot = unit_of_work.snapshots.append(
                    built_snapshot,
                    request_fingerprint=snapshot.snapshot_request_fingerprint(task.id),
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
                    unit_of_work,
                    commands,
                    tool_name=tool_name,
                    scope=context.scope,
                    payload=payload,
                    idempotency_key=f"{idempotency_key}:workspace",
                )
                unit_of_work.commit()
            return TaskIntent(
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
                validate_task_replay(existing, request)
                return self._context_for(unit_of_work.state, existing)

    def propose_changeset(self, request: ChangesetProposal) -> PendingChangeset:
        idempotency_key = normalize_idempotency_key(request.idempotency_key)
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
                validate_changeset_replay(existing, request)
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
                validate_changeset_replay(existing, request)
                approval = unit_of_work.state.find_approval_by_changeset_id(existing.id)
                if approval is None:
                    raise RuntimeError("changeset approval is missing")
                return PendingChangeset(changeset=existing, approval=approval)
            task = self._require_task(unit_of_work.state, request.task_id)
            if task.status not in {
                TaskStatus.PLANNING,
                TaskStatus.EXECUTING,
                TaskStatus.REPAIRING,
            }:
                raise InvalidTransitionError(
                    "Task must be planning or executing before proposing a Changeset"
                )
            if task.target_version_id is None or task.workspace_id is None:
                raise ValueError("Task has no writable Workspace Version")
            workspace = unit_of_work.state.get_workspace(task.workspace_id)
            if workspace is None:
                raise ValueError("Task Workspace is unavailable")
            if (
                request.expected_workspace_revision is not None
                and workspace.revision != request.expected_workspace_revision
            ):
                raise VersionConflictError(
                    f"expected Workspace revision {request.expected_workspace_revision}, "
                    f"current revision is {workspace.revision}"
                )
            current_index = unit_of_work.project_indexes.get(task.target_version_id)
            validate_changeset_limits(workspace, current_index, request.files)
            if task.status is TaskStatus.REPAIRING:
                task.transition_to(TaskStatus.EXECUTING)
            context = self._context_for(unit_of_work.state, task)
            changeset = Changeset.create(
                project_id=task.project_id,
                workspace_id=task.workspace_id,
                conversation_id=task.conversation_id,
                task_id=task.id,
                version_id=task.target_version_id,
                files=tuple(mutation.path for mutation in request.files),
                patches=tuple(
                    encode_mutation(
                        mutation,
                        expected_workspace_revision=request.expected_workspace_revision,
                    )
                    for mutation in request.files
                ),
                reason=request.reason,
                risk_level="medium",
                idempotency_key=idempotency_key,
            )
            policy = self._execution_policy.resolve(
                unit_of_work.execution_settings,
                execution_target=context.scope.execution_target,
            )
            dispatch = commands.submit(
                CommandRequest(
                    tool_name="edit.apply_changeset",
                    actor="agent",
                    scope=context.scope,
                    payload={"files": list(changeset.files), "reason": changeset.reason},
                    idempotency_key=f"{idempotency_key}:apply",
                ),
                profile=policy.profile,
                capability_overrides=dict(policy.capability_overrides),
                sandbox_healthy=policy.sandbox_healthy,
            )
            if not dispatch.accepted or dispatch.run is None:
                raise command_rejected(dispatch, "Changeset approval command was rejected")
            auto_approved = not dispatch.requires_approval
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
        if auto_approved:
            approve_changeset_by_policy(
                self._unit_of_work_factory,
                approval_id=approval.id,
                changeset_id=changeset.id,
            )
            changeset = self.decide_approval(
                approval_id=approval.id,
                approved=True,
                decided_by="policy:autonomous",
            )
            approval = self.get_approval(approval.id)
        return PendingChangeset(changeset=changeset, approval=approval)

    def decide_approval(
        self,
        *,
        approval_id: UUID,
        approved: bool,
        decided_by: str,
    ) -> Changeset:
        approval = self.record_approval_decision(
            approval_id=approval_id,
            approved=approved,
            decided_by=decided_by,
        )
        if approval.changeset_id is None:
            raise ValueError("approval is not associated with a Changeset")
        with self._transaction() as (unit_of_work, commands):
            changeset = self._require_changeset(
                unit_of_work.state,
                approval.changeset_id,
            )
            if not approved or changeset.status in {
                ChangesetStatus.APPLIED,
                ChangesetStatus.APPLYING,
            }:
                return changeset
            if changeset.status is not ChangesetStatus.AWAITING_APPROVAL:
                raise InvalidTransitionError(
                    f"approved Changeset cannot resume from {changeset.status.value}"
                )
            task = self._require_task(unit_of_work.state, approval.task_id)
            running = commands.start(approval.command_run_id)
            changeset.transition_to(ChangesetStatus.APPLYING)
            task.transition_to(TaskStatus.EXECUTING)
            unit_of_work.state.save_changeset(changeset)
            unit_of_work.state.save_task(task)
            unit_of_work.commit()

        try:
            decoded = tuple(
                decode_mutation(path, patch)
                for path, patch in zip(changeset.files, changeset.patches, strict=True)
            )
            expected_revision = expected_workspace_revision(decoded)
            if expected_revision is not None:
                with self._unit_of_work_factory() as unit_of_work:
                    workspace = unit_of_work.state.get_workspace(changeset.workspace_id)
                if workspace is None or workspace.revision != expected_revision:
                    raise VersionConflictError(
                        f"expected Workspace revision {expected_revision} before applying Changeset"
                    )
            written = self._workspaces.apply_changeset(
                project_id=task.workspace_id,
                version_id=changeset.version_id,
                mutations=tuple(zip(changeset.files, changeset.patches, strict=True)),
            )
            with self._unit_of_work_factory() as unit_of_work:
                current_index = unit_of_work.project_indexes.get(changeset.version_id)
            current_generation = current_index.generation if current_index is not None else 0
            refreshed_index = self._project_indexer.build(
                project_id=changeset.project_id,
                workspace_id=changeset.workspace_id,
                version_id=changeset.version_id,
                root=self._workspaces.version_path(
                    changeset.workspace_id,
                    changeset.version_id,
                ),
                generation=current_generation + 1,
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
            unit_of_work.project_indexes.replace_generation(
                refreshed_index,
                expected_generation=current_generation,
            )
            unit_of_work.commit()
        self.execution_planning.complete_file_batch_if_planned(applied.task_id, applied.files)
        return applied

    def get_approval(self, approval_id: UUID) -> Approval:
        return self._approvals.get(approval_id)

    def checkpoint_scratch_task(self, task_id: UUID) -> None:
        """Commit a validated scratch candidate without promoting it to the active Version."""
        with self._transaction() as (unit_of_work, commands):
            task = self._require_task(unit_of_work.state, task_id)
            if task.project_id is not None:
                raise ValueError("only scratch tasks use automatic candidate checkpoints")
            if task.workspace_id is None or task.target_version_id is None:
                raise ValueError("scratch Task has no writable Workspace Version")
            context = self._context_for(unit_of_work.state, task)
            running = start_recoverable_core_command(
                unit_of_work,
                commands,
                execution_policy=self._execution_policy,
                tool_name="workspace.checkpoint",
                scope=context.scope,
                payload={
                    "workspace_id": str(task.workspace_id),
                    "version_id": str(task.target_version_id),
                },
                idempotency_key=f"task:{task.id}:scratch-checkpoint",
            )
            unit_of_work.commit()
        if running.status is CommandStatus.SUCCEEDED:
            return

        try:
            commit = self._workspaces.checkpoint(
                project_id=task.workspace_id,
                version_id=task.target_version_id,
                message=f"Fairy scratch Task {task.id}",
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
            commands.complete(
                running.id,
                output={"commit": commit},
                lease_owner=running.lease_owner,
                lease_fence=running.lease_fence,
            )
            unit_of_work.commit()

    def record_approval_decision(
        self,
        *,
        approval_id: UUID,
        approved: bool,
        decided_by: str,
    ) -> Approval:
        return self._approvals.decide(
            approval_id=approval_id,
            approved=approved,
            decided_by=decided_by,
        )

    def review_task(self, task_id: UUID) -> Checkpoint:
        with self._transaction() as (unit_of_work, commands):
            task = self._require_task(unit_of_work.state, task_id)
            if task.status not in {
                TaskStatus.EXECUTING,
                TaskStatus.PREVIEWING,
                TaskStatus.REVIEWING,
            }:
                raise InvalidTransitionError(
                    "Task must be executing, previewing, or reviewing before checkpoint"
                )
            if task.project_id is None or task.target_version_id is None:
                raise ValueError("scratch tasks do not produce project checkpoints")
            if task.status is not TaskStatus.REVIEWING:
                task.transition_to(TaskStatus.REVIEWING)
            unit_of_work.state.save_task(task)
            context = self._context_for(unit_of_work.state, task)
            diff_running = start_recoverable_core_command(
                unit_of_work,
                commands,
                execution_policy=self._execution_policy,
                tool_name="workspace.diff",
                scope=context.scope,
                payload={
                    "project_id": str(task.workspace_id),
                    "workspace_id": str(task.workspace_id),
                    "version_id": str(task.target_version_id),
                },
                idempotency_key=f"task:{task.id}:diff",
            )
            unit_of_work.commit()

        diff = ""
        if diff_running.status is not CommandStatus.SUCCEEDED:
            try:
                diff = self._workspaces.diff(
                    project_id=task.workspace_id,
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
            if diff_running.status is not CommandStatus.SUCCEEDED:
                commands.complete(
                    diff_running.id,
                    output={"has_changes": bool(diff), "bytes": len(diff)},
                    lease_owner=diff_running.lease_owner,
                    lease_fence=diff_running.lease_fence,
                )
            checkpoint_running = start_recoverable_core_command(
                unit_of_work,
                commands,
                execution_policy=self._execution_policy,
                tool_name="workspace.checkpoint",
                scope=context.scope,
                payload={
                    "project_id": str(task.workspace_id),
                    "workspace_id": str(task.workspace_id),
                    "version_id": str(task.target_version_id),
                },
                idempotency_key=f"task:{task.id}:checkpoint",
            )
            unit_of_work.commit()

        try:
            commit = self._workspaces.checkpoint(
                project_id=task.workspace_id,
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
            evidence = collect_checkpoint_evidence(
                state=unit_of_work.state,
                project_indexes=unit_of_work.project_indexes,
                task=persisted_task,
                changesets=changesets,
            )
            checkpoint = Checkpoint.create(
                task_id=persisted_task.id,
                version_id=persisted_task.target_version_id,
                changed_files=evidence.changed_files,
                command_run_ids=evidence.command_run_ids,
                preview_artifact_id=evidence.preview_artifact_id,
                evidence_artifact_ids=evidence.evidence_artifact_ids,
            )
            unit_of_work.state.save_checkpoint(checkpoint)
            persisted_task.transition_to(TaskStatus.READY)
            unit_of_work.state.save_task(persisted_task)
            plan = unit_of_work.state.execution_plan_for_task(persisted_task.id)
            if plan is not None and plan.status in {
                ExecutionPlanStatus.ACTIVE,
                ExecutionPlanStatus.PAUSED,
            }:
                checkpoint_step = next(
                    (
                        step
                        for step in unit_of_work.state.task_steps_for_plan(plan.id)
                        if step.kind is TaskStepKind.CHECKPOINT
                    ),
                    None,
                )
                if checkpoint_step is not None and checkpoint_step.status in {
                    TaskStepStatus.PENDING,
                    TaskStepStatus.RUNNING,
                }:
                    if checkpoint_step.status is TaskStepStatus.PENDING:
                        expected_status = checkpoint_step.status
                        expected_attempts = checkpoint_step.attempts
                        checkpoint_step.transition_to(TaskStepStatus.RUNNING)
                        unit_of_work.state.save_task_step(
                            checkpoint_step,
                            expected_status=expected_status,
                            expected_attempts=expected_attempts,
                        )
                    expected_status = checkpoint_step.status
                    expected_attempts = checkpoint_step.attempts
                    checkpoint_step.transition_to(TaskStepStatus.COMPLETED)
                    unit_of_work.state.save_task_step(
                        checkpoint_step,
                        expected_status=expected_status,
                        expected_attempts=expected_attempts,
                    )
                steps = unit_of_work.state.task_steps_for_plan(plan.id)
                if steps and all(
                    step.status in {TaskStepStatus.COMPLETED, TaskStepStatus.SKIPPED}
                    for step in steps
                ):
                    expected_revision = plan.revision
                    plan.complete()
                    unit_of_work.state.update_execution_plan(
                        plan,
                        expected_revision=expected_revision,
                    )
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
            blocking_previews = (
                preview
                for preview in unit_of_work.state.previews_for_conversation(task.conversation_id)
                if preview.task_id == task.id
                and preview.status not in {PreviewStatus.STOPPED, PreviewStatus.FAILED}
            )
            if next(blocking_previews, None) is not None:
                raise InvalidTransitionError(
                    "Preview must be stopped before discarding its Version"
                )
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
                unit_of_work,
                commands,
                tool_name="workspace.discard",
                scope=context.scope,
                payload={
                    "project_id": str(task.workspace_id),
                    "workspace_id": str(task.workspace_id),
                    "version_id": str(task.target_version_id),
                },
                idempotency_key=(f"task:{task.id}:discard:revision:{reservation_revision}"),
            )
            unit_of_work.commit()

        try:
            self._workspaces.discard_version(
                project_id=task.workspace_id,
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
