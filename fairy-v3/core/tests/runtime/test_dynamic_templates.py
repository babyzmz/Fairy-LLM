from __future__ import annotations

import json
from pathlib import Path

import pytest

from fairy_core.domain.execution import RuntimeKind
from fairy_core.runtime.templates import (
    PORT_TOKEN,
    RuntimeAdapter,
    RuntimeTemplateError,
    select_runtime_template,
)


def test_vite_template_uses_fixed_binary_and_ignores_package_scripts(tmp_path: Path) -> None:
    _node_project(
        tmp_path,
        dependencies={"vite": "8.1.4"},
        scripts={"dev": "node -e \"require('child_process').execSync('calc')\""},
    )

    template = select_runtime_template(tmp_path, execution_target="local")

    assert template.kind is RuntimeKind.WSL_PROJECT
    assert template.adapter is RuntimeAdapter.VITE
    assert template.argv == (
        "node_modules/.bin/vite",
        "--host",
        "127.0.0.1",
        "--port",
        PORT_TOKEN,
        "--strictPort",
    )
    assert template.cwd == "."
    assert template.readiness_path == "/"
    assert len(template.dependency_key) == 64
    assert "calc" not in " ".join(template.argv)


@pytest.mark.parametrize(
    ("dependency", "adapter", "expected"),
    [
        (
            "next",
            RuntimeAdapter.NEXT,
            (
                "node_modules/.bin/next",
                "dev",
                "-H",
                "127.0.0.1",
                "-p",
                PORT_TOKEN,
            ),
        ),
        (
            "astro",
            RuntimeAdapter.ASTRO,
            (
                "node_modules/.bin/astro",
                "dev",
                "--host",
                "127.0.0.1",
                "--port",
                PORT_TOKEN,
            ),
        ),
    ],
)
def test_supported_node_adapters_have_exact_argv(
    tmp_path: Path,
    dependency: str,
    adapter: RuntimeAdapter,
    expected: tuple[str, ...],
) -> None:
    _node_project(tmp_path, dependencies={dependency: "1.0.0"})

    template = select_runtime_template(tmp_path, execution_target="cloud")

    assert template.kind is RuntimeKind.CLOUD_OCI
    assert template.adapter is adapter
    assert template.argv == expected


def test_python_asgi_requires_a_strict_declarative_entry(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='fixture'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text(
        "version = 1\nrevision = 1\nrequires-python = '>=3.13'\n",
        encoding="utf-8",
    )
    _json(
        tmp_path / "fairy.runtime.json",
        {
            "schema_version": 1,
            "adapter": "python_asgi",
            "entry": "service.api:app",
            "readiness_path": "/healthz",
        },
    )

    template = select_runtime_template(tmp_path, execution_target="local")

    assert template.adapter is RuntimeAdapter.PYTHON_ASGI
    assert template.argv == (
        ".venv/bin/python",
        "-m",
        "uvicorn",
        "service.api:app",
        "--host",
        "127.0.0.1",
        "--port",
        PORT_TOKEN,
        "--no-access-log",
    )
    assert template.readiness_path == "/healthz"


def test_runtime_manifest_rejects_command_environment_and_endpoint_injection(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='fixture'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text(
        "version = 1\nrevision = 1\nrequires-python = '>=3.13'\n",
        encoding="utf-8",
    )
    for forbidden in ("command", "environment", "host", "port"):
        _json(
            tmp_path / "fairy.runtime.json",
            {
                "schema_version": 1,
                "adapter": "python_asgi",
                "entry": "service:app",
                forbidden: "attacker-controlled",
            },
        )
        with pytest.raises(RuntimeTemplateError, match="schema"):
            select_runtime_template(tmp_path, execution_target="local")


def test_static_fallback_is_local_only_and_ambiguous_dynamic_adapters_fail_closed(
    tmp_path: Path,
) -> None:
    (tmp_path / "index.html").write_text("<h1>Static</h1>", encoding="utf-8")

    static = select_runtime_template(tmp_path, execution_target="local")

    assert static.kind is RuntimeKind.STATIC_SITE
    assert static.adapter is RuntimeAdapter.STATIC
    assert static.argv == ()
    assert static.entry_path == "index.html"
    with pytest.raises(RuntimeTemplateError, match="cloud"):
        select_runtime_template(tmp_path, execution_target="cloud")

    _node_project(tmp_path, dependencies={"vite": "8.1.4", "next": "16.0.0"})
    with pytest.raises(RuntimeTemplateError, match="multiple"):
        select_runtime_template(tmp_path, execution_target="local")


def test_runtime_control_file_cannot_be_a_symlink(tmp_path: Path) -> None:
    target = tmp_path / "outside.json"
    _json(
        target,
        {
            "schema_version": 1,
            "adapter": "python_asgi",
            "entry": "service:app",
        },
    )
    try:
        (tmp_path / "fairy.runtime.json").symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(RuntimeTemplateError, match="regular file"):
        select_runtime_template(tmp_path, execution_target="local")


def _node_project(
    root: Path,
    *,
    dependencies: dict[str, str],
    scripts: dict[str, str] | None = None,
) -> None:
    _json(
        root / "package.json",
        {
            "name": "fixture",
            "scripts": scripts or {},
            "dependencies": dependencies,
        },
    )
    _json(
        root / "package-lock.json",
        {"lockfileVersion": 3, "packages": {}},
    )


def _json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")
