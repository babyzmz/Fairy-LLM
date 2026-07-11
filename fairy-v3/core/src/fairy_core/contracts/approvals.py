from __future__ import annotations

from uuid import UUID

from fairy_core.contracts.models import ContractModel, VersionListInput


class ApprovalListInput(VersionListInput):
    pass


class ApprovalDecisionInput(ContractModel):
    approval_id: UUID
    approved: bool


__all__ = ["ApprovalDecisionInput", "ApprovalListInput"]
