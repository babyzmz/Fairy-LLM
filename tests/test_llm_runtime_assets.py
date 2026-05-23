from pathlib import Path
from types import SimpleNamespace

from app.ai.llm_client_server_manager import ServerManager


def _config(**overrides):
    data = {
        "server_executable": Path("missing-llama-server.exe"),
        "model_path": Path("model") / "Qwen3.5-9B-Q4_K_M.gguf",
        "mmproj_path": None,
        "model": "Qwen3.5-9B-Q4_K_M",
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_relative_model_path_is_resolved_from_base_dir(tmp_path, monkeypatch) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "Qwen3.5-9B-Q4_K_M.gguf").write_bytes(b"gguf")
    monkeypatch.chdir(tmp_path.parent)

    manager = ServerManager(_config(), base_dir=tmp_path)

    assert manager._discover_model_path() == Path("model") / "Qwen3.5-9B-Q4_K_M.gguf"


def test_project_model_discovery_returns_relative_runtime_path(tmp_path) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "Qwen3.5-9B-Q4_K_M.gguf").write_bytes(b"gguf")

    manager = ServerManager(
        _config(model_path=Path("missing") / "missing.gguf"),
        base_dir=tmp_path,
    )

    assert manager._discover_model_path() == Path("model") / "Qwen3.5-9B-Q4_K_M.gguf"


def test_mmproj_discovery_is_limited_to_model_directory(tmp_path) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    model_path = model_dir / "Qwen3.5-9B-Q4_K_M.gguf"
    model_path.write_bytes(b"gguf")
    stale_dir = tmp_path / "other_models"
    stale_dir.mkdir()
    (stale_dir / "mmproj-BF16.gguf").write_bytes(b"stale")

    manager = ServerManager(_config(), base_dir=tmp_path)

    assert manager._discover_mmproj_path(Path("model") / model_path.name) is None
