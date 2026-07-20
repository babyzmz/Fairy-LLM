from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from fairy_core.skills.models import SkillManifest, SkillPackage

_MANIFEST = "fairy-skill.json"
_MAX_INSTRUCTION_BYTES = 128 * 1024
_MAX_PACKAGE_BYTES = 512 * 1024
_MAX_FILES = 64
_EXECUTABLE_SUFFIXES = frozenset({".bat", ".cmd", ".com", ".exe", ".ps1", ".sh"})
_SKILL_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


class SkillPackageError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SkillPackageInspection:
    name: str
    description: str
    instructions: str
    manifest: SkillManifest | None
    file_count: int
    content_bytes: int


def package_content_digest(root: Path) -> str:
    package_root = root.resolve(strict=True)
    contents = _package_contents(package_root, include_manifest=False)
    return _content_digest(package_root, contents)


def _content_digest(root: Path, contents: dict[Path, bytes]) -> str:
    digest = hashlib.sha256()
    for path, content in contents.items():
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


class SkillPackageLoader:
    def inspect(self, root: Path) -> SkillPackageInspection:
        if root.is_symlink() or (hasattr(root, "is_junction") and root.is_junction()):
            raise SkillPackageError("Skill package root cannot be a link")
        try:
            package_root = root.resolve(strict=True)
        except OSError as error:
            raise SkillPackageError("Skill package could not be accessed") from error
        if not package_root.is_dir():
            raise SkillPackageError("Skill package must be a directory")
        contents = _package_contents(package_root, include_manifest=True)
        files = tuple(contents)
        _reject_executables(package_root, files)
        instructions_path = package_root / "SKILL.md"
        if instructions_path not in files:
            raise SkillPackageError("Skill package requires SKILL.md")
        instructions = _decode_instructions(contents[instructions_path])
        frontmatter = _agent_skill_frontmatter(instructions)
        name = frontmatter.get("name")
        description = frontmatter.get("description")
        if not isinstance(name, str) or _SKILL_NAME.fullmatch(name) is None or "--" in name:
            raise SkillPackageError("SKILL.md name is invalid")
        if not isinstance(description, str) or not description.strip() or len(description) > 1_024:
            raise SkillPackageError("SKILL.md description is invalid")
        manifest_path = package_root / _MANIFEST
        manifest = self.load(package_root).manifest if manifest_path in files else None
        return SkillPackageInspection(
            name=name,
            description=description,
            instructions=instructions,
            manifest=manifest,
            file_count=len(files),
            content_bytes=sum(len(content) for content in contents.values()),
        )

    def load(self, root: Path) -> SkillPackage:
        if root.is_symlink() or (hasattr(root, "is_junction") and root.is_junction()):
            raise SkillPackageError("Skill package root cannot be a link")
        try:
            package_root = root.resolve(strict=True)
        except OSError as error:
            raise SkillPackageError("Skill package could not be accessed") from error
        if not package_root.is_dir():
            raise SkillPackageError("Skill package must be a directory")
        contents = _package_contents(package_root, include_manifest=True)
        files = tuple(contents)
        _reject_executables(package_root, files)
        manifest_path = package_root / _MANIFEST
        instructions_path = package_root / "SKILL.md"
        if manifest_path not in files or instructions_path not in files:
            raise SkillPackageError("Skill package requires fairy-skill.json and SKILL.md")
        instruction_bytes = contents[instructions_path]
        if len(instruction_bytes) > _MAX_INSTRUCTION_BYTES:
            raise SkillPackageError("Skill instructions are too large")
        instructions = _decode_instructions(instruction_bytes)
        try:
            raw_manifest = json.loads(contents[manifest_path].decode("utf-8"))
            manifest = SkillManifest.model_validate(raw_manifest)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
            raise SkillPackageError(f"Invalid Fairy Skill manifest: {error}") from error
        if manifest.name != package_root.name:
            raise SkillPackageError("Skill manifest name must match its directory")
        content_digest = _content_digest(
            package_root,
            {path: content for path, content in contents.items() if path.name != _MANIFEST},
        )
        if content_digest != manifest.content_sha256:
            raise SkillPackageError("Skill package content digest does not match its manifest")
        _validate_agent_skill_frontmatter(instructions, manifest)
        resources = {
            path.relative_to(package_root).as_posix(): contents[path]
            for path in contents
            if path.name not in {_MANIFEST, "SKILL.md"}
        }
        return SkillPackage(
            manifest=manifest,
            instructions=instructions,
            content_sha256=content_digest,
            resources=resources,
        )


def _package_files(root: Path, *, include_manifest: bool) -> tuple[Path, ...]:
    paths: list[Path] = []
    total = 0
    for candidate in sorted(root.rglob("*"), key=lambda value: value.as_posix()):
        if candidate.is_symlink():
            raise SkillPackageError("Skill packages cannot contain links")
        if candidate.is_dir():
            continue
        if not candidate.is_file():
            raise SkillPackageError("Skill packages can contain regular files only")
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise SkillPackageError("Skill package file escaped its root")
        if not include_manifest and candidate.name == _MANIFEST:
            continue
        paths.append(candidate)
        total += candidate.stat().st_size
        if len(paths) > _MAX_FILES:
            raise SkillPackageError("Skill package contains too many files")
        if total > _MAX_PACKAGE_BYTES:
            raise SkillPackageError("Skill package is too large")
    return tuple(paths)


def _package_contents(root: Path, *, include_manifest: bool) -> dict[Path, bytes]:
    contents: dict[Path, bytes] = {}
    total = 0
    for path in _package_files(root, include_manifest=include_manifest):
        before = path.resolve(strict=True)
        if path.is_symlink() or not before.is_relative_to(root):
            raise SkillPackageError("Skill package file escaped its root")
        try:
            with path.open("rb") as handle:
                mode = os.fstat(handle.fileno()).st_mode
                content = handle.read(_MAX_PACKAGE_BYTES + 1)
        except OSError as error:
            raise SkillPackageError("Skill package file could not be read") from error
        after = path.resolve(strict=True)
        if before != after or path.is_symlink() or not stat.S_ISREG(mode):
            raise SkillPackageError("Skill package changed during validation")
        total += len(content)
        if total > _MAX_PACKAGE_BYTES:
            raise SkillPackageError("Skill package is too large")
        contents[path] = content
    return contents


def _reject_executables(root: Path, files: tuple[Path, ...]) -> None:
    if any(part.casefold() == "scripts" for path in files for part in path.relative_to(root).parts):
        raise SkillPackageError("Fairy Skills cannot contain executable scripts")
    for path in files:
        mode = path.stat().st_mode
        if path.suffix.casefold() in _EXECUTABLE_SUFFIXES or mode & (
            stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        ):
            raise SkillPackageError("Fairy Skills cannot contain executable files")


def _validate_agent_skill_frontmatter(instructions: str, manifest: SkillManifest) -> None:
    frontmatter = _agent_skill_frontmatter(instructions)
    if frontmatter.get("name") != manifest.name:
        raise SkillPackageError("SKILL.md name does not match the manifest")
    if frontmatter.get("description") != manifest.description:
        raise SkillPackageError("SKILL.md description does not match the manifest")


def _decode_instructions(instruction_bytes: bytes) -> str:
    if len(instruction_bytes) > _MAX_INSTRUCTION_BYTES:
        raise SkillPackageError("Skill instructions are too large")
    try:
        return instruction_bytes.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError as error:
        raise SkillPackageError("Skill instructions must be UTF-8") from error


def _agent_skill_frontmatter(instructions: str) -> dict[str, object]:
    lines = instructions.splitlines()
    if len(lines) < 4 or lines[0].strip() != "---":
        raise SkillPackageError("SKILL.md requires YAML frontmatter")
    try:
        closing = next(
            index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"
        )
    except StopIteration as error:
        raise SkillPackageError("SKILL.md frontmatter is not closed") from error
    try:
        frontmatter = yaml.safe_load("\n".join(lines[1:closing]))
    except yaml.YAMLError as error:
        raise SkillPackageError("SKILL.md frontmatter is invalid") from error
    if not isinstance(frontmatter, dict):
        raise SkillPackageError("SKILL.md frontmatter must be an object")
    return frontmatter


__all__ = [
    "SkillPackageError",
    "SkillPackageInspection",
    "SkillPackageLoader",
    "package_content_digest",
]
