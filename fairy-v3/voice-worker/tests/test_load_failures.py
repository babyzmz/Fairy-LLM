from pathlib import Path
from types import SimpleNamespace

import pytest

from fairy_voice_worker.runtime import CosyVoice3Runtime


@pytest.mark.parametrize("oom", [True, False])
@pytest.mark.parametrize("cleanup_fails", [True, False])
def test_failed_load_clears_ready_and_preserves_the_original_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    oom: bool,
    cleanup_fails: bool,
) -> None:
    class OutOfMemoryError(RuntimeError):
        pass

    original = OutOfMemoryError("private allocator detail") if oom else RuntimeError("private path")

    def fail() -> None:
        raise original

    def available() -> bool:
        if cleanup_fails:
            raise RuntimeError("device disappeared")
        return True

    cuda = SimpleNamespace(
        OutOfMemoryError=OutOfMemoryError, is_available=available, empty_cache=lambda: None
    )
    monkeypatch.setitem(__import__("sys").modules, "torch", SimpleNamespace(cuda=cuda))
    runtime = CosyVoice3Runtime(
        model_dir=tmp_path,
        source_dir=tmp_path,
        prompt_wav=tmp_path / "prompt.wav",
        prompt_text=tmp_path / "prompt.txt",
    )
    monkeypatch.setattr(runtime, "_validate_assets", fail)
    runtime._model = object()
    runtime._model_digest = "a" * 64
    with pytest.raises(type(original)) as caught:
        runtime.load()
    assert caught.value is original
    assert runtime._model is None and runtime._model_digest is None and not runtime._ready
    assert runtime._error_code == ("VOICE_INSUFFICIENT_VRAM" if oom else "VOICE_WORKER_LOAD_FAILED")
