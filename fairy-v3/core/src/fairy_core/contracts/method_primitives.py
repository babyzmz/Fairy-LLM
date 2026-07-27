from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, Field

from fairy_core.contracts.models import ContractModel, EventEnvelopeModel


class EmptyInput(ContractModel):
    pass


class EventSubscribeInput(ContractModel):
    cursor: int = Field(default=0, ge=0)


class EventListInput(EventSubscribeInput):
    limit: int = Field(default=500, ge=1, le=2_000)


class EventPageModel(ContractModel):
    items: tuple[EventEnvelopeModel, ...]
    next_cursor: int = Field(ge=0)


class CoreMethodTransport(StrEnum):
    LOCAL_ONLY = "local_only"
    LOCAL_AND_CLOUD = "local_and_cloud"


@dataclass(frozen=True, slots=True)
class CoreMethod:
    name: str
    request_model: type[BaseModel]
    response_model: type[BaseModel]
    transport: CoreMethodTransport = CoreMethodTransport.LOCAL_AND_CLOUD


__all__ = [
    "CoreMethod",
    "CoreMethodTransport",
    "EmptyInput",
    "EventListInput",
    "EventPageModel",
    "EventSubscribeInput",
]
