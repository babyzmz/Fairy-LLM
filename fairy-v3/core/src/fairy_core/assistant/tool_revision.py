from __future__ import annotations

from fairy_core.domain.errors import ScopeViolationError


def active_tool_revision(unit, turn):
    """Resolve trusted ownership, never accept revision metadata from model arguments."""
    if turn.execution_engine_version != 4:
        return None, 1
    run = unit.workflows.get_run(turn.workflow_run_id) if turn.workflow_run_id else None
    if (
        run is None
        or run.engine_version != 4
        or run.task_id != turn.task_id
        or run.owner_id != str(turn.id)
        or run.owner_kind != "assistant_turn"
        or run.conversation_id != turn.conversation_id
    ):
        raise ScopeViolationError("Tool Invocation has no matching Assistant Workflow")
    return run.id, run.active_plan_revision


def tool_command_key(invocation):
    prefix = f"assistant:{invocation.turn_id}:tool:"
    # First-revision keys stay compatible with pre-migration queued commands.
    if invocation.workflow_plan_revision > 1:
        prefix += f"revision:{invocation.workflow_plan_revision}:"
    return prefix + invocation.argument_hash
