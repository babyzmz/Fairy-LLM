from __future__ import annotations

import json
import os
import shutil
import zipfile
from pathlib import Path

import pytest

from fairy_core.commanding.registry import build_default_registry
from fairy_core.skills.manager import GSAP_SKILL_SPECS, SkillManager
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


def test_catalog_exposes_and_installs_official_gsap_skills(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "fairy_core.skills.manager._download_pinned_skill",
        _instructions,
    )
    tools = build_default_registry()
    skills = SkillRegistry(tools)
    manager = SkillManager(tmp_path / "skills", skills)

    entries = {entry.extension_id: entry for entry in manager.catalog()}
    expected = {spec.entry.extension_id for spec in GSAP_SKILL_SPECS}
    assert expected == {
        "gsap-core",
        "gsap-frameworks",
        "gsap-performance",
        "gsap-plugins",
        "gsap-react",
        "gsap-scrolltrigger",
        "gsap-timeline",
        "gsap-utils",
    }
    assert expected.issubset(entries)
    assert all(entries[name].publisher == "GreenSock" for name in expected)
    assert all(entries[name].source.endswith(f"/skills/{name}") for name in expected)
    assert entries["gsap-scrolltrigger"].name == "GSAP ScrollTrigger"

    manager.install("gsap-scrolltrigger")

    package = skills.get("gsap-scrolltrigger")
    assert package is not None
    assert package.manifest.provenance.publisher == "GreenSock"
    assert tools.get("skill.gsap-scrolltrigger") is not None


def test_catalog_exposes_reviewed_mcp_presets(tmp_path) -> None:
    manager = SkillManager(tmp_path / "skills", SkillRegistry(build_default_registry()))

    entries = {entry.extension_id: entry for entry in manager.catalog()}

    assert {"context7", "github", "playwright"}.issubset(entries)
    assert entries["playwright"].kind == "mcp_preset"
    assert entries["playwright"].trust == "verified_publisher"
    assert manager.mcp_preset("playwright").arguments[1] == "@playwright/mcp@0.0.78"


def test_external_folder_is_copied_inspected_and_installed(tmp_path) -> None:
    source = tmp_path / "external-skill"
    source.mkdir()
    source.joinpath("SKILL.md").write_text(
        "---\nname: external-skill\ndescription: A local read-only design helper.\n---\n\n"
        "# External Skill\n\nReview the supplied brief.\n",
        encoding="utf-8",
    )
    tools = build_default_registry()
    skills = SkillRegistry(tools)
    manager = SkillManager(tmp_path / "managed" / "skills", skills)

    pending = manager.inspect_import("folder", str(source))
    source.joinpath("unrelated.txt").write_text("changed after copy", encoding="utf-8")
    manager.install_import(
        pending.token,
        name="external-skill",
        version="0.1.0",
        description="A local read-only design helper.",
        publisher="Local user",
        license_name="Private",
        input_schema={},
        required_capabilities=(),
        compatible_mcp_servers=(),
    )

    assert skills.get("external-skill") is not None
    assert tools.get("skill.external-skill") is not None
    assert not (tmp_path / "managed" / "skills" / "external-skill" / "unrelated.txt").exists()


def test_external_zip_rejects_path_traversal(tmp_path) -> None:
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("../escaped.txt", "unsafe")
        package.writestr(
            "skill/SKILL.md",
            "---\nname: skill\ndescription: Unsafe package.\n---\n",
        )
    manager = SkillManager(tmp_path / "skills", SkillRegistry(build_default_registry()))

    with pytest.raises(ValueError, match="unsafe path"):
        manager.inspect_import("zip", str(archive))

    assert not (tmp_path / "escaped.txt").exists()


@pytest.mark.parametrize("member_name", ["skill/payload.txt:secret", "skill/CON.txt"])
def test_external_zip_rejects_windows_special_paths(tmp_path, member_name) -> None:
    archive = tmp_path / "windows-special.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr(member_name, "unsafe")
        package.writestr(
            "skill/SKILL.md",
            "---\nname: skill\ndescription: Unsafe package.\n---\n",
        )
    manager = SkillManager(tmp_path / "skills", SkillRegistry(build_default_registry()))

    with pytest.raises(ValueError, match="unsafe path"):
        manager.inspect_import("zip", str(archive))


def test_skill_creator_uses_the_same_governed_package_loader(tmp_path) -> None:
    tools = build_default_registry()
    skills = SkillRegistry(tools)
    manager = SkillManager(tmp_path / "skills", skills)

    manager.create(
        name="fairy-review",
        version="0.1.0",
        description="Review a Fairy interface against a supplied brief.",
        instructions="# Review\n\nInspect the interface and report actionable findings.",
        publisher="Fairy user",
        license_name="Private",
        input_schema={},
        required_capabilities=(),
        compatible_mcp_servers=("playwright",),
    )

    package = skills.get("fairy-review")
    assert package is not None
    assert package.manifest.provenance.source == "fairy://created/fairy-review"
    assert package.manifest.compatible_mcp_servers == ("playwright",)


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
        path.name.startswith(".quarantine-design-taste-frontend-") for path in root.iterdir()
    )


def test_startup_skips_inaccessible_package_when_quarantine_is_denied(
    tmp_path,
    monkeypatch,
) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    broken = root / "broken-skill"
    broken.mkdir()
    (broken / "SKILL.md").write_text("not a package", encoding="utf-8")
    state_path = root / ".state.json"
    state_path.write_text('{"broken-skill": true}\n', encoding="utf-8")
    real_replace = os.replace

    def deny_quarantine(source: str | Path, destination: str | Path) -> None:
        if Path(source) == broken:
            raise PermissionError("injected quarantine denial")
        real_replace(source, destination)

    monkeypatch.setattr("fairy_core.skills.manager.os.replace", deny_quarantine)
    tools = build_default_registry()
    skills = SkillRegistry(tools)
    manager = SkillManager(root, skills)

    def deny_package_access(_path: Path) -> None:
        raise PermissionError("injected package access denial")

    monkeypatch.setattr(manager._loader, "load", deny_package_access)

    manager.load_installed()

    assert skills.get("broken-skill") is None
    assert broken.is_dir()
    assert json.loads(state_path.read_text(encoding="utf-8")) == {}


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


def _instructions(spec=None) -> bytes:
    name = spec.entry.extension_id if spec is not None else "design-taste-frontend"
    description = (
        spec.entry.description
        if spec is not None
        else (
            "Anti-slop frontend skill for landing pages, portfolios, and redesigns. "
            "The agent reads the brief, infers the right design direction, and ships "
            "interfaces that do not look templated. Real design systems when applicable, "
            "audit-first on redesigns, strict pre-flight check."
        )
    )
    return (
        b"---\n"
        + f"name: {name}\n".encode()
        + f"description: {description}\n".encode()
        + b"---\n\n# Test Skill\n\n"
        + b"Build from the supplied brief.\n"
    )
