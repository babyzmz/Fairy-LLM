from __future__ import annotations

from dataclasses import replace
from uuid import uuid5

from fairy_core.workflow.models import WorkflowNode

STEP_ROUTE = "assistant.step.route"
STEP_MODEL = "assistant.step.model"
STEP_REVIEW = "assistant.step.review"
STEP_VERIFY = "assistant.step.verify"
STEP_FINALIZE = "assistant.step.finalize"


def step_node(source: WorkflowNode, kind: str, **payload: object) -> WorkflowNode:
    node = WorkflowNode.create(
        run_id=source.run_id, plan_revision=source.plan_revision,
        node_key=f"{source.id}:{kind}", kind=kind,
        payload={"turn_id": source.payload["turn_id"], **payload},
        public_summary={
            STEP_MODEL: "Running one model round",
            STEP_REVIEW: "Reviewing the candidate response",
            STEP_VERIFY: "Verifying the response contract",
            STEP_FINALIZE: "Finalizing the unique response",
        }[kind],
        resource_keys=(f"assistant-turn:{source.payload['turn_id']}",),
        max_attempts=128,
    )
    return replace(node, id=uuid5(source.id, kind))
