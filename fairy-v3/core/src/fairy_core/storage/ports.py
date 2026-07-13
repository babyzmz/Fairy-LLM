from __future__ import annotations

from typing import Protocol
from uuid import UUID

from fairy_core.domain.execution import (
    Approval,
    ApprovalDecision,
    Artifact,
    Changeset,
    Checkpoint,
    PreviewSession,
    RuntimeSession,
)
from fairy_core.domain.models import Conversation, Project, Task, Version, Workspace
from fairy_core.execution.plans import ExecutionPlan, TaskStep, TaskStepStatus
from fairy_core.research.models import ResearchEvidence
from fairy_core.storage.pagination import StatePage


class StateStore(Protocol):
    def save_execution_plan(
        self,
        plan: ExecutionPlan,
        steps: list[TaskStep] | tuple[TaskStep, ...],
    ) -> None: ...

    def update_execution_plan(
        self,
        plan: ExecutionPlan,
        *,
        expected_revision: int,
    ) -> None: ...

    def get_execution_plan(self, plan_id: UUID) -> ExecutionPlan | None: ...

    def execution_plan_for_task(self, task_id: UUID) -> ExecutionPlan | None: ...

    def task_steps_for_plan(self, plan_id: UUID) -> list[TaskStep]: ...

    def save_task_step(
        self,
        step: TaskStep,
        *,
        expected_status: TaskStepStatus,
        expected_attempts: int,
    ) -> None: ...

    def save_workspace(self, workspace: Workspace) -> None: ...

    def get_workspace(self, workspace_id: UUID) -> Workspace | None: ...

    def save_project(self, project: Project) -> None: ...

    def get_project(self, project_id: UUID) -> Project | None: ...

    def list_projects(self, *, limit: int, cursor: str | None) -> StatePage[Project]: ...

    def save_conversation(self, conversation: Conversation) -> None: ...

    def update_conversation_metadata(
        self,
        conversation: Conversation,
        *,
        expected_revision: int,
    ) -> None: ...

    def get_conversation(self, conversation_id: UUID) -> Conversation | None: ...

    def list_conversations(
        self,
        *,
        project_id: UUID | None,
        limit: int,
        cursor: str | None,
    ) -> StatePage[Conversation]: ...

    def save_version(self, version: Version) -> None: ...

    def get_version(self, version_id: UUID) -> Version | None: ...

    def list_versions(
        self,
        *,
        workspace_id: UUID | None,
        project_id: UUID | None,
        conversation_id: UUID | None,
        task_id: UUID | None,
        limit: int,
        cursor: str | None,
    ) -> StatePage[Version]: ...

    def save_task(self, task: Task, *, idempotency_key: str | None = None) -> None: ...

    def update_task_metadata(self, task: Task, *, expected_revision: int) -> None: ...

    def get_task(self, task_id: UUID) -> Task | None: ...

    def list_tasks(
        self,
        *,
        project_id: UUID | None,
        conversation_id: UUID | None,
        limit: int,
        cursor: str | None,
    ) -> StatePage[Task]: ...

    def find_task_by_idempotency_key(self, idempotency_key: str) -> Task | None: ...

    def save_changeset(self, changeset: Changeset) -> None: ...

    def get_changeset(self, changeset_id: UUID) -> Changeset | None: ...

    def find_changeset_by_idempotency_key(self, idempotency_key: str) -> Changeset | None: ...

    def changesets_for_task(self, task_id: UUID) -> list[Changeset]: ...

    def save_approval(self, approval: Approval) -> None: ...

    def update_approval(
        self,
        approval: Approval,
        *,
        expected_decision: ApprovalDecision,
    ) -> None: ...

    def get_approval(self, approval_id: UUID) -> Approval | None: ...

    def list_approvals(
        self,
        *,
        project_id: UUID | None,
        conversation_id: UUID | None,
        task_id: UUID | None,
        limit: int,
        cursor: str | None,
    ) -> StatePage[Approval]: ...

    def find_approval_by_changeset_id(self, changeset_id: UUID) -> Approval | None: ...

    def find_approval_by_tool_invocation_id(
        self,
        tool_invocation_id: UUID,
    ) -> Approval | None: ...

    def find_approval_by_command_run_id(self, command_run_id: UUID) -> Approval | None: ...

    def save_checkpoint(self, checkpoint: Checkpoint) -> None: ...

    def get_checkpoint(self, checkpoint_id: UUID) -> Checkpoint | None: ...

    def append_runtime(self, runtime: RuntimeSession) -> RuntimeSession: ...

    def save_runtime(
        self,
        runtime: RuntimeSession,
        *,
        expected_revision: int,
    ) -> RuntimeSession: ...

    def get_runtime(self, runtime_id: UUID) -> RuntimeSession | None: ...

    def find_runtime_by_idempotency_key(self, key: str) -> RuntimeSession | None: ...

    def runtimes_for_task(self, task_id: UUID) -> list[RuntimeSession]: ...

    def recoverable_runtimes(self) -> list[RuntimeSession]: ...

    def append_preview(self, preview: PreviewSession) -> PreviewSession: ...

    def save_preview(
        self,
        preview: PreviewSession,
        *,
        expected_revision: int,
    ) -> PreviewSession: ...

    def get_preview(self, preview_id: UUID) -> PreviewSession | None: ...

    def find_preview_by_idempotency_key(self, key: str) -> PreviewSession | None: ...

    def preview_for_task(
        self,
        task_id: UUID,
        *,
        include_terminal: bool = False,
    ) -> PreviewSession | None: ...

    def previews_for_conversation(self, conversation_id: UUID) -> list[PreviewSession]: ...

    def append_artifact(self, artifact: Artifact) -> Artifact: ...

    def get_artifact(self, artifact_id: UUID) -> Artifact | None: ...

    def artifacts_for_task(self, task_id: UUID) -> list[Artifact]: ...

    def append_research_evidence(
        self,
        evidence: ResearchEvidence,
    ) -> ResearchEvidence: ...

    def research_evidence_for_artifact(
        self,
        artifact_id: UUID,
    ) -> list[ResearchEvidence]: ...

    def accept_version(
        self,
        *,
        project_id: UUID,
        version_id: UUID,
        expected_revision: int,
    ) -> Project: ...

    def reserve_version_discard(
        self,
        *,
        project_id: UUID,
        version_id: UUID,
        expected_revision: int,
    ) -> Project: ...

    def close(self) -> None: ...
