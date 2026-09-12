from sqlalchemy import select

from fairy_core.assistant.evidence import EvidenceRequirementKind
from fairy_core.storage.schema import workflow_nodes


def objective_operation_receipts(unit, turn, intent, invocations):
    """Only this revision/objective's successful, governed operations are facts."""
    if intent is None or intent.active_objective_index is None or turn.workflow_run_id is None:
        return ()
    run = unit.workflows.get_run(turn.workflow_run_id)
    if (
        run is None
        or run.engine_version != 4
        or run.task_id != turn.task_id
        or run.conversation_id != turn.conversation_id
    ):
        return ()
    return tuple(
        invocation
        for invocation in invocations
        if invocation.workflow_run_id == run.id
        and invocation.workflow_plan_revision == run.active_plan_revision
        and invocation.workflow_objective_index == intent.active_objective_index
        and invocation.turn_id == turn.id
        and invocation.task_id == turn.task_id
        and invocation.scope_digest == intent.scope_digest
        and invocation.status == "completed"
        and invocation.error_code is None
        and invocation.command_run_id is not None
        and (command := unit.commands.get_run(invocation.command_run_id)) is not None
        and command.status == "succeeded"
        and command.command_name == invocation.tool_name
        and command.task_id == turn.task_id
        and command.conversation_id == turn.conversation_id
        and command.scope_digest == intent.scope_digest
    )


def operation_objective_issue(unit, turn, intent, invocations):
    if intent is None or intent.active_objective_index is None:
        return None
    action = intent.objectives[intent.active_objective_index].action.value
    required = {
        "run": {"run.sandboxed", "review.test", "review.typecheck", "review.lint", "review.build"},
        "generate": {"media.images.generate", "media.audio.generate", "media.videos.start"},
        "manage": {
            "memory.suggest",
            "system.notify",
            "system.copy_text",
            "system.reveal_path",
            "system.open_settings",
            "system.open_url",
        },
    }.get(action)
    receipts = objective_operation_receipts(unit, turn, intent, invocations)
    explicit_mcp = {name for name in intent.target_descriptions if name.startswith("mcp.")}
    if action in {"create", "change", "run", "manage"} and explicit_mcp:
        required = (required or set()) | explicit_mcp
    if action == "browse":
        # Reading/snapshotting is sufficient; do not force a click or a form side effect.
        if any(
            item.tool_name.startswith("browser.")
            and item.tool_name
            not in {
                "browser.status",
                "browser.close",
                "browser.tabs.list",
            }
            for item in receipts
        ):
            return None
        required = {"browser.snapshot", "browser.navigate"}
    if required is not None and not any(item.tool_name in required for item in receipts):
        return (
            f"Objective execution is unverified ({action}): no successful governed operation "
            "is recorded for this objective and revision. Perform the authorized operation "
            "using its tool, or surface the actual blocker; never claim completion from a "
            "draft, an earlier objective, a failed call or an unapproved proposal."
        )
    return None


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
