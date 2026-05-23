from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Callable


class CommandKind(str, Enum):
    CLIENT_ACTION = "client_action"
    REWRITE_MESSAGE = "rewrite_message"
    SERVER_ACTION = "server_action"


@dataclass(frozen=True, slots=True)
class CommandSpec:
    name: str
    aliases: tuple[str, ...]
    description: str
    usage: str
    kind: CommandKind
    rewrite_template: str | None = None
    client_action_type: str | None = None
    server_handler_name: str | None = None
    argument_label: str | None = None

    def all_names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["kind"] = self.kind.value
        return payload


ServerHandler = Callable[[dict], dict]


@dataclass(slots=True)
class CommandRegistry:
    specs: list[CommandSpec] = field(default_factory=list)
    server_handlers: dict[str, ServerHandler] = field(default_factory=dict)

    def register(self, spec: CommandSpec, server_handler: ServerHandler | None = None) -> None:
        self.specs.append(spec)
        if spec.kind == CommandKind.SERVER_ACTION:
            if not spec.server_handler_name or server_handler is None:
                raise ValueError(f"server_action command {spec.name} needs server_handler_name + handler")
            self.server_handlers[spec.server_handler_name] = server_handler

    def find(self, name: str) -> CommandSpec | None:
        candidate = name.strip()
        for spec in self.specs:
            if candidate in spec.all_names():
                return spec
        return None

    def list_specs(self) -> list[dict[str, object]]:
        return [spec.to_dict() for spec in self.specs]

    def execute_server(self, handler_name: str, args: dict | None = None) -> dict:
        handler = self.server_handlers.get(handler_name)
        if handler is None:
            raise KeyError(f"server handler not found: {handler_name}")
        return handler(args or {})


_singleton: CommandRegistry | None = None
_singleton_lock = threading.Lock()


def get_command_registry() -> CommandRegistry:
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = _build_default_registry()
        return _singleton


def _build_default_registry() -> CommandRegistry:
    from app.commands.builtins import register_builtins

    registry = CommandRegistry()
    register_builtins(registry)
    return registry
