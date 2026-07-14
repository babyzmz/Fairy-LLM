from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RenderBrokerRequest:
    pack_id: str
    pack_version: str
    input_path: Path
    output_directory: Path
    timeout_seconds: int
    max_output_bytes: int


@dataclass(frozen=True, slots=True)
class RenderBrokerResult:
    exit_code: int
    output_paths: tuple[Path, ...]
    public_summary: str


class RendererBroker(Protocol):
    """Native broker boundary. Implementations must enforce OS sandboxing."""

    def is_healthy(self) -> bool: ...

    def execute(self, request: RenderBrokerRequest) -> RenderBrokerResult: ...


class UnavailableRendererBroker:
    def is_healthy(self) -> bool:
        return False

    def execute(self, request: RenderBrokerRequest) -> RenderBrokerResult:
        del request
        raise RuntimeError("RENDERER_UNAVAILABLE: native restricted renderer broker is unavailable")


__all__ = [
    "RenderBrokerRequest",
    "RenderBrokerResult",
    "RendererBroker",
    "UnavailableRendererBroker",
]
