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
        "alembic>=1.18.5,<1.19",
        "cryptography>=46,<50",
        "httpx>=0.28.1,<0.29",
        "mcp>=1.28.1,<2",
        "pydantic>=2.13,<3",
        "pyyaml>=6.0.3,<7",
        "sqlalchemy>=2.0.51,<2.1",
        "tzdata>=2025.3",
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


def test_boundary_gate_rejects_release_safety_and_structure_regressions(
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
    (tmp_path / "core/src/host_shell.py").write_text(
        'import subprocess\nsubprocess.run(["cmd.exe"], shell=True)\n',
        encoding="utf-8",
    )
    (tmp_path / "capabilities/src/vector_authority.py").write_text(
        "import chromadb\n",
        encoding="utf-8",
    )
    (tmp_path / "cloud/src/embedded_secret.py").write_text(
        'TOKEN = "sk-or-v1-abcdefghijklmnopqrstuvwxyz"\n',
        encoding="utf-8",
    )
    (tmp_path / "desktop/src/browser_voice.ts").write_text(
        "window.speechSynthesis.speak(new SpeechSynthesisUtterance('unsafe'));\n",
        encoding="utf-8",
    )
    (tmp_path / "desktop/src/keyword_router.ts").write_text(
        "export const keywordRouter = () => 'weather';\n",
        encoding="utf-8",
    )
    (tmp_path / "desktop/src/privileged_renderer.ts").write_text(
        'import { readTextFile } from "@tauri-apps/plugin-fs";\n',
        encoding="utf-8",
    )
    (tmp_path / "desktop/src/oversized.ts").write_text(
        "\n".join("export {};" for _ in range(1_201)),
        encoding="utf-8",
    )
    (tmp_path / "core/src/unowned.bin").write_bytes(b"not source")
    (tmp_path / "desktop/src/empty_future").mkdir()
    (tmp_path / "cloud/compose.yaml").write_text(
        """services:
  execution:
    privileged: true
    network_mode: host
    cap_add: [SYS_ADMIN]
    volumes:
      - ./workspace:/workspace
      - /var/run/docker.sock:/var/run/docker.sock
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(V3_ROOT / "scripts" / "check_boundaries.py"), str(tmp_path)],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 1
    assert "host shell execution is forbidden" in result.stdout
    assert "duplicate memory authority dependency" in result.stdout
    assert "credential-shaped literal" in result.stdout
    assert "browser speech synthesis is forbidden" in result.stdout
    assert "keyword routing is forbidden" in result.stdout
    assert "renderer privileged API is forbidden" in result.stdout
    assert "Docker socket access is forbidden" in result.stdout
    assert "privileged Compose service is forbidden" in result.stdout
    assert "host network_mode is forbidden" in result.stdout
    assert "added Linux capabilities are forbidden" in result.stdout
    assert "project execution service has a host bind mount" in result.stdout
    assert "unowned source file type" in result.stdout
    assert "source module exceeds 1200 lines" in result.stdout
    assert "empty future-facing source directory" in result.stdout


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
