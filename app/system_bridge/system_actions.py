from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


SystemActionName = Literal[
    "cancel_current_request",
    "restart_backend",
    "clear_asset_cache",
    "refresh_capabilities",
]


@dataclass(slots=True)
class SystemActionResult:
    action: SystemActionName
    ok: bool
    message: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
