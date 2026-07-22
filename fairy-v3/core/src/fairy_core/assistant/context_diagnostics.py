from __future__ import annotations

from uuid import UUID

from fairy_core.assistant.durable_context import ToolContextProjection
from fairy_core.commanding import CommandRun, EventVisibility


class AssistantContextDiagnosticsMixin:
    def _record_context_projection(
        self,
        *,
        turn_id: UUID,
        run: CommandRun,
        model_round: int,
        context,
        tool_context: ToolContextProjection,
    ) -> None:
        diagnostics = context.diagnostics
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.commands.append_event(
                run_id=run.id,
                event_type="model.context.prepared",
                visibility=EventVisibility.DEVELOPER,
                message="Bounded model context prepared",
                payload={
                    "turn_id": str(turn_id),
                    "model_round": model_round,
                    "memory_source_characters": diagnostics.memory_source_characters,
                    "memory_projected_characters": diagnostics.memory_projected_characters,
                    "knowledge_source_characters": diagnostics.knowledge_source_characters,
                    "knowledge_projected_characters": diagnostics.knowledge_projected_characters,
                    "tool_source_characters": tool_context.source_characters,
                    "tool_projected_characters": tool_context.projected_characters,
                    "tool_truncated_items": tool_context.truncated_items,
                    "manifest_tool_definitions": diagnostics.manifest_tool_definitions,
                    "offered_tool_definitions": len(context.tool_definitions),
                    "completion_handoff": diagnostics.completion_handoff,
                },
                lease_owner=run.lease_owner,
                lease_fence=run.lease_fence,
            )
            unit_of_work.commit()

    def _record_suppressed_tool_candidates(
        self,
        *,
        turn_id: UUID,
        run: CommandRun,
        model_round: int,
        count: int,
    ) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.commands.append_event(
                run_id=run.id,
                event_type="model.tool_candidates.deduplicated",
                visibility=EventVisibility.DEVELOPER,
                message="Equivalent tool requests deduplicated",
                payload={
                    "turn_id": str(turn_id),
                    "model_round": model_round,
                    "suppressed_count": count,
                },
                lease_owner=run.lease_owner,
                lease_fence=run.lease_fence,
            )
            unit_of_work.commit()


__all__ = ["AssistantContextDiagnosticsMixin"]
