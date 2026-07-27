from __future__ import annotations

import json
from hashlib import sha256

from fairy_core.commanding import EventVisibility
from fairy_core.domain.errors import InvalidTransitionError
from fairy_core.memory.models import (
    MemoryAuthority,
    MemoryClaim,
    MemoryClaimRevision,
    MemoryNamespace,
    MemorySensitivity,
)
from fairy_core.memory.policy import MemoryPolicy
from fairy_core.persistence.unit_of_work import CoreUnitOfWork
from fairy_core.realtime.models import (
    CompanionDigestActivity,
    CompanionSessionDigest,
    RealtimeCaptionSpeaker,
    RealtimeMemoryProposal,
    RealtimeMemoryProposalDecision,
    RealtimeMemoryProposalKind,
    RealtimeMemoryProposalStatus,
    RealtimeTranscriptEntry,
)

REALTIME_MEMORY_ACTOR = "core:realtime-memory-policy"
USER_MEMORY_ACTOR = "user:local"


def build_memory_proposals(
    *,
    digest: CompanionSessionDigest,
    entries: tuple[RealtimeTranscriptEntry, ...],
    device_id: str,
    unit_of_work: CoreUnitOfWork,
) -> tuple[RealtimeMemoryProposal, ...]:
    policy = MemoryPolicy()
    proposals: list[RealtimeMemoryProposal] = []
    for entry in entries:
        if entry.speaker is not RealtimeCaptionSpeaker.USER:
            continue
        evidence_digest = sha256(
            json.dumps(
                {
                    "session_id": str(entry.session_id),
                    "sequence": entry.sequence,
                    "speaker": entry.speaker.value,
                    "text": entry.text,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        for kind, target_namespace, subject, predicate, sensitivity in _classify_caption(
            digest,
            entry.text,
            evidence_digest,
        ):
            scan = policy.scan_content(entry.text, sensitivity=sensitivity)
            if not scan.allowed:
                continue
            auto_eligible = kind in {
                RealtimeMemoryProposalKind.GAME_PROGRESS,
                RealtimeMemoryProposalKind.NEXT_GOAL,
                RealtimeMemoryProposalKind.EXPLICIT_PREFERENCE,
            }
            existing = _matching_claim(
                unit_of_work,
                namespace=target_namespace,
                device_id=device_id,
                subject=subject,
                predicate=predicate,
            )
            conflict = existing is not None and _claim_normalized_text(
                unit_of_work, existing
            ) != _truncate(entry.text, 2_000)
            decision = (
                RealtimeMemoryProposalDecision.AUTO_PROMOTE
                if auto_eligible and not conflict
                else RealtimeMemoryProposalDecision.REQUIRES_CONFIRMATION
            )
            reason = (
                "explicit_low_risk_stable_caption"
                if decision is RealtimeMemoryProposalDecision.AUTO_PROMOTE
                else (
                    "existing_claim_conflict"
                    if conflict
                    else "inferred_or_sensitive_requires_confirmation"
                )
            )
            proposals.append(
                RealtimeMemoryProposal.create(
                    digest_id=digest.id,
                    session_id=digest.session_id,
                    conversation_id=digest.conversation_id,
                    kind=kind,
                    subject=subject,
                    predicate=predicate,
                    value=entry.text,
                    normalized_text=_truncate(entry.text, 2_000),
                    target_namespace=target_namespace,
                    confidence=1.0 if auto_eligible else 0.6,
                    sensitivity=sensitivity,
                    source_sequence=entry.sequence,
                    evidence_digest=evidence_digest,
                    policy_decision=decision,
                    policy_reason=reason,
                )
            )
            if len(proposals) >= 12:
                return tuple(proposals)
    return tuple(proposals)


def promote_memory_proposal(
    *,
    unit_of_work: CoreUnitOfWork,
    proposal: RealtimeMemoryProposal,
    device_id: str,
    decision_idempotency_key: str,
    explicit_user: bool,
) -> RealtimeMemoryProposal:
    if proposal.status is RealtimeMemoryProposalStatus.PROMOTED:
        if proposal.decision_idempotency_key == decision_idempotency_key:
            return proposal
        raise InvalidTransitionError("realtime memory proposal is already promoted")
    if proposal.status is not RealtimeMemoryProposalStatus.PENDING:
        raise InvalidTransitionError("only pending realtime memory proposals can be promoted")
    existing = _matching_claim(
        unit_of_work,
        namespace=proposal.target_namespace,
        device_id=device_id,
        subject=proposal.subject,
        predicate=proposal.predicate,
    )
    current_text = _claim_normalized_text(unit_of_work, existing) if existing is not None else None
    if not explicit_user and existing is not None and current_text != proposal.normalized_text:
        raise InvalidTransitionError("automatic realtime memory cannot replace an existing Claim")
    actor = USER_MEMORY_ACTOR if explicit_user else REALTIME_MEMORY_ACTOR
    event = unit_of_work.commands.append_domain_event(
        event_type=(
            "realtime.memory.accepted" if explicit_user else "realtime.memory.auto_promoted"
        ),
        visibility=EventVisibility.USER,
        message=(
            "Realtime memory proposal accepted"
            if explicit_user
            else "Explicit low-risk realtime memory saved"
        ),
        payload={
            "proposal_id": str(proposal.id),
            "digest_id": str(proposal.digest_id),
            "session_id": str(proposal.session_id),
            "kind": proposal.kind.value,
            "target_namespace": proposal.target_namespace.value,
            "policy_reason": proposal.policy_reason,
        },
        actor=actor,
        conversation_id=proposal.conversation_id,
    )
    claim = existing
    if claim is None:
        claim = MemoryClaim.create(
            namespace=proposal.target_namespace,
            device_id=(
                device_id if proposal.target_namespace is MemoryNamespace.DEVICE_LOCAL else None
            ),
            subject=proposal.subject,
            predicate=proposal.predicate,
        )
        claim = unit_of_work.memory.create_claim(
            claim,
            request_fingerprint=_stage_fingerprint(
                proposal.id,
                "claim",
            ),
        )
    if current_text != proposal.normalized_text:
        current_revision = claim.current_revision
        revision = MemoryClaimRevision.create(
            claim_id=claim.id,
            revision=current_revision + 1,
            value=proposal.value,
            normalized_text=proposal.normalized_text,
            source_observation_ids=(),
            source_event_ids=(event.id,),
            authority=(
                MemoryAuthority.EXPLICIT_USER
                if explicit_user
                else MemoryAuthority.DETERMINISTIC_CORE
            ),
            confidence=proposal.confidence,
            actor=actor,
            supersedes_revision=current_revision or None,
        )
        claim = unit_of_work.memory.append_revision(
            claim.id,
            expected_revision=current_revision,
            revision=revision,
            request_fingerprint=_stage_fingerprint(
                proposal.id,
                f"revision:{decision_idempotency_key}",
            ),
        )
    promoted = proposal.promote(
        claim_id=claim.id,
        decision_idempotency_key=decision_idempotency_key,
    )
    return unit_of_work.realtime.update_memory_proposal(
        promoted,
        expected_revision=proposal.revision,
    )


def _classify_caption(
    digest: CompanionSessionDigest,
    text: str,
    evidence_digest: str,
) -> tuple[
    tuple[
        RealtimeMemoryProposalKind,
        MemoryNamespace,
        str,
        str,
        MemorySensitivity,
    ],
    ...,
]:
    normalized = text.casefold()
    candidates: list[
        tuple[
            RealtimeMemoryProposalKind,
            MemoryNamespace,
            str,
            str,
            MemorySensitivity,
        ]
    ] = []
    if any(marker in normalized for marker in ("remember ", "remember:", "记住", "请记得")):
        candidates.append(
            (
                RealtimeMemoryProposalKind.EXPLICIT_PREFERENCE,
                MemoryNamespace.USER_PROFILE,
                "user",
                f"realtime_preference:{evidence_digest[:12]}",
                MemorySensitivity.PRIVATE,
            )
        )
    if any(marker in normalized for marker in ("next", "goal", "接下来", "下一步", "下次", "目标")):
        candidates.append(
            (
                RealtimeMemoryProposalKind.NEXT_GOAL,
                MemoryNamespace.DEVICE_LOCAL,
                f"activity:{digest.activity.value}",
                "next_goal",
                MemorySensitivity.PRIVATE,
            )
        )
    if digest.activity is CompanionDigestActivity.GAME and any(
        marker in normalized
        for marker in ("complete", "finished", "passed", "success", "完成", "通过", "成功")
    ):
        candidates.append(
            (
                RealtimeMemoryProposalKind.GAME_PROGRESS,
                MemoryNamespace.DEVICE_LOCAL,
                f"game:{digest.subject_title or 'unknown'}",
                "progress",
                MemorySensitivity.PUBLIC,
            )
        )
    if not candidates and any(
        marker in normalized
        for marker in (
            "i usually",
            "i prefer",
            "i feel",
            "i am ",
            "我通常",
            "我喜欢",
            "我感觉",
            "我是",
        )
    ):
        candidates.append(
            (
                RealtimeMemoryProposalKind.INFERRED_FACT,
                MemoryNamespace.USER_PROFILE,
                "user",
                f"inferred_fact:{evidence_digest[:12]}",
                MemorySensitivity.PRIVATE,
            )
        )
    return tuple(candidates)


def _matching_claim(
    unit_of_work: CoreUnitOfWork,
    *,
    namespace: MemoryNamespace,
    device_id: str,
    subject: str,
    predicate: str,
) -> MemoryClaim | None:
    claims = unit_of_work.memory.claims_for_scope(
        namespace=namespace,
        device_id=device_id if namespace is MemoryNamespace.DEVICE_LOCAL else None,
    )
    return next(
        (claim for claim in claims if claim.subject == subject and claim.predicate == predicate),
        None,
    )


def _claim_normalized_text(
    unit_of_work: CoreUnitOfWork,
    claim: MemoryClaim,
) -> str:
    revisions = unit_of_work.memory.revisions_for_claim(claim.id)
    if not revisions:
        raise ValueError("active Hermes Claim has no current revision")
    return revisions[-1].normalized_text


def _stage_fingerprint(proposal_id: object, stage: str) -> str:
    return sha256(f"realtime-memory:{proposal_id}:{stage}".encode()).hexdigest()


def _truncate(value: str, maximum: int) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= maximum:
        return normalized
    return normalized[: maximum - 1].rstrip() + "…"


__all__ = [
    "USER_MEMORY_ACTOR",
    "build_memory_proposals",
    "promote_memory_proposal",
]
