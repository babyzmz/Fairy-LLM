from __future__ import annotations

from fairy_core.assistant.models import Message
from fairy_core.commanding import CommandRun, CommandStatus, EventVisibility
from fairy_core.commanding.ports import CommandLedger


def append_message_created(
    commands: CommandLedger,
    *,
    run: CommandRun,
    message: Message,
) -> None:
    lease_owner = run.lease_owner if run.status is CommandStatus.RUNNING else None
    lease_fence = run.lease_fence if run.status is CommandStatus.RUNNING else None
    commands.append_event(
        run_id=run.id,
        event_type="message.created",
        visibility=EventVisibility.USER,
        message=f"{message.role.value.replace('_', ' ').title()} message created",
        payload={
            "turn_id": str(message.turn_id),
            "message_id": str(message.id),
            "role": message.role.value,
        },
        lease_owner=lease_owner,
        lease_fence=lease_fence,
    )


__all__ = ["append_message_created"]
