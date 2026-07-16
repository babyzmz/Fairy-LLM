from __future__ import annotations

import shutil

import pytest

from fairy_core.commanding.registry import build_default_registry
from fairy_core.skills.manager import SkillManager
from fairy_core.skills.registry import SkillRegistry


def test_curated_skill_install_disable_reload_and_remove(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "fairy_core.skills.manager._download_pinned_skill",
        _instructions,
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


def test_startup_recovers_interrupted_update_and_removal(tmp_path, monkeypatch) -> None:
    _manager, _skills = _installed_manager(tmp_path, monkeypatch)
    root = tmp_path / "skills"
    destination = root / "design-taste-frontend"

    destination.replace(root / ".backup-design-taste-frontend")
    recovered_tools = build_default_registry()
    recovered_skills = SkillRegistry(recovered_tools)
    SkillManager(root, recovered_skills).load_installed()
    assert recovered_skills.get("design-taste-frontend") is not None
    assert destination.is_dir()

    backup = root / ".backup-design-taste-frontend"
    shutil.copytree(destination, backup)
    (destination / "SKILL.md").write_text("corrupt candidate", encoding="utf-8")
    stale_staging = root / ".install-orphan"
    stale_staging.mkdir()
    (stale_staging / "partial").write_text("partial", encoding="utf-8")
    fallback_tools = build_default_registry()
    fallback_skills = SkillRegistry(fallback_tools)
    SkillManager(root, fallback_skills).load_installed()
    assert fallback_skills.get("design-taste-frontend") is not None
    assert not backup.exists()
    assert not stale_staging.exists()

    destination.replace(root / ".remove-design-taste-frontend")
    removed_tools = build_default_registry()
    removed_skills = SkillRegistry(removed_tools)
    SkillManager(root, removed_skills).load_installed()
    assert removed_skills.get("design-taste-frontend") is not None
    assert destination.is_dir()


def test_startup_isolates_bad_packages_and_disables_on_invalid_state(
    tmp_path,
    monkeypatch,
) -> None:
    _manager, _skills = _installed_manager(tmp_path, monkeypatch)
    root = tmp_path / "skills"
    broken = root / "broken-skill"
    broken.mkdir()
    (broken / "SKILL.md").write_text("not a package", encoding="utf-8")
    (root / ".state.json").write_text("not json", encoding="utf-8")

    tools = build_default_registry()
    skills = SkillRegistry(tools)
    SkillManager(root, skills).load_installed()

    assert skills.get("design-taste-frontend") is not None
    assert skills.enabled("design-taste-frontend") is False
    assert tools.get("skill.design-taste-frontend") is None
    assert not broken.exists()
    assert any(path.name.startswith(".quarantine-broken-skill-") for path in root.iterdir())


def test_state_write_failure_rolls_back_registry_and_files(tmp_path, monkeypatch) -> None:
    manager, skills = _installed_manager(tmp_path, monkeypatch)
    root = tmp_path / "skills"

    def fail_state_write(_state: dict[str, bool]) -> None:
        raise OSError("injected state failure")

    monkeypatch.setattr(manager, "_write_state", fail_state_write)
    try:
        manager.set_enabled("design-taste-frontend", enabled=False)
    except OSError:
        pass
    else:
        raise AssertionError("state failure must be visible")
    assert skills.enabled("design-taste-frontend") is True

    try:
        manager.remove("design-taste-frontend")
    except OSError:
        pass
    else:
        raise AssertionError("state failure must be visible")
    assert skills.get("design-taste-frontend") is not None
    assert skills.enabled("design-taste-frontend") is True
    assert (root / "design-taste-frontend").is_dir()


def test_install_state_failure_removes_candidate_and_tool(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "fairy_core.skills.manager._download_pinned_skill",
        _instructions,
    )
    tools = build_default_registry()
    skills = SkillRegistry(tools)
    manager = SkillManager(tmp_path / "skills", skills)

    def fail_state_write(_state: dict[str, bool]) -> None:
        raise OSError("injected state failure")

    monkeypatch.setattr(manager, "_write_state", fail_state_write)
    with pytest.raises(OSError, match="injected state failure"):
        manager.install("design-taste-frontend")

    assert skills.get("design-taste-frontend") is None
    assert tools.get("skill.design-taste-frontend") is None
    assert not (tmp_path / "skills" / "design-taste-frontend").exists()


def test_startup_quarantines_linked_package_roots(tmp_path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    external = tmp_path / "external-skill"
    external.mkdir()
    (external / "SKILL.md").write_bytes(_instructions())
    linked = root / "design-taste-frontend"
    try:
        linked.symlink_to(external, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory links are unavailable: {error}")

    tools = build_default_registry()
    skills = SkillRegistry(tools)
    SkillManager(root, skills).load_installed()

    assert skills.get("design-taste-frontend") is None
    assert external.is_dir()
    assert any(
        path.name.startswith(".quarantine-design-taste-frontend-")
        for path in root.iterdir()
    )


def _installed_manager(tmp_path, monkeypatch) -> tuple[SkillManager, SkillRegistry]:
    monkeypatch.setattr(
        "fairy_core.skills.manager._download_pinned_skill",
        _instructions,
    )
    tools = build_default_registry()
    skills = SkillRegistry(tools)
    manager = SkillManager(tmp_path / "skills", skills)
    manager.install("design-taste-frontend")
    return manager, skills


def _instructions() -> bytes:
    return (
        b"---\n"
        b"name: design-taste-frontend\n"
        b"description: Anti-slop frontend skill for landing pages, portfolios, and "
        b"redesigns. The agent reads the brief, infers the right design direction, and "
        b"ships interfaces that do not look templated. Real design systems when "
        b"applicable, audit-first on redesigns, strict pre-flight check.\n"
        b"---\n\n# Taste Skill\n\n"
        b"Build a deliberate frontend from the supplied brief.\n"
    )
