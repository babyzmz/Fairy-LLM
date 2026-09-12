import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
from fairy_core.providers import CancellationToken, ProviderCancelledError, ProviderUnavailableError
from fairy_core.voice import AudioMediaType, TranscriptionRequest

from fairy_capabilities.voice.local_whisper import LocalWhisperAdapter


def test_composition_does_not_start_or_download_local_models(tmp_path, monkeypatch):
    from fairy_capabilities.composition import build_provider_registry, build_voice_registry

    def forbidden(*args, **kwargs):
        raise AssertionError("Health/startup must not start a model worker")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    configured = {"FAIRY_LOCAL_STT_ROOT": str(tmp_path)}
    providers = build_provider_registry(configured)
    voice = build_voice_registry(configured)
    try:
        local = providers.profile("local-whisper-small")
        assert local.fallback_profile_id is None
        assert local.credential_ref is None
        assert (
            next(item for item in providers.health() if item.profile_id == local.id).status.value
            == "unavailable"
        )
    finally:
        voice.close()
        providers.close()


def test_local_stt_missing_runtime_is_unavailable_without_starting_a_process(tmp_path: Path):
    adapter = LocalWhisperAdapter(tmp_path)
    assert adapter.health().status.value == "unavailable"
    with pytest.raises(ProviderUnavailableError, match="not installed"):
        adapter.transcribe(request(), CancellationToken())


def test_local_stt_executes_without_provider_credentials_and_releases_worker(tmp_path: Path):
    worker = tmp_path / "worker.py"
    worker.write_text(
        "import json,os,sys\n"
        "p=json.load(sys.stdin)\n"
        "assert not any(k.startswith('FAIRY_PROVIDER_SECRET') "
        "or k in ('HF_TOKEN','OPENROUTER_API_KEY') for k in os.environ)\n"
        "assert os.environ['HF_HUB_OFFLINE']=='1'\n"
        "assert p['audio_base64']=='YWJj'\n"
        "print(json.dumps({'text':'本地文字','language':'zh'}))\n",
        encoding="utf-8",
    )
    adapter = LocalWhisperAdapter(tmp_path, python=Path(sys.executable), worker=worker)
    (tmp_path / "model").mkdir()
    for name in ["model.bin", "config.json", "tokenizer.json", "vocabulary.txt"]:
        (tmp_path / "model" / name).write_text("fixture", encoding="utf-8")
    result = adapter.transcribe(request(), CancellationToken())
    assert result.text == "本地文字"
    assert result.profile_id == "local-whisper-small"
    adapter.close()
    with pytest.raises(ProviderUnavailableError):
        adapter.transcribe(request(), CancellationToken())


def request():
    return TranscriptionRequest.create(
        profile_id="local-whisper-small",
        media_type=AudioMediaType.WEBM,
        audio=b"abc",
        language=None,
    )


@pytest.mark.parametrize("stop", ["cancel", "close", "timeout"])
def test_local_stt_reaps_worker_and_releases_lock(stop, tmp_path, monkeypatch):
    worker = tmp_path / "worker.py"
    worker.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    model = tmp_path / "model"
    model.mkdir()
    for name in ["model.bin", "config.json", "tokenizer.json", "vocabulary.txt"]:
        (model / name).write_text("fixture", encoding="utf-8")
    adapter = LocalWhisperAdapter(tmp_path, python=Path(sys.executable), worker=worker)
    adapter.profile = replace(adapter.profile, timeout_seconds=1)
    created = threading.Event()
    children = []
    original = subprocess.Popen

    def spawn(*args, **kwargs):
        process = original(*args, **kwargs)
        children.append(process)
        created.set()
        return process

    monkeypatch.setattr(subprocess, "Popen", spawn)
    cancellation = CancellationToken()
    with ThreadPoolExecutor(max_workers=1) as executor:
        operation = executor.submit(adapter.transcribe, request(), cancellation)
        assert created.wait(3)
        with pytest.raises(ProviderUnavailableError, match="busy"):
            adapter.transcribe(request(), CancellationToken())
        if stop == "cancel":
            cancellation.cancel()
        elif stop == "close":
            adapter.close()
        expected = ProviderCancelledError if stop == "cancel" else ProviderUnavailableError
        with pytest.raises(expected):
            operation.result(timeout=5)
    assert all(child.poll() is not None for child in children)
    assert adapter._lock.acquire(blocking=False)
    adapter._lock.release()
