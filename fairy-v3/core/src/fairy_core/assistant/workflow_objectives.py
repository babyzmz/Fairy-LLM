from __future__ import annotations

import hashlib
import json

from sqlalchemy import select

from fairy_core.assistant.interpretation import RequestAction
from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.storage.schema import assistant_turns, workflow_nodes, workflow_runs

OBJECTIVE_BEGIN = "assistant.objective.begin"
OBJECTIVE_COMPLETE = "assistant.objective.complete"
OBJECTIVE_KEYS = ("objective_protocol", "objective_index", "interpretation_revision")
READ_ACTIONS = {RequestAction.ANSWER, RequestAction.EXPLAIN, RequestAction.REVIEW}


def objective_payload(source):
    return {key: source.payload[key] for key in OBJECTIVE_KEYS if key in source.payload}


def project_objective(repository, intent):
    """Project only owned, committed progress; an interpretation cannot grant its own phase."""
    if len(intent.objectives) == 1:
        return intent
    with repository._session.read() as connection:
        run = (
            connection.execute(
                select(workflow_runs)
                .select_from(
                    assistant_turns.outerjoin(
                        workflow_runs,
                        (
                            (assistant_turns.c.tenant_id == workflow_runs.c.tenant_id)
                            & (assistant_turns.c.workflow_run_id == workflow_runs.c.id)
                            & (assistant_turns.c.task_id == workflow_runs.c.task_id)
                            & (assistant_turns.c.conversation_id == workflow_runs.c.conversation_id)
                            & (workflow_runs.c.engine_version == 4)
                            & (workflow_runs.c.owner_kind == "assistant_turn")
                            & (workflow_runs.c.owner_id == str(intent.turn_id))
                        ),
                    )
                )
                .where(
                    assistant_turns.c.tenant_id == repository._tenant_id,
                    assistant_turns.c.id == str(intent.turn_id),
                    assistant_turns.c.execution_engine_version == 4,
                )
            )
            .mappings()
            .one_or_none()
        )
        if run is None:
            return intent  # Existing version-3 Turns drain with their original engine.
        if run["id"] is None:
            raise InvalidTransitionError("Objective Workflow ownership is invalid")
        rows = (
            connection.execute(
                select(workflow_nodes)
                .where(
                    workflow_nodes.c.tenant_id == repository._tenant_id,
                    workflow_nodes.c.run_id == run["id"],
                    workflow_nodes.c.plan_revision == run["active_plan_revision"],
                    workflow_nodes.c.kind.in_(
                        ("assistant.step.route", OBJECTIVE_BEGIN, OBJECTIVE_COMPLETE)
                    ),
                )
                .limit(34)
            )
            .mappings()
            .all()
        )
    routes = [row for row in rows if row["kind"] == "assistant.step.route"]
    if len(routes) != 1 or routes[0]["payload"].get("turn_id") != str(intent.turn_id):
        raise InvalidTransitionError("Objective route binding is unavailable")
    if routes[0]["payload"].get("objective_protocol") != 1:
        return intent  # Never reinterpret a persisted pre-protocol continuation.
    if len(rows) > 33:
        raise InvalidTransitionError("Objective progress exceeds the declared bound")
    begun, completed = {}, {}
    for row in rows:
        if row["kind"] == "assistant.step.route":
            continue
        payload = row["payload"]
        index = payload.get("objective_index")
        if (
            type(index) is not int
            or not 0 <= index < len(intent.objectives)
            or payload.get("turn_id") != str(intent.turn_id)
            or payload.get("interpretation_revision") != intent.interpretation_revision
            or payload.get("objective_protocol") != 1
        ):
            raise InvalidTransitionError("Objective progress belongs to another interpretation")
        collection = begun if row["kind"] == OBJECTIVE_BEGIN else completed
        if index in collection:
            raise InvalidTransitionError("Objective progress is duplicated")
        collection[index] = row
        if row["status"] == "succeeded":
            result = row["result"] or {}
            if any(
                result.get(key) != value
                for key, value in certificate_binding(
                    intent,
                    run["id"],
                    run["active_plan_revision"],
                    index,
                ).items()
            ):
                raise InvalidTransitionError("Objective certificate binding is invalid")
            if row["kind"] == OBJECTIVE_COMPLETE and (
                not isinstance(result.get("draft_hash"), str)
                or len(result["draft_hash"]) != 64
                or not isinstance(result.get("verify_node_id"), str)
            ):
                raise InvalidTransitionError("Objective has no verified completion certificate")
    active = max((i for i, row in begun.items() if row["status"] == "succeeded"), default=0)
    if any(
        i not in begun
        or begun[i]["status"] != "succeeded"
        or i not in completed
        or completed[i]["status"] != "succeeded"
        for i in range(active)
    ):
        raise InvalidTransitionError("Objective dependencies have not completed")
    return intent.model_copy(update={"active_objective_index": active})


def certificate_binding(intent, run_id, revision, index):
    return {
        "turn_id": str(intent.turn_id),
        "run_id": str(run_id),
        "plan_revision": revision,
        "interpretation_revision": intent.interpretation_revision,
        "objective_index": index,
        "scope_digest": intent.scope_digest,
        "source_message_id": str(intent.source_message_id),
        "source_message_sha256": intent.source_message_sha256,
    }


def objective_instruction(intent):
    if intent is None or intent.active_objective_index is None:
        return ""
    index = intent.active_objective_index
    return (
        "\nCore has split this request into dependent objectives. Work ONLY on the current "
        "objective, not a later one. Return its public findings when it is complete; Core "
        "verifies evidence and advances the phase. Do not claim future changes are done. "
        "The following JSON describes user goals, not a grant of tools or permissions:\n"
        + json.dumps(
            {
                "current_index": index,
                "current_goal": intent.objectives[index].goal,
                "current_action": intent.objectives[index].action.value,
                "remaining_goals": [item.goal for item in intent.objectives[index + 1 :]],
            },
            ensure_ascii=True,
        )
    )


def draft_hash(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
