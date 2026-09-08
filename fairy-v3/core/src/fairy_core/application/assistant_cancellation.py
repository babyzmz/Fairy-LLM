from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast
from uuid import UUID

from pydantic import BaseModel

from fairy_core.assistant.models import AssistantTurnStatus, ToolInvocationStatus
from fairy_core.assistant.tools import ToolCancellationReceipt
from fairy_core.contracts.models import AssistantTurnCancelInput
from fairy_core.domain.errors import InvalidTransitionError, ProjectBusyError
from fairy_core.persistence.unit_of_work import CoreUnitOfWorkFactory


class AssistantCancellationMixin:
    """Coordinates cancellation without coupling transport endpoints to tool runtimes."""

    _assistant_ledger: Any
    _assistant_scheduler: Any
    _image_attachments: Any
    _tool_executor: Any
    _unit_of_work_factory: CoreUnitOfWorkFactory

    def _cancel_assistant_turn(self, request: BaseModel) -> Any:
        validated = cast(AssistantTurnCancelInput, request)
        return self._cancel_assistant_turn_by_id(
            validated.turn_id,
            expected_cancellation_revision=validated.expected_cancellation_revision,
        )

    def _cancel_assistant_turn_by_id(
        self,
        turn_id: UUID,
        *,
        expected_cancellation_revision: int,
        strict_tool_cancellation: bool = False,
    ) -> Any:
        if strict_tool_cancellation:
            return self._commit_assistant_cancellation(
                turn_id, expected_cancellation_revision=expected_cancellation_revision,
                strict_tool_cancellation=True,
            )
        with self._cancellation_cleanup.reserve() as defer:
            return self._commit_assistant_cancellation(
                turn_id, expected_cancellation_revision=expected_cancellation_revision,
                runtime_cleanup=defer,
            )

    def _commit_assistant_cancellation(
        self,
        turn_id: UUID,
        *,
        expected_cancellation_revision: int,
        strict_tool_cancellation: bool = False,
        runtime_cleanup: Callable[[Callable[[], None]], None] | None = None,
    ) -> Any:
        persisted = self._assistant_ledger.get_turn(turn_id)
        if persisted.status in {
            AssistantTurnStatus.COMPLETED,
            AssistantTurnStatus.FAILED,
        }:
            self._image_attachments.release(turn_id)
            return persisted
        try:
            if (
                strict_tool_cancellation
                and persisted.status is AssistantTurnStatus.CANCELLED
                and persisted.cancellation_revision == expected_cancellation_revision
            ):
                cancelled = persisted
            else:
                cancelled = self._assistant_ledger.cancel_turn(
                    turn_id=turn_id,
                    expected_cancellation_revision=expected_cancellation_revision,
                )
        except InvalidTransitionError:
            cancelled = self._assistant_ledger.get_turn(turn_id)
            if (
                persisted.status is AssistantTurnStatus.CANCELLED
                or cancelled.status is not AssistantTurnStatus.CANCELLED
                or cancelled.cancellation_revision != expected_cancellation_revision + 1
            ):
                raise
        # Commit the revision-checked request before touching a live worker or runtime.
        # A stale caller must not cancel the Workflow and only then receive a conflict.
        was_running = self._assistant_scheduler.cancel(turn_id)
        if was_running or strict_tool_cancellation:
            if runtime_cleanup is not None:
                runtime_cleanup(lambda: self._cancel_running_tool_command(turn_id))
            else:
                self._cancel_running_tool_command(turn_id, strict=strict_tool_cancellation)
        self._image_attachments.release(turn_id)
        projected = self._assistant_ledger.get_turn(turn_id)
        if strict_tool_cancellation and projected.cancellation_pending:
            raise ProjectBusyError("The previous operation is still stopping")
        return projected

    def _cancel_running_tool_command(self, turn_id: UUID, *, strict: bool = False) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            runs = tuple(
                run
                for invocation in unit_of_work.assistant.list_tool_invocations(turn_id)
                if invocation.status is ToolInvocationStatus.RUNNING
                and invocation.command_run_id is not None
                if (run := unit_of_work.commands.get_run(invocation.command_run_id)) is not None
            )
        if not runs:
            return
        cancel_command = getattr(self._tool_executor, "cancel_command", None)
        if not callable(cancel_command):
            if strict:
                raise ProjectBusyError("A running tool cannot be stopped safely")
            return
        for run in runs:
            try:
                receipt = cancel_command(run)
                if isinstance(receipt, ToolCancellationReceipt):
                    if receipt.command_id != run.id:
                        raise ValueError("Tool cancellation receipt belongs to another Command")
                    receipt.stopped.add_done_callback(
                        lambda stopped, command=run: (
                            self._assistant_application.settle_cancelled_domain_tool(command)
                            if not stopped.cancelled() and stopped.exception() is None else None
                        )
                    )
            except Exception as error:
                if strict:
                    raise ProjectBusyError("A running tool could not be stopped") from error


__all__ = ["AssistantCancellationMixin"]
