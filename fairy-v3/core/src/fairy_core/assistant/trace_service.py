from __future__ import annotations

from typing import Any, cast

from pydantic import BaseModel

from fairy_core.assistant.trace_models import TurnTrace
from fairy_core.contracts.models import AssistantTurnIdInput
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory


class TurnTraceService:
    def __init__(self, unit_of_work_factory: CoreUnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    @property
    def handlers(self) -> dict[str, Any]:
        return {"assistant.turns.trace.list": self.get_trace}

    def get_trace(self, request: BaseModel) -> dict[str, object]:
        turn_id = cast(AssistantTurnIdInput, request).turn_id
        with self._unit_of_work_factory() as unit_of_work:
            turn = unit_of_work.assistant.get_turn(turn_id)
            if turn is None:
                raise KeyError(f"Assistant Turn not found: {turn_id}")
            trace = unit_of_work.assistant.get_trace_by_turn_id(turn_id)
            if trace is None:
                trace, inserted = unit_of_work.assistant.create_trace_if_absent(
                    TurnTrace.create(
                        turn_id=turn.id,
                        conversation_id=turn.conversation_id,
                        task_id=turn.task_id,
                        legacy=True,
                    )
                )
                if inserted:
                    unit_of_work.commit()
            steps = unit_of_work.assistant.list_trace_steps(turn_id)
            receipts_by_id = {
                receipt.id: receipt
                for invocation in unit_of_work.assistant.list_tool_invocations(turn_id)
                for receipt in invocation.evidence_receipts
            }
            evidence_sources = tuple(
                receipts_by_id[receipt_id]
                for receipt_id in turn.cited_evidence_receipt_ids
                if receipt_id in receipts_by_id
            )
        return {
            "id": trace.id,
            "turn_id": trace.turn_id,
            "conversation_id": trace.conversation_id,
            "task_id": trace.task_id,
            "legacy": trace.legacy,
            "last_sequence": trace.last_sequence,
            "revision": trace.revision,
            "created_at": trace.created_at,
            "updated_at": trace.updated_at,
            "started_at": trace.started_at,
            "completed_at": trace.completed_at,
            "steps": tuple(_step_model(step) for step in steps),
            "evidence_sources": tuple(_evidence_source(receipt) for receipt in evidence_sources),
        }


def _step_model(step) -> dict[str, object]:
    return {
        "id": step.id,
        "trace_id": step.trace_id,
        "turn_id": step.turn_id,
        "sequence": step.sequence,
        "parent_step_id": step.parent_step_id,
        "caused_by_step_id": step.caused_by_step_id,
        "kind": step.kind,
        "status": step.status,
        "public_summary": step.public_summary,
        "public_detail": step.public_detail,
        "model_id": step.model_id,
        "model_role": step.model_role,
        "provider_attempt_id": step.provider_attempt_id,
        "command_run_id": step.command_run_id,
        "artifact_refs": step.artifact_refs,
        "visibility": step.visibility,
        "revision": step.revision,
        "created_at": step.created_at,
        "updated_at": step.updated_at,
        "started_at": step.started_at,
        "completed_at": step.completed_at,
        "duration_ms": step.duration_ms,
    }


def _evidence_source(receipt) -> dict[str, object]:
    return {
        "id": receipt.id,
        "source_kind": receipt.source_kind,
        "public_label": receipt.public_label,
        "tool_name": receipt.tool_name,
        "workspace_id": receipt.workspace_id if receipt.relative_path is not None else None,
        "version_id": receipt.version_id if receipt.relative_path is not None else None,
        "relative_path": receipt.relative_path,
        "line_start": receipt.line_start,
        "line_end": receipt.line_end,
        "safe_url": receipt.safe_url,
        "observed_at": receipt.observed_at,
        "expires_at": receipt.expires_at,
        "truncated": receipt.truncated,
    }


__all__ = ["TurnTraceService"]
