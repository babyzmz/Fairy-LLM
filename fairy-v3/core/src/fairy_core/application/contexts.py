from dataclasses import dataclass

from fairy_core.commanding import CommandRun
from fairy_core.domain.execution import Approval, Changeset
from fairy_core.domain.models import Conversation, Project, ScopeContract, Task, Version


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
class TaskIntent:
    task: Task
    conversation: Conversation
    target_version: Version | None
    running: CommandRun


__all__ = ["PendingChangeset", "ProjectContext", "TaskContext", "TaskIntent"]
