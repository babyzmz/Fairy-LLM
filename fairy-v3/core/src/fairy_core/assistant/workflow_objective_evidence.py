from sqlalchemy import select

from fairy_core.assistant.evidence import EvidenceRequirementKind
from fairy_core.storage.schema import workflow_nodes


def read_objective_issue(unit, turn, intent, plan, invocations):
    needs_files = turn.routing_decision is not None and (
        turn.routing_decision.requires_workspace_changes
    )
    if not needs_files:
        return None
    required_paths = {
        item["path"]
        for item in (plan.manifest.get("files", []) if plan else [])
        if item.get("expected_hash")
    }
    if intent.project_id is None and not required_paths:
        return None  # An empty scratch task can be analyzed from its source request.
    run = unit.workflows.get_run(turn.workflow_run_id)
    repository = unit.assistant
    with repository._session.read() as connection:
        payloads = (
            connection.execute(
                select(workflow_nodes.c.payload)
                .where(
                    workflow_nodes.c.tenant_id == repository._tenant_id,
                    workflow_nodes.c.run_id == str(run.id),
                    workflow_nodes.c.plan_revision == run.active_plan_revision,
                    workflow_nodes.c.kind == "assistant.step.tool",
                    workflow_nodes.c.status == "succeeded",
                )
                .limit(97)
            )
            .scalars()
            .all()
        )
    invocation_ids = {
        item["invocation_id"]
        for item in payloads
        if item.get("objective_index") == intent.active_objective_index
        and item.get("interpretation_revision") == intent.interpretation_revision
    }
    paths = {
        receipt.relative_path
        for invocation in invocations
        if str(invocation.id) in invocation_ids and invocation.status == "completed"
        for receipt in invocation.evidence_receipts
        if receipt.requirement_kind == EvidenceRequirementKind.WORKSPACE_CONTENT
        and receipt.scope_digest == intent.scope_digest
        and receipt.task_id == intent.task_id
        and receipt.turn_id == intent.turn_id
        and receipt.version_id == intent.target_version_id
        and not receipt.is_expired
    }
    if not paths or not required_paths.issubset(paths):
        return (
            "The analysis objective needs actual current file contents before the change phase. "
            "Read the relevant existing files in this objective; a directory listing, file plan, "
            "or an earlier phase's receipts do not verify current contents."
        )
    return None
