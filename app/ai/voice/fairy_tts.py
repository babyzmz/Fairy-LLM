from __future__ import annotations

import base64
import io
import json
import logging
import os
import subprocess
import threading
import time
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import requests

from app.config import BASE_DIR, voice_config

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TTSChunk:
    path: Path
    duration_sec: float
    sample_rate: int
    is_final: bool = False


class FairyTTS:
    """Python 3.13 client for the external Python 3.10 CosyVoice runtime."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir or (BASE_DIR / "data" / "voice_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.log_path = BASE_DIR / "data" / "cosyvoice_service.log"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.hf_home = BASE_DIR / ".hf_home"
        self.hf_home.mkdir(parents=True, exist_ok=True)
        self.mpl_config_dir = BASE_DIR / ".matplotlib"
        self.mpl_config_dir.mkdir(parents=True, exist_ok=True)

        self._warmup_thread: threading.Thread | None = None
        self._process_lock = threading.Lock()
        self._spawned_process: subprocess.Popen[str] | None = None
        self._log_handle = None
        self._session = requests.Session()
        self._base_url = f"http://{voice_config.cosyvoice_service_host}:{voice_config.cosyvoice_service_port}"

    def start_warmup(self) -> None:
        thread = self._warmup_thread
        if thread is not None and thread.is_alive():
            return
        self._warmup_thread = threading.Thread(target=self.warmup, name="fairy-cosyvoice-warmup", daemon=True)
        self._warmup_thread.start()

    def warmup(self) -> None:
        try:
            self._ensure_service_started()
            self._session.post(
                f"{self._base_url}/warmup",
                timeout=voice_config.cosyvoice_request_timeout_sec,
            ).raise_for_status()
        except Exception:
            logger.exception("CosyVoice warmup failed")

    def is_ready(self) -> bool:
        return self._is_service_healthy()

    def stream_chunks(self, text: str, *, system_voice: bool = False) -> Iterator[TTSChunk]:
        prepared_text = self._normalize_text(text, system_voice=system_voice)
        if not prepared_text:
            return

        self._ensure_service_started()
        response = self._session.post(
            f"{self._base_url}/synthesize-stream",
            json={"text": prepared_text, "system_voice": system_voice},
            stream=True,
            timeout=voice_config.cosyvoice_request_timeout_sec,
        )
        response.raise_for_status()
        pending_chunk: TTSChunk | None = None
        try:
            for raw_line in response.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
                payload = json.loads(raw_line)
                if payload.get("error"):
                    raise RuntimeError(str(payload["error"]))
                if payload.get("done"):
                    if pending_chunk is not None:
                        pending_chunk.is_final = True
                        yield pending_chunk
                        pending_chunk = None
                    break
                audio_b64 = payload.get("audio_b64")
                sample_rate = int(payload.get("sample_rate", voice_config.audio_sample_rate))
                if not audio_b64:
                    continue
                wav_bytes = base64.b64decode(audio_b64)
                path, duration_sec = self._write_chunk_file(
                    wav_bytes,
                    sample_rate=sample_rate,
                    system_voice=system_voice,
                )
                chunk = TTSChunk(path=path, duration_sec=duration_sec, sample_rate=sample_rate)
                if pending_chunk is not None:
                    yield pending_chunk
                pending_chunk = chunk
        finally:
            response.close()
        if pending_chunk is not None:
            pending_chunk.is_final = True
            yield pending_chunk

    def stream_to_files(self, text: str, *, system_voice: bool = False) -> Iterator[Path]:
        for chunk in self.stream_chunks(text, system_voice=system_voice):
            yield chunk.path

    def synthesize_to_file(self, text: str, *, system_voice: bool = False) -> Path:
        chunk_paths = [chunk.path for chunk in self.stream_chunks(text, system_voice=system_voice)]
        if not chunk_paths:
            raise RuntimeError("CosyVoice service returned no audio chunks.")
        if len(chunk_paths) == 1:
            return chunk_paths[0]

        merged_path = self.cache_dir / f"{uuid.uuid4().hex}.wav"
        self._merge_wav_files(chunk_paths, merged_path)
        for chunk_path in chunk_paths:
            chunk_path.unlink(missing_ok=True)
        return merged_path

    def shutdown(self) -> None:
        process = self._spawned_process
        self._spawned_process = None
        log_handle = self._log_handle
        self._log_handle = None
        if process is None:
            if log_handle is not None:
                log_handle.close()
            return
        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
            except Exception:
                pass
        finally:
            if log_handle is not None:
                log_handle.close()

    def _ensure_service_started(self) -> None:
        if self._is_service_healthy():
            return

        with self._process_lock:
            if self._is_service_healthy():
                return

            process = self._spawned_process
            if process is not None and process.poll() is None:
                self._wait_for_service()
                return

            python_path = voice_config.cosyvoice_python_path
            if not python_path.exists():
                raise RuntimeError(
                    f"CosyVoice Python 3.10 runtime not found: {python_path}. "
                    "Create cosyvoice_env and install requirements-cosyvoice.txt first."
                )

            script_path = BASE_DIR / "app" / "ai" / "voice" / "cosyvoice_service.py"
            self._log_handle = self.log_path.open("a", encoding="utf-8")
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            env = os.environ.copy()
            env.setdefault("HF_HOME", str(self.hf_home))
            env.setdefault("HF_HUB_CACHE", str(self.hf_home / "hub"))
            env.setdefault("TRANSFORMERS_CACHE", str(self.hf_home / "transformers"))
            env.setdefault("HF_HUB_DISABLE_XET", "1")
            env.setdefault("MPLCONFIGDIR", str(self.mpl_config_dir))
            if voice_config.cosyvoice_device.strip().lower() != "cuda":
                env.setdefault("CUDA_VISIBLE_DEVICES", "")
            self._spawned_process = subprocess.Popen(
                [str(python_path), str(script_path)],
                cwd=str(BASE_DIR),
                stdout=self._log_handle,
                stderr=self._log_handle,
                text=True,
                creationflags=creationflags,
                env=env,
            )
            self._wait_for_service()

    def _wait_for_service(self) -> None:
        deadline = time.monotonic() + voice_config.cosyvoice_service_start_timeout_sec
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                if self._is_service_healthy():
                    return
            except Exception as exc:  # noqa: BLE001
                last_error = exc
            process = self._spawned_process
            if process is not None and process.poll() is not None:
                raise RuntimeError(
                    f"CosyVoice service exited early with code {process.returncode}. "
                    f"See log: {self.log_path}"
                ) from last_error
            time.sleep(1.0)
        raise RuntimeError(f"CosyVoice service startup timed out. Check log: {self.log_path}") from last_error

    def _is_service_healthy(self) -> bool:
        try:
            response = self._session.get(f"{self._base_url}/health", timeout=2.0)
            if response.status_code != 200:
                return False
            payload = response.json()
            return bool(payload.get("ok"))
        except Exception:
            return False

    def _normalize_text(self, text: str, *, system_voice: bool) -> str:
        cleaned = " ".join(str(text or "").replace("\r", "\n").split())
        if not cleaned:
            return ""
        if system_voice:
            cleaned = cleaned.replace("：", "，")
            cleaned = cleaned.replace(":", "，")
            cleaned = cleaned.replace(" - ", "，")
            cleaned = cleaned.replace("—", "，")
            cleaned = cleaned.replace("…", "。")
        if cleaned[-1] not in "\u3002\uff01\uff1f.!?":
            cleaned += "\u3002"
        return cleaned

    def _write_chunk_file(self, wav_bytes: bytes, *, sample_rate: int, system_voice: bool) -> tuple[Path, float]:
        prefix = "system" if system_voice else "reply"
        path = self.cache_dir / f"{prefix}_{uuid.uuid4().hex}.wav"
        path.write_bytes(wav_bytes)
        duration_sec = self._wav_duration_sec(wav_bytes, fallback_sample_rate=sample_rate)
        return path, duration_sec

    def _wav_duration_sec(self, wav_bytes: bytes, *, fallback_sample_rate: int) -> float:
        try:
            with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
                frame_rate = wav_file.getframerate() or fallback_sample_rate
                return wav_file.getnframes() / max(frame_rate, 1)
        except Exception:
            return 0.0

    def _merge_wav_files(self, chunk_paths: list[Path], output_path: Path) -> None:
        params: wave._wave_params | None = None
        frames: list[bytes] = []
        for chunk_path in chunk_paths:
            with wave.open(str(chunk_path), "rb") as wav_file:
                current_params = wav_file.getparams()
                if params is None:
                    params = current_params
                elif current_params[:3] != params[:3]:
                    # Only compare nchannels / sampwidth / framerate.
                    # nframes (params[3]) legitimately differs per chunk.
                    raise RuntimeError(
                        "CosyVoice returned chunks with incompatible WAV format "
                        f"(got {current_params[:3]}, expected {params[:3]})."
                    )
                frames.append(wav_file.readframes(wav_file.getnframes()))
        if params is None:
            raise RuntimeError("CosyVoice service returned no mergeable WAV chunks.")
        with wave.open(str(output_path), "wb") as wav_file:
            wav_file.setparams(params)
            for frame_block in frames:
                wav_file.writeframes(frame_block)
