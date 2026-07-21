from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from fairy_core.contracts.common import ContractModel, ExecutionTarget


class BrowserProfileKind(StrEnum):
    PERSISTENT = "persistent"
    EPHEMERAL = "ephemeral"


class BrowserSessionStatus(StrEnum):
    STARTING = "starting"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    STOPPED = "stopped"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


class BrowserActionKind(StrEnum):
    NAVIGATE = "navigate"
    CLICK = "click"
    FILL = "fill"
    PRESS = "press"
    SELECT = "select"
    SCROLL = "scroll"
    WAIT = "wait"
    RELOAD = "reload"
    GO_BACK = "go_back"
    GO_FORWARD = "go_forward"


class BrowserWorkerHealthModel(ContractModel):
    available: bool
    browser_name: str = "Microsoft Edge"
    browser_version: str | None = None
    error_code: str | None = None
    diagnostic: str | None = None


class BrowserProfileModel(ContractModel):
    id: str = "fairy-default"
    kind: BrowserProfileKind = BrowserProfileKind.PERSISTENT
    configured: bool = True
    local_only: bool = True
    retention_days: int = Field(default=7, ge=1, le=90)


class BrowserTabModel(ContractModel):
    id: UUID
    session_id: UUID
    title: str = "New tab"
    url: str = "about:blank"
    active: bool = False
    loading: bool = False
    revision: int = Field(default=0, ge=0)


class BrowserSessionModel(ContractModel):
    id: UUID
    project_id: UUID | None = None
    conversation_id: UUID | None = None
    task_id: UUID | None = None
    execution_target: ExecutionTarget
    profile_kind: BrowserProfileKind
    status: BrowserSessionStatus
    active_tab_id: UUID | None = None
    tabs: tuple[BrowserTabModel, ...] = ()
    revision: int = Field(default=0, ge=0)
    created_at: datetime
    updated_at: datetime
    error_code: str | None = None
    public_error: str | None = None


class BrowserSessionStartInput(ContractModel):
    project_id: UUID | None = None
    conversation_id: UUID | None = None
    task_id: UUID | None = None
    execution_target: ExecutionTarget = ExecutionTarget.LOCAL
    profile_kind: BrowserProfileKind = BrowserProfileKind.PERSISTENT
    initial_url: str | None = Field(default=None, max_length=4096)
    idempotency_key: str = Field(min_length=1, max_length=255)


class BrowserSessionIdInput(ContractModel):
    session_id: UUID


class BrowserSessionListInput(ContractModel):
    conversation_id: UUID | None = None
    task_id: UUID | None = None
    exact_task_scope: bool = False
    include_terminal: bool = False


class BrowserSessionPageModel(ContractModel):
    items: tuple[BrowserSessionModel, ...]


class BrowserTabOpenInput(BrowserSessionIdInput):
    url: str = Field(default="about:blank", max_length=4096)


class BrowserTabIdInput(BrowserSessionIdInput):
    tab_id: UUID


class BrowserActionInput(BrowserTabIdInput):
    kind: BrowserActionKind
    selector: str | None = Field(default=None, max_length=2048)
    value: str | None = Field(default=None, max_length=32_000)
    x: float | None = None
    y: float | None = None
    delta_x: float | None = None
    delta_y: float | None = None
    expected_page_revision: int | None = Field(default=None, ge=0)
    idempotency_key: str = Field(min_length=1, max_length=255)


class BrowserActionResultModel(ContractModel):
    session: BrowserSessionModel
    tab: BrowserTabModel
    public_summary: str
    replayed: bool = False


class BrowserSnapshotInput(BrowserTabIdInput):
    include_screenshot: bool = True


class BrowserSnapshotModel(ContractModel):
    session_id: UUID
    tab_id: UUID
    page_revision: int = Field(ge=0)
    url: str
    title: str
    aria_snapshot: str = Field(max_length=200_000)
    viewport_width: int = Field(default=1365, ge=1, le=16_384)
    viewport_height: int = Field(default=768, ge=1, le=16_384)
    screenshot_data_url: str | None = None
    captured_at: datetime


__all__ = [
    "BrowserActionInput",
    "BrowserActionKind",
    "BrowserActionResultModel",
    "BrowserProfileKind",
    "BrowserProfileModel",
    "BrowserSessionIdInput",
    "BrowserSessionListInput",
    "BrowserSessionModel",
    "BrowserSessionPageModel",
    "BrowserSessionStartInput",
    "BrowserSessionStatus",
    "BrowserSnapshotInput",
    "BrowserSnapshotModel",
    "BrowserTabIdInput",
    "BrowserTabModel",
    "BrowserTabOpenInput",
    "BrowserWorkerHealthModel",
]
