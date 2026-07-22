from __future__ import annotations

from fairy_core.persona import load_default_persona_authority


def test_default_persona_is_fixed_versioned_and_safe_for_all_scenarios() -> None:
    authority = load_default_persona_authority()

    assert authority.version == "1.0.0"
    assert len(authority.digest) == 64
    assert authority.canonical_identity.codename == "Fairy"
    assert "Ⅲ型总序式集成泛用人工智能" in authority.system_prompt
    assert "Do not expose hidden chain of thought" in authority.system_prompt
    assert "medical, legal, financial, security" in authority.system_prompt
    assert "high_risk" in authority.scenario_policy
    assert authority.prohibited_drift.allow_persona_selector is False


def test_persona_digest_is_stable_across_loads() -> None:
    first = load_default_persona_authority()
    second = load_default_persona_authority()

    assert first.digest == second.digest
    assert first.system_prompt == second.system_prompt
