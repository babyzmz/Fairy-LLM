from __future__ import annotations

from uuid import UUID

from fairy_core.domain.models import TaskStatus
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory

_IDLE = {
    TaskStatus.READY,
    TaskStatus.ACCEPTED,
    TaskStatus.REJECTED,
    TaskStatus.ARCHIVED,
    TaskStatus.FAILED,
}


class BrowserTaskActivityProbe:
    """Read business authority; never derive activity from a visible WebView."""

    def __init__(self, factory: CoreUnitOfWorkFactory) -> None:
        self._factory = factory

    def __call__(self, task_ids: tuple[UUID, ...]) -> frozenset[UUID]:
        with self._factory() as unit:
            tasks = unit.state.get_tasks_by_ids(task_ids)
            # Missing bindings are unknown, not permission to evict resources.
            pinned = set(task_ids) - {task.id for task in tasks}
            pinned.update(task.id for task in tasks if task.status not in _IDLE)
            pinned.update(
                turn.task_id for turn in unit.assistant.nonterminal_turns_for_tasks(task_ids)
            )
        return frozenset(pinned)
