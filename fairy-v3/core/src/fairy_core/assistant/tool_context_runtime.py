from __future__ import annotations

import json
from uuid import UUID

from fairy_core.assistant.candidates import ToolCandidate
from fairy_core.assistant.durable_context import durable_tool_context
from fairy_core.assistant.models import Message, MessageRole, MessageVisibility
from fairy_core.assistant.tools import tool_message_content
from fairy_core.assistant.turn_reader import require_task, require_turn
from fairy_core.domain.models import TaskStatus
from fairy_core.providers import ModelMessage, ModelToolCall


class AssistantToolContextMixin:
    def _durable_tool_context(self, turn_id: UUID) -> tuple[ModelMessage, ...]:
        with self._unit_of_work_factory() as unit_of_work:
            invocations = unit_of_work.assistant.list_tool_invocations(turn_id)
        return durable_tool_context(invocations)

    def _changeset_approval_state(self, turn_id: UUID) -> str:
        with self._unit_of_work_factory() as unit_of_work:
            turn = require_turn(unit_of_work, turn_id)
            task = require_task(unit_of_work, turn.task_id)
        if task.status is TaskStatus.AWAITING_APPROVAL:
            return "waiting"
        if task.status is TaskStatus.REJECTED:
            return "rejected"
        return "ready"

    def _next_model_round(self, turn_id: UUID) -> int:
        with self._unit_of_work_factory() as unit_of_work:
            invocations = unit_of_work.assistant.list_tool_invocations(turn_id)
        return max((invocation.model_round for invocation in invocations), default=0) + 1

    @staticmethod
    def _model_tool_call(candidate: ToolCandidate) -> ModelToolCall:
        arguments = candidate.arguments()
        return ModelToolCall.create(
            tool_call_id=candidate.call_id,
            name=candidate.name or "unknown",
            arguments=json.dumps(
                arguments,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )

    def _append_tool_message(
        self,
        *,
        turn_id: UUID,
        tool_name: str,
        tool_call_id: str,
        content: str,
        rejected: bool,
    ) -> Message:
        with self._unit_of_work_factory() as unit_of_work:
            message = self._append_tool_message_in_unit(
                unit_of_work,
                turn_id=turn_id,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                content=content,
                rejected=rejected,
            )
            unit_of_work.commit()
        return message

    @staticmethod
    def _append_tool_message_in_unit(
        unit_of_work,
        *,
        turn_id: UUID,
        tool_name: str,
        tool_call_id: str,
        content: str,
        rejected: bool,
    ) -> Message:
        turn = require_turn(unit_of_work, turn_id)
        message = Message.create(
            conversation_id=turn.conversation_id,
            task_id=turn.task_id,
            turn_id=turn.id,
            sequence=unit_of_work.assistant.next_message_sequence(turn.conversation_id),
            role=MessageRole.TOOL,
            visibility=MessageVisibility.DEVELOPER,
            content=tool_message_content(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                content=content,
                rejected=rejected,
            ),
        )
        unit_of_work.assistant.append_message(message)
        return message
