from __future__ import annotations

import json
from pathlib import Path

import pytest

from fairy_core.skills.loader import SkillPackageError, SkillPackageLoader
from tests.skills.support import write_skill


def test_versioned_skill_manifest_loads_bounded_agent_skill_instructions(tmp_path: Path) -> None:
    package_path = write_skill(tmp_path)

    package = SkillPackageLoader().load(package_path)

    assert package.manifest.name == "release-notes"
    assert package.manifest.version == "1.0.0"
    assert package.manifest.tool_name == "skill.release-notes"
    assert package.manifest.required_capabilities == ("project.read",)
    assert package.manifest.provenance.publisher == "Fairy"
    assert package.instructions.startswith("---\nname: release-notes")
    assert package.content_sha256 == package.manifest.content_sha256


def test_skill_manifest_rejects_content_tampering_and_name_mismatch(tmp_path: Path) -> None:
    package_path = write_skill(tmp_path)
    (package_path / "SKILL.md").write_text("tampered", encoding="utf-8")

    with pytest.raises(SkillPackageError, match="digest"):
        SkillPackageLoader().load(package_path)

    package_path = write_skill(tmp_path / "second", name="triage")
    manifest_path = package_path / "fairy-skill.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["name"] = "different-name"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(SkillPackageError, match="directory"):
        SkillPackageLoader().load(package_path)


def test_skill_package_rejects_traversal_scripts_and_oversized_instructions(
    tmp_path: Path,
) -> None:
    package_path = write_skill(tmp_path)
    manifest_path = package_path / "fairy-skill.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["instructions"] = "../outside.md"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(SkillPackageError, match="instructions"):
        SkillPackageLoader().load(package_path)

    scripted = write_skill(tmp_path / "scripted", name="scripted")
    scripts = scripted / "scripts"
    scripts.mkdir()
    (scripts / "run.py").write_text("print('unsafe')", encoding="utf-8")

    with pytest.raises(SkillPackageError, match="executable"):
        SkillPackageLoader().load(scripted)

    large = write_skill(tmp_path / "large", name="large")
    (large / "SKILL.md").write_text("x" * 70_000, encoding="utf-8")

    with pytest.raises(SkillPackageError, match="too large"):
        SkillPackageLoader().load(large)


def test_skill_manifest_rejects_open_deep_and_scope_bearing_schemas(tmp_path: Path) -> None:
    for index, schema in enumerate(
        (
            {"type": "object", "additionalProperties": True},
            {
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "nested": {
                        "type": "array",
                        "items": {
                            "type": "array",
                            "items": {
                                "type": "array",
                                "items": {
                                    "type": "array",
                                    "items": {
                                        "type": "array",
                                        "items": {
                                            "type": "array",
                                            "items": {
                                                "type": "array",
                                                "items": {
                                                    "type": "array",
                                                    "items": {"type": "string"},
                                                },
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    }
                },
                "additionalProperties": False,
            },
        )
    ):
        package_path = write_skill(tmp_path / str(index), name=f"invalid-{index}")
        manifest_path = package_path / "fairy-skill.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["input_schema"] = schema
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with pytest.raises(SkillPackageError, match=r"schema|reserved|nesting"):
            SkillPackageLoader().load(package_path)
