from __future__ import annotations

from typing import Never, Protocol


class AssistantModelYield(Exception):
    """A durable node boundary, not a provider failure or Turn cancellation."""


class AssistantModelBoundary(Protocol):
    model_round: int
    usage: dict[str, int]
    chunk_index: int
    feedback: tuple[str, ...]
    invalid_tool_retry_used: bool

    def tools(self, **payload: object) -> Never: ...

    def draft(self, **payload: object) -> Never: ...

    def retry(self, **payload: object) -> Never: ...
