from __future__ import annotations

from collections.abc import Callable

from fairy_core.application.contexts import (
    PendingChangeset,
    TaskContext,
    WorkspaceMutationContext,
)
from fairy_core.application.core_support import (
    require_conversation,
    require_task,
    require_version,
)
from fairy_core.application.errors import ApprovalRequiredError
from fairy_core.contracts.common import ExecutionTarget
from fairy_core.contracts.models import ChangesetProposal, TaskCreate
from fairy_core.contracts.workspaces import WorkspaceFileMutateInput
from fairy_core.domain.errors import InvalidTransitionError, VersionConflictError
from fairy_core.domain.execution import Approval, Changeset, ChangesetStatus
from fairy_core.domain.models import OperationMode
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory


class WorkspaceMutationApplication:
    def __init__(
        self,
        *,
        unit_of_work_factory: CoreUnitOfWorkFactory,
        create_task: Callable[[TaskCreate], TaskContext],
        propose_changeset: Callable[[ChangesetProposal], PendingChangeset],
        decide_approval: Callable[..., Changeset],
        get_approval: Callable[..., Approval],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._create_task = create_task
        self._propose_changeset = propose_changeset
        self._decide_approval = decide_approval
        self._get_approval = get_approval

    def mutate(self, request: WorkspaceFileMutateInput) -> WorkspaceMutationContext:
        if not request.user_confirmed:
            raise ApprovalRequiredError("Workspace file mutation requires user confirmation")
        with self._unit_of_work_factory() as unit_of_work:
            conversation = require_conversation(unit_of_work.state, request.conversation_id)
            workspace = unit_of_work.state.get_workspace(request.workspace_id)
        if conversation.workspace_id != request.workspace_id or workspace is None:
            raise InvalidTransitionError("Conversation does not belong to the Workspace")
        if workspace.revision != request.expected_workspace_revision:
            raise VersionConflictError(
                f"expected Workspace revision {request.expected_workspace_revision}, "
                f"current revision is {workspace.revision}"
            )
        task_context = self._create_task(
            TaskCreate(
                conversation_id=conversation.id,
                user_request=request.reason,
                operation_mode=OperationMode.CONTINUE_CURRENT_DRAFT,
                execution_target=ExecutionTarget.LOCAL,
                idempotency_key=f"{request.idempotency_key}:task",
            )
        )
        pending = self._propose_changeset(
            ChangesetProposal(
                task_id=task_context.task.id,
                files=request.files,
                expected_workspace_revision=request.expected_workspace_revision,
                reason=request.reason,
                idempotency_key=f"{request.idempotency_key}:changeset",
            )
        )
        changeset = pending.changeset
        if changeset.status is ChangesetStatus.AWAITING_APPROVAL:
            changeset = self._decide_approval(
                approval_id=pending.approval.id,
                approved=True,
                decided_by="user",
            )
        approval = self._get_approval(pending.approval.id)
        with self._unit_of_work_factory() as unit_of_work:
            persisted_workspace = unit_of_work.state.get_workspace(request.workspace_id)
            persisted_conversation = require_conversation(
                unit_of_work.state,
                request.conversation_id,
            )
            persisted_task = require_task(unit_of_work.state, task_context.task.id)
            target_version = require_version(
                unit_of_work.state,
                persisted_task.target_version_id,
            )
        if persisted_workspace is None:
            raise RuntimeError("Workspace disappeared after file mutation")
        return WorkspaceMutationContext(
            workspace=persisted_workspace,
            conversation=persisted_conversation,
            task=persisted_task,
            target_version=target_version,
            changeset=changeset,
            approval=approval,
        )


__all__ = ["WorkspaceMutationApplication"]
