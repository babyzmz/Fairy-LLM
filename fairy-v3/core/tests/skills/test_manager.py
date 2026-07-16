from __future__ import annotations

from fairy_core.commanding.registry import build_default_registry
from fairy_core.skills.manager import SkillManager
from fairy_core.skills.registry import SkillRegistry


def test_curated_skill_install_disable_reload_and_remove(tmp_path, monkeypatch) -> None:
    instructions = (
        b"---\n"
        b"name: design-taste-frontend\n"
        b"description: Anti-slop frontend skill for landing pages, portfolios, and "
        b"redesigns. The agent reads the brief, infers the right design direction, and "
        b"ships interfaces that do not look templated. Real design systems when "
        b"applicable, audit-first on redesigns, strict pre-flight check.\n"
        b"---\n\n# Taste Skill\n\n"
        b"Build a deliberate frontend from the supplied brief.\n"
    )
    monkeypatch.setattr(
        "fairy_core.skills.manager._download_pinned_skill",
        lambda: instructions,
    )
    tools = build_default_registry()
    skills = SkillRegistry(tools)
    manager = SkillManager(tmp_path / "skills", skills)

    manager.install("design-taste-frontend")

    package = skills.get("design-taste-frontend")
    assert package is not None
    assert skills.enabled("design-taste-frontend") is True
    assert tools.get("skill.design-taste-frontend") is not None

    manager.set_enabled("design-taste-frontend", enabled=False)
    assert tools.get("skill.design-taste-frontend") is None

    reloaded_tools = build_default_registry()
    reloaded_skills = SkillRegistry(reloaded_tools)
    reloaded = SkillManager(tmp_path / "skills", reloaded_skills)
    reloaded.load_installed()
    assert reloaded_skills.enabled("design-taste-frontend") is False

    reloaded.remove("design-taste-frontend")
    assert reloaded_skills.get("design-taste-frontend") is None
    assert not (tmp_path / "skills" / "design-taste-frontend").exists()
