import json
from dataclasses import replace
from uuid import UUID

from fairy_core.assistant.models import ToolInvocationStatus
from fairy_core.assistant.tools import ToolResult
from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.domain.execution import ChangesetStatus


def reconcile_changeset_receipt(unit, turn, result):
    """Publish the persisted domain outcome, not a proposal's earlier snapshot."""
    try:
        receipt = json.loads(result.model_content)
        changeset_id, approval_id = UUID(receipt["changeset_id"]), UUID(receipt["approval_id"])
    except (TypeError, ValueError, KeyError) as error:
        raise InvalidTransitionError(
            "Changeset tool receipt has no valid domain identity"
        ) from error
    changeset = unit.state.get_changeset_for_update(changeset_id)
    approval = unit.state.get_approval(approval_id)
    task = unit.state.get_task(turn.task_id)
    if (
        changeset is None
        or approval is None
        or task is None
        or changeset.task_id != turn.task_id
        or approval.task_id != turn.task_id
        or approval.changeset_id != changeset.id
        or changeset.conversation_id != turn.conversation_id
        or changeset.workspace_id != task.workspace_id
        or changeset.version_id != task.target_version_id
    ):
        raise InvalidTransitionError("Changeset receipt belongs to a different task scope")
    pending = changeset.status in {
        ChangesetStatus.PROPOSED,
        ChangesetStatus.AWAITING_APPROVAL,
        ChangesetStatus.APPLYING,
    }
    summary = {
        ChangesetStatus.PROPOSED: "File proposal is being prepared",
        ChangesetStatus.AWAITING_APPROVAL: "File proposal is awaiting approval",
        ChangesetStatus.APPLYING: "Approved files are still being applied",
        ChangesetStatus.APPLIED: f"Applied {len(changeset.files)} Workspace file(s)",
        ChangesetStatus.REJECTED: "File proposal was rejected; no files were applied",
        ChangesetStatus.FAILED: (
            "File application failed; inspect the recorded outcome before retrying"
        ),
        ChangesetStatus.ROLLED_BACK: "File application was rolled back",
    }[changeset.status]
    return replace(
        result,
        public_summary=summary,
        awaiting_approval=pending,
        model_content=json.dumps(
            {
                "changeset_id": str(changeset.id),
                "approval_id": str(approval.id),
                "status": changeset.status.value,
                "files": list(changeset.files),
            },
            separators=(",", ":"),
        ),
    ), changeset.status


def reconcile_failed_changeset_receipts(unit, turn, failed_ids, trace):
    """The proposal succeeded; its later approved application did not."""
    for invocation in unit.assistant.list_tool_invocations(turn.id):
        if (
            invocation.status is not ToolInvocationStatus.COMPLETED
            or invocation.tool_name != "edit.propose_changeset"
            or invocation.command_run_id is None
        ):
            continue
        try:
            changeset_id = UUID(json.loads(invocation.model_content)["changeset_id"])
        except (ValueError, TypeError, KeyError):
            continue
        if changeset_id not in failed_ids:
            continue
        result, _status = reconcile_changeset_receipt(unit, turn, ToolResult.create(
            public_summary=invocation.public_summary or "File proposal",
            model_content=invocation.model_content,
            artifact_ids=invocation.artifact_ids,
        ))
        invocation.revise_completed_result(
            public_summary=result.public_summary, model_content=result.model_content,
        )
        unit.assistant.update_tool_invocation(
            invocation, expected_status=ToolInvocationStatus.COMPLETED,
        )
        command = unit.commands.get_run(invocation.command_run_id)
        if command is None or command.scope_digest != turn.scope_digest:
            raise InvalidTransitionError("Failed file receipt has no matching proposal Command")
        approval_id = UUID(json.loads(result.model_content)["approval_id"])
        if unit.state.get_approval(approval_id).decision == "approved":
            trace.approve_in_unit(unit, run=command)
        trace.fail_in_unit(unit, run=command, error_code="CHANGESET_APPLY_FAILED")
