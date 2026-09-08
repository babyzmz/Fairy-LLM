from uuid import UUID

from fairy_core.commanding import EventVisibility
from fairy_core.workflow.errors import WorkflowFenceError
from fairy_core.workflow.models import WorkflowRunStatus


class AssistantWorkflowFailureProjection:
    def __init__(self, application):
        self._application = application

    def recover(self, factory):
        recovered = 0
        while True:
            with factory() as unit:
                run_ids = unit.assistant.unsettled_failed_workflow_ids(limit=64)
                if not run_ids:
                    return recovered
                for run_id in run_ids:
                    self.settle(unit, unit.workflows.get_run(run_id))
                unit.commit()
                recovered += len(run_ids)

    def settle(self, unit, run):
        if run.status is not WorkflowRunStatus.FAILED or run.owner_kind != "assistant_turn":
            raise WorkflowFenceError("Assistant failure projection requires a failed owned Run")
        turn = unit.assistant.get_turn(UUID(run.owner_id))
        if (
            turn is None or turn.workflow_run_id != run.id
            or turn.execution_engine_version != run.engine_version
        ):
            raise WorkflowFenceError("Failed Workflow is not bound to this Assistant Turn")
        if turn.is_terminal:
            return
        error = run.error_code or "WORKFLOW_NODE_FAILED"
        self._application._fail_turn_in_unit(unit, turn.id, None, error_code=error)
        task = unit.state.get_task(turn.task_id)
        unit.commands.append_domain_event(
            event_type="assistant.turn.failed", visibility=EventVisibility.USER,
            message="Assistant workflow could not finish",
            payload={"turn_id": str(turn.id), "error_code": error},
            actor="core:workflow", conversation_id=turn.conversation_id, task_id=turn.task_id,
            project_id=task.project_id,
        )
