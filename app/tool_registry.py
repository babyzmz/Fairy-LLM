from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


ToolHandler = Callable[..., dict[str, Any]]


@dataclass(slots=True)
class RegisteredTool:
    name: str
    description: str
    handler: ToolHandler


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, name: str, description: str, handler: ToolHandler) -> None:
        self._tools[name] = RegisteredTool(name=name, description=description, handler=handler)

    def has(self, name: str) -> bool:
        return name in self._tools

    def get(self, name: str) -> RegisteredTool:
        if name not in self._tools:
            raise KeyError(f"Tool not registered: {name}")
        return self._tools[name]

    def list_tools(self) -> list[str]:
        return sorted(self._tools)
