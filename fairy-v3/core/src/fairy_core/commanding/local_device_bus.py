from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from fairy_core.commanding.registry import SideEffect, ToolRegistry
from fairy_core.commanding.types import PermissionProfile

_Result = TypeVar("_Result")


@dataclass(frozen=True, slots=True)
class LocalDeviceCommandRequest:
    command_name: str
    actor: str
    payload_digest: str
    idempotency_key: str

    def __post_init__(self) -> None:
        if self.actor != "core:ambient_dialogue":
            raise ValueError("local device command actor is not authorized")
        if len(self.payload_digest) != 64 or any(
            character not in "0123456789abcdef" for character in self.payload_digest
        ):
            raise ValueError("local device command payload digest must be SHA-256")
        if not self.idempotency_key.startswith("ambient-dialogue:"):
            raise ValueError("local device command idempotency key is out of scope")


class LocalDeviceCommandBus:
    """Policy gate for private, non-durable device commands.

    This bus deliberately has no Ledger adapter: ambient context and generated text must
    remain process-local. Only an input digest crosses the command boundary.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def execute(
        self,
        request: LocalDeviceCommandRequest,
        handler: Callable[[], _Result],
    ) -> _Result:
        definition = self._registry.get(request.command_name)
        if (
            request.command_name != "model.generate"
            or definition is None
            or definition.model_visible
            or not definition.idempotent
            or definition.side_effect is not SideEffect.READ
            or PermissionProfile.OBSERVE not in definition.profiles
        ):
            raise PermissionError("local device command is not allowed")
        return handler()


__all__ = ["LocalDeviceCommandBus", "LocalDeviceCommandRequest"]
