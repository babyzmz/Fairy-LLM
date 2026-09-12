from __future__ import annotations

from uuid import UUID

from fairy_core.application.recoverable_command import start_recoverable_core_command
from fairy_core.commanding import CommandStatus


class CoreScratchCheckpointMixin:
    def checkpoint_scratch_task(self, task_id: UUID) -> None:
        """Commit a validated scratch candidate without promoting it to the active Version."""
        with self._transaction() as (unit_of_work, commands):
            task = self._require_task(unit_of_work.state, task_id)
            if task.project_id is not None:
                raise ValueError("only scratch tasks use automatic candidate checkpoints")
            if task.workspace_id is None or task.target_version_id is None:
                raise ValueError("scratch Task has no writable Workspace Version")
            context = self._context_for(unit_of_work.state, task)
            plan = unit_of_work.state.execution_plan_for_task(task.id)
            checkpoint_key = f"task:{task.id}:scratch-checkpoint"
            if plan is not None and plan.generation > 1:
                checkpoint_key += f":plan:{plan.id}"
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
                idempotency_key=checkpoint_key,
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
