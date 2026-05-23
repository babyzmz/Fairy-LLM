from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


BundleEventCallback = Callable[[str, dict[str, Any]], None]


@dataclass(slots=True)
class BundleRuntimeServices:
    llm: Any
    llm_helper: Any | None = None
    browser: Any | None = None
    documents: Any | None = None
    commands: Any | None = None
    screen: Any | None = None
    news: Any | None = None
    registry: Any | None = None
    web_runtime: Any | None = None
    search_tool: Callable[..., list[dict[str, Any]]] | None = None
    fetch_page: Callable[[str], str] | None = None
    browse_policy_callback: Callable[..., dict[str, Any]] | None = None
    event_callback: BundleEventCallback | None = None

    def emit(self, name: str, payload: dict[str, Any] | None = None) -> None:
        callback = self.event_callback
        if callback is None:
            return
        callback(name, dict(payload or {}))
