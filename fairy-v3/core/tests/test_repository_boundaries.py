from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

from fairy_core.storage.ports import StateStore
from fairy_core.storage.sqlalchemy import SqlAlchemyStateStore

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


def test_pre_adapter_compatibility_modules_are_removed() -> None:
    assert not (V3_ROOT / "core/src/fairy_core/commanding/ledger.py").exists()
    assert not (V3_ROOT / "core/src/fairy_core/storage/state_store.py").exists()


def test_boundary_gate_rejects_core_cloud_cycles_and_cloud_local_adapters(
    tmp_path: Path,
) -> None:
    for source_root in (
        "core/src",
        "capabilities/src",
        "cloud/src",
        "desktop/src",
        "desktop/src-tauri/crates",
    ):
        (tmp_path / source_root).mkdir(parents=True)
    (tmp_path / "core/src/core_cycle.py").write_text(
        "from fairy_cloud.api import create_cloud_app\n",
        encoding="utf-8",
    )
    (tmp_path / "core/src/capability_cycle.py").write_text(
        "from fairy_capabilities.composition import build_provider_registry\n",
        encoding="utf-8",
    )
    (tmp_path / "capabilities/src/cloud_cycle.py").write_text(
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
    assert "Core cannot import Capability adapters" in result.stdout
    assert "Capabilities cannot import Cloud composition" in result.stdout
    assert "Cloud cannot compose through Core transports" in result.stdout
    assert "Cloud cannot use local SQLite adapters" in result.stdout


def test_state_store_protocol_and_sqlalchemy_adapter_expose_runtime_contract() -> None:
    required_methods = {
        "list_projects",
        "list_conversations",
        "list_tasks",
        "list_versions",
        "list_approvals",
        "append_runtime",
        "save_runtime",
        "get_runtime",
        "find_runtime_by_idempotency_key",
        "runtimes_for_task",
        "append_preview",
        "save_preview",
        "get_preview",
        "find_preview_by_idempotency_key",
        "preview_for_task",
        "previews_for_conversation",
        "append_artifact",
        "get_artifact",
        "artifacts_for_task",
    }

    assert required_methods <= set(StateStore.__dict__)
    assert all(hasattr(SqlAlchemyStateStore, method) for method in required_methods)
