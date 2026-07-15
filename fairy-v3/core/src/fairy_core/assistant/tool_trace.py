from __future__ import annotations

from uuid import UUID

from fairy_core.assistant.models import AssistantTurn
from fairy_core.assistant.trace_models import TraceStep, TraceStepKind, TraceStepStatus
from fairy_core.assistant.trace_runtime import TurnTraceRuntime
from fairy_core.commanding import CommandRun


class ToolTraceCoordinator:
    def __init__(self, runtime: TurnTraceRuntime) -> None:
        self._runtime = runtime

    def start_in_unit(
        self,
        unit_of_work,
        *,
        turn: AssistantTurn,
        run: CommandRun,
        public_intent: str,
        tool_summary: str,
        approval_required: bool = False,
        failed: bool = False,
    ) -> TraceStep:
        reasoning = self._runtime.append_step_in_unit(
            unit_of_work,
            turn=turn,
            run=run,
            kind=TraceStepKind.REASONING,
            status=TraceStepStatus.SUCCEEDED,
            public_summary=public_intent,
        )
        tool_status = (
            TraceStepStatus.FAILED
            if failed
            else TraceStepStatus.WAITING
            if approval_required
            else TraceStepStatus.RUNNING
        )
        tool = self._runtime.append_step_in_unit(
            unit_of_work,
            turn=turn,
            run=run,
            kind=TraceStepKind.TOOL,
            status=tool_status,
            public_summary=tool_summary,
            parent_step_id=reasoning.id,
            caused_by_step_id=reasoning.id,
        )
        if approval_required:
            self._runtime.append_step_in_unit(
                unit_of_work,
                turn=turn,
                run=run,
                kind=TraceStepKind.APPROVAL,
                status=TraceStepStatus.WAITING,
                public_summary="Waiting for tool approval",
                parent_step_id=tool.id,
                caused_by_step_id=tool.id,
            )
        return tool

    def approve_in_unit(self, unit_of_work, *, run: CommandRun) -> None:
        self._runtime.transition_command_step_in_unit(
            unit_of_work,
            run=run,
            kind=TraceStepKind.APPROVAL,
            status=TraceStepStatus.SUCCEEDED,
            public_summary="Tool approved",
        )
        self._runtime.transition_command_step_in_unit(
            unit_of_work,
            run=run,
            kind=TraceStepKind.TOOL,
            status=TraceStepStatus.RUNNING,
            public_summary="Running approved tool",
        )

    def reject_in_unit(self, unit_of_work, *, run: CommandRun) -> None:
        self._runtime.transition_command_step_in_unit(
            unit_of_work,
            run=run,
            kind=TraceStepKind.APPROVAL,
            status=TraceStepStatus.CANCELLED,
            public_summary="Tool approval declined",
        )
        self._runtime.transition_command_step_in_unit(
            unit_of_work,
            run=run,
            kind=TraceStepKind.TOOL,
            status=TraceStepStatus.CANCELLED,
            public_summary="Tool call cancelled",
        )

    def fail_in_unit(
        self,
        unit_of_work,
        *,
        run: CommandRun,
        error_code: str,
    ) -> None:
        self._runtime.transition_command_step_in_unit(
            unit_of_work,
            run=run,
            kind=TraceStepKind.APPROVAL,
            status=TraceStepStatus.CANCELLED,
            public_summary="Tool approval ended",
        )
        self._runtime.transition_command_step_in_unit(
            unit_of_work,
            run=run,
            kind=TraceStepKind.TOOL,
            status=TraceStepStatus.FAILED,
            public_detail=f"Tool call failed ({error_code}).",
        )

    def cancel_in_unit(self, unit_of_work, *, run: CommandRun) -> None:
        self._runtime.transition_command_step_in_unit(
            unit_of_work,
            run=run,
            kind=TraceStepKind.APPROVAL,
            status=TraceStepStatus.CANCELLED,
            public_summary="Tool approval cancelled",
        )
        self._runtime.transition_command_step_in_unit(
            unit_of_work,
            run=run,
            kind=TraceStepKind.TOOL,
            status=TraceStepStatus.CANCELLED,
            public_summary="Tool call cancelled",
        )

    def complete_in_unit(
        self,
        unit_of_work,
        *,
        turn: AssistantTurn,
        run: CommandRun,
        public_summary: str,
        artifact_refs: tuple[UUID, ...],
    ) -> None:
        tool = self._runtime.transition_command_step_in_unit(
            unit_of_work,
            run=run,
            kind=TraceStepKind.TOOL,
            status=TraceStepStatus.SUCCEEDED,
            public_summary=public_summary,
            artifact_refs=artifact_refs,
        )
        observation = self._runtime.append_step_in_unit(
            unit_of_work,
            turn=turn,
            run=run,
            kind=TraceStepKind.OBSERVATION,
            status=TraceStepStatus.SUCCEEDED,
            public_summary=public_summary,
            parent_step_id=tool.id if tool is not None else None,
            caused_by_step_id=tool.id if tool is not None else None,
            artifact_refs=artifact_refs,
        )
        if artifact_refs:
            self._runtime.append_step_in_unit(
                unit_of_work,
                turn=turn,
                run=run,
                kind=TraceStepKind.ARTIFACT,
                status=TraceStepStatus.SUCCEEDED,
                public_summary=f"Created {len(artifact_refs)} artifact(s)",
                parent_step_id=observation.id,
                caused_by_step_id=observation.id,
                artifact_refs=artifact_refs,
            )


__all__ = ["ToolTraceCoordinator"]
