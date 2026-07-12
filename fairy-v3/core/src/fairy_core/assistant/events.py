from __future__ import annotations

from fairy_core.assistant.models import Message
from fairy_core.commanding import CommandRun, EventVisibility
from fairy_core.commanding.ports import CommandLedger


def append_message_created(
    commands: CommandLedger,
    *,
    run: CommandRun,
    message: Message,
) -> None:
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
        lease_owner=run.lease_owner,
        lease_fence=run.lease_fence,
    )


__all__ = ["append_message_created"]
