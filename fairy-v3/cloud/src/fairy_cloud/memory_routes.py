from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any
from uuid import UUID

from fairy_core.contracts.models import (
    MemoryClaimContextModel,
    MemoryClaimGetInput,
    MemoryClaimPageModel,
    MemoryClaimPromoteInput,
    MemoryClaimQuery,
    MemoryClaimResolveInput,
    MemoryClaimSupersedeInput,
    MemoryForgetInput,
    MemoryObservationModel,
    MemoryObservationPageModel,
    MemoryObservationQuery,
    MemoryObserveInput,
    MemoryProjectionHealthInput,
    MemoryProjectionHealthModel,
    MemoryProposalActionInput,
    MemoryProposalListInput,
    MemoryProposalModel,
    MemoryProposalPageModel,
    MemorySearchInput,
    MemorySearchPageModel,
    MemorySettingsModel,
    MemorySettingsUpdateInput,
    MemorySnapshotGetInput,
    MemorySnapshotModel,
    MemoryTombstoneModel,
)
from fairy_core.memory.models import MemoryNamespace
from fastapi import APIRouter, HTTPException, Query


def install_memory_routes(
    router: APIRouter,
    *,
    invoke: Callable[[str, dict[str, Any]], Any],
) -> None:
    @router.post(
        "/memory/observations",
        operation_id="memory.observations.create",
        response_model=MemoryObservationModel,
    )
    def create_memory_observation(request: MemoryObserveInput) -> dict[str, Any]:
        return invoke("memory.observations.create", request.model_dump(mode="json"))

    @router.get(
        "/memory/observations",
        operation_id="memory.observations.list",
        response_model=MemoryObservationPageModel,
    )
    def list_memory_observations(
        task_id: UUID,
        namespace: MemoryNamespace,
    ) -> dict[str, Any]:
        request = MemoryObservationQuery(task_id=task_id, namespace=namespace)
        return invoke("memory.observations.list", request.model_dump(mode="json"))

    @router.post(
        "/memory/claims/promote",
        operation_id="memory.claims.promote",
        response_model=MemoryClaimContextModel,
    )
    def promote_memory_claim(request: MemoryClaimPromoteInput) -> dict[str, Any]:
        return invoke("memory.claims.promote", request.model_dump(mode="json"))

    @router.get(
        "/memory/claims",
        operation_id="memory.claims.list",
        response_model=MemoryClaimPageModel,
    )
    def list_memory_claims(
        task_id: UUID,
        namespace: MemoryNamespace,
    ) -> dict[str, Any]:
        request = MemoryClaimQuery(task_id=task_id, namespace=namespace)
        return invoke("memory.claims.list", request.model_dump(mode="json"))

    @router.get(
        "/memory/claims/{claim_id}",
        operation_id="memory.claims.get",
        response_model=MemoryClaimContextModel,
    )
    def get_memory_claim(claim_id: UUID, task_id: UUID) -> dict[str, Any]:
        request = MemoryClaimGetInput(task_id=task_id, claim_id=claim_id)
        return invoke("memory.claims.get", request.model_dump(mode="json"))

    @router.post(
        "/memory/claims/{claim_id}/supersede",
        operation_id="memory.claims.supersede",
        response_model=MemoryClaimContextModel,
    )
    def supersede_memory_claim(
        claim_id: UUID,
        request: MemoryClaimSupersedeInput,
    ) -> dict[str, Any]:
        if request.claim_id != claim_id:
            raise HTTPException(
                status_code=409,
                detail={"code": "SCOPE_MISMATCH", "message": "claim id mismatch"},
            )
        return invoke("memory.claims.supersede", request.model_dump(mode="json"))

    @router.post(
        "/memory/claims/{claim_id}/resolve-conflict",
        operation_id="memory.claims.resolve_conflict",
        response_model=MemoryClaimContextModel,
    )
    def resolve_memory_conflict(
        claim_id: UUID,
        request: MemoryClaimResolveInput,
    ) -> dict[str, Any]:
        if request.claim_id != claim_id:
            raise HTTPException(
                status_code=409,
                detail={"code": "SCOPE_MISMATCH", "message": "claim id mismatch"},
            )
        return invoke(
            "memory.claims.resolve_conflict",
            request.model_dump(mode="json"),
        )

    @router.post(
        "/memory/forget",
        operation_id="memory.forget",
        response_model=MemoryTombstoneModel,
    )
    def forget_memory(request: MemoryForgetInput) -> dict[str, Any]:
        return invoke("memory.forget", request.model_dump(mode="json"))

    @router.get(
        "/memory/search",
        operation_id="memory.search",
        response_model=MemorySearchPageModel,
    )
    def search_memory(
        request: Annotated[MemorySearchInput, Query()],
    ) -> dict[str, Any]:
        return invoke("memory.search", request.model_dump(mode="json"))

    @router.get(
        "/memory/snapshots/{snapshot_id}",
        operation_id="memory.snapshots.get",
        response_model=MemorySnapshotModel,
    )
    def get_memory_snapshot(
        snapshot_id: UUID,
        request: Annotated[MemoryProjectionHealthInput, Query()],
    ) -> dict[str, Any]:
        payload = MemorySnapshotGetInput(
            task_id=request.task_id,
            snapshot_id=snapshot_id,
        )
        return invoke("memory.snapshots.get", payload.model_dump(mode="json"))

    @router.get(
        "/memory/projection/health",
        operation_id="memory.projection.health",
        response_model=MemoryProjectionHealthModel,
    )
    def get_memory_projection_health(
        request: Annotated[MemoryProjectionHealthInput, Query()],
    ) -> dict[str, Any]:
        return invoke("memory.projection.health", request.model_dump(mode="json"))

    @router.get(
        "/memory/settings",
        operation_id="memory.settings.get",
        response_model=MemorySettingsModel,
    )
    def get_memory_settings() -> dict[str, Any]:
        return invoke("memory.settings.get", {})

    @router.put(
        "/memory/settings",
        operation_id="memory.settings.update",
        response_model=MemorySettingsModel,
    )
    def update_memory_settings(request: MemorySettingsUpdateInput) -> dict[str, Any]:
        return invoke("memory.settings.update", request.model_dump(mode="json"))

    @router.get(
        "/memory/proposals",
        operation_id="memory.proposals.list",
        response_model=MemoryProposalPageModel,
    )
    def list_memory_proposals(
        request: Annotated[MemoryProposalListInput, Query()],
    ) -> dict[str, Any]:
        return invoke("memory.proposals.list", request.model_dump(mode="json"))

    @router.post(
        "/memory/proposals/{observation_id}/accept",
        operation_id="memory.proposals.accept",
        response_model=MemoryProposalModel,
    )
    def accept_memory_proposal(
        observation_id: UUID,
        request: MemoryProposalActionInput,
    ) -> dict[str, Any]:
        if request.observation_id != observation_id:
            raise HTTPException(
                status_code=409,
                detail={"code": "SCOPE_MISMATCH", "message": "observation id mismatch"},
            )
        return invoke("memory.proposals.accept", request.model_dump(mode="json"))

    @router.post(
        "/memory/proposals/{observation_id}/reject",
        operation_id="memory.proposals.reject",
        response_model=MemoryProposalModel,
    )
    def reject_memory_proposal(
        observation_id: UUID,
        request: MemoryProposalActionInput,
    ) -> dict[str, Any]:
        if request.observation_id != observation_id:
            raise HTTPException(
                status_code=409,
                detail={"code": "SCOPE_MISMATCH", "message": "observation id mismatch"},
            )
        return invoke("memory.proposals.reject", request.model_dump(mode="json"))
