from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

V3_ROOT = Path(__file__).parents[2]


def test_core_runtime_dependencies_stay_transport_independent() -> None:
    project = tomllib.loads((V3_ROOT / "core" / "pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["dependencies"] == [
        "pydantic>=2.13,<3",
        "sqlalchemy>=2.0.51,<2.1",
    ]


def test_v3_source_has_no_legacy_runtime_dependencies() -> None:
    result = subprocess.run(
        [sys.executable, str(V3_ROOT / "scripts" / "check_boundaries.py"), str(V3_ROOT)],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_boundary_gate_rejects_core_cloud_cycles_and_cloud_local_adapters(
    tmp_path: Path,
) -> None:
    for source_root in (
        "core/src",
        "cloud/src",
        "desktop/src",
        "desktop/src-tauri/crates",
    ):
        (tmp_path / source_root).mkdir(parents=True)
    (tmp_path / "core/src/core_cycle.py").write_text(
        "from fairy_cloud.api import create_cloud_app\n",
        encoding="utf-8",
    )
    (tmp_path / "cloud/src/transport_cycle.py").write_text(
        "from fairy_core.transports.jsonrpc import JsonRpcDispatcher\n",
        encoding="utf-8",
    )
    (tmp_path / "cloud/src/local_state.py").write_text(
        "from fairy_core.persistence.sqlite import create_sqlite_core_engine\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(V3_ROOT / "scripts" / "check_boundaries.py"), str(tmp_path)],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 1
    assert "Core cannot import Cloud adapters" in result.stdout
    assert "Cloud cannot compose through Core transports" in result.stdout
    assert "Cloud cannot use local SQLite adapters" in result.stdout
