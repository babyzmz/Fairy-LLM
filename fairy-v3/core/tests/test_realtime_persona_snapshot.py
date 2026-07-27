from __future__ import annotations

import json
from dataclasses import asdict, replace

import pytest

from fairy_core.persona import (
    RealtimeActivityProfile,
    RealtimeInteractionIntensity,
    load_default_persona_authority,
    project_realtime_persona_snapshot,
)


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def test_realtime_snapshot_projects_the_canonical_fairy_authority() -> None:
    authority = load_default_persona_authority()

    snapshot = project_realtime_persona_snapshot(
        authority=authority,
        locale="zh-CN",
        activity_profile=RealtimeActivityProfile.GAME,
        interaction_intensity=RealtimeInteractionIntensity.STANDARD,
        current_goal="Finish Phase 0",
        subject_title=None,
        recent_progress=None,
    )

    assert snapshot.identity.name == "Fairy"
    assert snapshot.persona_digest == authority.digest
    assert snapshot.authority_version == authority.version
    assert snapshot.relationship.user_has_final_authority is True
    assert snapshot.realtime_policy.grounding_required is True
    assert snapshot.realtime_policy.never_claim_unobserved_action is True
    assert "system_prompt" not in asdict(snapshot)
    assert "hidden_reasoning" not in asdict(snapshot)


def test_realtime_snapshot_is_deterministic_for_the_same_inputs() -> None:
    authority = load_default_persona_authority()
    inputs = {
        "authority": authority,
        "locale": "en",
        "activity_profile": RealtimeActivityProfile.FOCUS,
        "interaction_intensity": RealtimeInteractionIntensity.QUIET,
        "current_goal": None,
        "subject_title": "Editor",
        "recent_progress": "Tests are passing.",
    }

    first = project_realtime_persona_snapshot(**inputs)
    second = project_realtime_persona_snapshot(**inputs)

    assert canonical_json(asdict(first)) == canonical_json(asdict(second))


def test_realtime_snapshot_rejects_unknown_locale_and_unbounded_short_memory() -> None:
    authority = load_default_persona_authority()
    common = {
        "authority": authority,
        "activity_profile": RealtimeActivityProfile.AUTO,
        "interaction_intensity": RealtimeInteractionIntensity.ACTIVE,
        "subject_title": None,
        "recent_progress": None,
    }

    with pytest.raises(ValueError, match="locale"):
        project_realtime_persona_snapshot(
            **common,
            locale="fr",
            current_goal=None,
        )

    with pytest.raises(ValueError, match="current_goal"):
        project_realtime_persona_snapshot(
            **common,
            locale="zh-CN",
            current_goal="x" * 501,
        )


def test_realtime_snapshot_rejects_noncanonical_identity() -> None:
    authority = load_default_persona_authority()
    changed = replace(
        authority,
        canonical_identity=replace(authority.canonical_identity, codename="Other"),
    )

    with pytest.raises(ValueError, match="Fairy"):
        project_realtime_persona_snapshot(
            authority=changed,
            locale="zh-CN",
            activity_profile=RealtimeActivityProfile.AUTO,
            interaction_intensity=RealtimeInteractionIntensity.STANDARD,
            current_goal=None,
            subject_title=None,
            recent_progress=None,
        )
