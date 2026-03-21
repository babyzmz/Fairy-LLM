from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import os
import re
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import BASE_DIR, voice_config


COSYVOICE_REPO_CANDIDATES = (
    BASE_DIR / "third_party" / "CosyVoice",
    BASE_DIR / "third_party" / "CosyVoice-main",
)


def _first_existing_path(paths: tuple[Path, ...]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


COSYVOICE_REPO = _first_existing_path(COSYVOICE_REPO_CANDIDATES)
MATCHA_REPO = COSYVOICE_REPO / "third_party" / "Matcha-TTS" if COSYVOICE_REPO is not None else None
HF_HOME = BASE_DIR / ".hf_home"
HF_HOME.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("HF_HOME", str(HF_HOME))
os.environ.setdefault("HF_HUB_CACHE", str(HF_HOME / "hub"))
os.environ.setdefault("TRANSFORMERS_CACHE", str(HF_HOME / "transformers"))
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
MPLCONFIGDIR = BASE_DIR / ".matplotlib"
MPLCONFIGDIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(MPLCONFIGDIR))
if voice_config.cosyvoice_device.strip().lower() != "cuda":
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
if COSYVOICE_REPO is not None and str(COSYVOICE_REPO) not in sys.path:
    sys.path.insert(0, str(COSYVOICE_REPO))
if MATCHA_REPO is not None and MATCHA_REPO.exists() and str(MATCHA_REPO) not in sys.path:
    sys.path.insert(0, str(MATCHA_REPO))

import numpy as np
import soundfile as sf
import torch
import torchaudio
from huggingface_hub import snapshot_download


LOG_PATH = BASE_DIR / "data" / "cosyvoice_service.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=str(LOG_PATH),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


def _import_cosyvoice_classes() -> tuple[Any, Any | None]:
    try:
        from cosyvoice.cli.cosyvoice import CosyVoice, CosyVoice2
        return CosyVoice, CosyVoice2
    except Exception:
        try:
            from cosyvoice.cli.cosyvoice import CosyVoice  # type: ignore
            return CosyVoice, None
        except Exception as exc:
            repo_hint = str(COSYVOICE_REPO) if COSYVOICE_REPO is not None else str(BASE_DIR / "third_party" / "CosyVoice")
            raise RuntimeError(
                "CosyVoice runtime code is missing. "
                f"Expected repo under {repo_hint} or an installed `cosyvoice` package in "
                f"{voice_config.cosyvoice_python_path.parent.parent}."
            ) from exc


class CosyVoiceRuntime:
    def __init__(self) -> None:
        self.model_dir = voice_config.cosyvoice_model_dir
        self.model_repo = voice_config.cosyvoice_model_repo
        self.device = self._resolve_device()
        self.model: Any | None = None
        self.prompt_text = ""
        self.prompt_wav_path: str | None = None
        self.zero_shot_spk_id = voice_config.cosyvoice_zero_shot_spk_id
        self.clone_speaker_ready = False

    def load(self) -> None:
        if self.model is not None:
            return
        self._ensure_model_downloaded()
        CosyVoice, CosyVoice2 = _import_cosyvoice_classes()

        model_cls = CosyVoice2 or CosyVoice
        self.model = model_cls(str(self.model_dir))
        self.prompt_text, self.prompt_wav_path = self._prepare_prompt_assets()
        self._register_zero_shot_speaker()
        self._prime_voice_path()
        logger.info("CosyVoice loaded on device=%s", self.device)

    def warmup(self) -> None:
        self.load()
        warmup_text = "主人，我已准备就绪。"
        try:
            for _ in self.iter_chunks(warmup_text, system_voice=False):
                break
        except Exception:
            logger.exception("Clone warmup failed; retrying with non-clone path")
            for _ in self.iter_chunks("我已准备就绪。", system_voice=True):
                break

    def health(self) -> dict[str, Any]:
        return {
            "ok": self.model is not None,
            "device": self.device,
            "model_dir": str(self.model_dir),
            "clone_enabled": bool(self.prompt_text and self.prompt_wav_path and voice_config.voice_clone_enabled),
            "clone_cached": self.clone_speaker_ready,
        }

    def generate_chunks(self, text: str, *, system_voice: bool = False) -> list[dict[str, Any]]:
        return list(self.iter_chunks(text, system_voice=system_voice))

    def iter_chunks(self, text: str, *, system_voice: bool = False) -> Any:
        self.load()
        assert self.model is not None

        normalized = self._normalize_text(text)
        if not normalized:
            return

        use_clone = (
            voice_config.voice_clone_enabled
            and not system_voice
            and self.clone_speaker_ready
        )

        iterator = self._build_inference_iterator(normalized, use_clone=use_clone)
        for item in iterator:
            speech = item.get("tts_speech")
            if speech is None:
                continue
            audio = self._speech_to_numpy(speech)
            if audio.size == 0:
                continue
            sample_rate = int(item.get("sample_rate") or item.get("sampling_rate") or voice_config.audio_sample_rate)
            wav_bytes = self._encode_wav_bytes(audio, sample_rate)
            yield {
                "sample_rate": sample_rate,
                "audio_b64": base64.b64encode(wav_bytes).decode("ascii"),
            }

    def _build_inference_iterator(self, text: str, *, use_clone: bool) -> Any:
        assert self.model is not None
        if use_clone:
            return self.model.inference_zero_shot(
                text,
                "",
                "",
                zero_shot_spk_id=self.zero_shot_spk_id,
                stream=True,
                text_frontend=voice_config.cosyvoice_text_frontend,
            )

        speaker_name = self._resolve_sft_speaker()
        if speaker_name is not None:
            return self.model.inference_sft(
                text,
                speaker_name,
                stream=True,
                text_frontend=voice_config.cosyvoice_text_frontend,
            )

        if self.clone_speaker_ready:
            logger.warning("No valid SFT speaker found; falling back to zero-shot speaker for text: %s", text)
            return self.model.inference_zero_shot(
                text,
                "",
                "",
                zero_shot_spk_id=self.zero_shot_spk_id,
                stream=True,
                text_frontend=voice_config.cosyvoice_text_frontend,
            )

        raise RuntimeError("CosyVoice has no valid SFT speaker and zero-shot speaker is not ready.")

    def _ensure_model_downloaded(self) -> None:
        self.model_dir.mkdir(parents=True, exist_ok=True)
        if any(self.model_dir.iterdir()):
            return
        logger.info("Downloading CosyVoice model repo %s", self.model_repo)
        snapshot_download(
            repo_id=self.model_repo,
            local_dir=str(self.model_dir),
            local_dir_use_symlinks=False,
        )

    def _prepare_prompt_assets(self) -> tuple[str, str | None]:
        if not voice_config.voice_clone_enabled:
            return "", None

        prompt_path = voice_config.prompt_wav_path
        text_path = voice_config.prompt_text_path
        if not prompt_path.exists():
            raise FileNotFoundError(f"Prompt WAV not found: {prompt_path}")
        if not text_path.exists():
            raise FileNotFoundError(f"Prompt text not found: {text_path}")

        prompt_text = re.sub(r"\s+", " ", text_path.read_text(encoding="utf-8-sig")).strip()
        if not prompt_text:
            raise ValueError("Prompt text is empty.")

        wav, sample_rate = torchaudio.load(str(prompt_path))
        if wav.ndim == 2 and wav.shape[0] > 1:
            wav = wav.mean(dim=0, keepdim=True)
        if sample_rate != 16000:
            wav = torchaudio.functional.resample(wav, sample_rate, 16000)
            sample_rate = 16000

        duration_sec = wav.shape[-1] / sample_rate
        if duration_sec < voice_config.prompt_min_sec:
            logger.warning("Prompt audio is too short for cloning: %.2fs", duration_sec)
            return "", None
        if duration_sec > voice_config.prompt_max_sec:
            keep_frames = int(voice_config.prompt_target_sec * sample_rate)
            wav = wav[:, :keep_frames]
            prompt_text = self._clip_prompt_text(prompt_text)

        prepared_dir = voice_config.prepared_prompt_dir
        prepared_dir.mkdir(parents=True, exist_ok=True)
        prepared_path = prepared_dir / "fairy_prompt_prepared.wav"
        sf.write(prepared_path, wav.squeeze(0).cpu().numpy(), 16000)
        return prompt_text, str(prepared_path)

    def _register_zero_shot_speaker(self) -> None:
        if not voice_config.voice_clone_enabled:
            self.clone_speaker_ready = False
            return
        if self.model is None or not self.prompt_text or not self.prompt_wav_path:
            self.clone_speaker_ready = False
            return
        try:
            self.model.add_zero_shot_spk(
                self.prompt_text,
                self.prompt_wav_path,
                self.zero_shot_spk_id,
            )
            self.clone_speaker_ready = True
        except Exception:
            self.clone_speaker_ready = False
            logger.exception("Failed to cache Fairy zero-shot speaker")

    def _prime_voice_path(self) -> None:
        if self.model is None:
            return
        text = "主人，我已准备就绪。" if self.clone_speaker_ready else "我已准备就绪。"
        try:
            iterator = self._build_inference_iterator(text, use_clone=self.clone_speaker_ready)
            next(iterator, None)
        except Exception:
            logger.exception("Failed to prime CosyVoice inference path")

    def _clip_prompt_text(self, text: str) -> str:
        sentence = re.split(r"(?<=[\u3002\uff01\uff1f.!?])", text, maxsplit=1)[0].strip()
        return sentence or text[:48].strip()

    def _resolve_sft_speaker(self) -> str | None:
        assert self.model is not None
        speaker_name = voice_config.cosyvoice_speaker
        speakers = getattr(self.model, "spk2info", None)
        if not speakers:
            return None
        if speaker_name in speakers:
            return speaker_name
        try:
            return next(iter(speakers.keys()))
        except StopIteration:
            return None

    def _normalize_text(self, text: str) -> str:
        cleaned = text.strip()
        if not cleaned:
            return ""
        cleaned = re.sub(r"https?://\S+", " ", cleaned)
        cleaned = re.sub(r"`[^`]*`", " ", cleaned)
        cleaned = re.sub(r"\[[^\]]+\]\([^)]+\)", " ", cleaned)
        cleaned = re.sub(r"[*_#~<>|[\]{}]+", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if cleaned and cleaned[-1] not in "\u3002\uff01\uff1f.!?":
            cleaned += "\u3002"
        return cleaned

    def _speech_to_numpy(self, speech: Any) -> np.ndarray:
        if hasattr(speech, "detach"):
            speech = speech.detach()
        if hasattr(speech, "cpu"):
            speech = speech.cpu()
        if hasattr(speech, "numpy"):
            speech = speech.numpy()
        audio = np.asarray(speech)
        if audio.ndim > 1:
            audio = audio.squeeze()
        return audio.astype(np.float32, copy=False)

    def _encode_wav_bytes(self, audio: np.ndarray, sample_rate: int) -> bytes:
        buffer = io.BytesIO()
        sf.write(buffer, audio, sample_rate, format="WAV")
        return buffer.getvalue()

    def _resolve_device(self) -> str:
        configured = voice_config.cosyvoice_device.strip().lower()
        if configured == "cuda" and torch.cuda.is_available():
            return "cuda"
        return "cpu"


class RequestHandler(BaseHTTPRequestHandler):
    runtime: CosyVoiceRuntime

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send_json(HTTPStatus.OK, self.runtime.health())
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/warmup":
            try:
                self.runtime.warmup()
            except Exception as exc:  # noqa: BLE001
                logger.exception("Warmup failed")
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
                return
            self._send_json(HTTPStatus.OK, {"ok": True})
            return

        if self.path == "/synthesize-stream":
            try:
                payload = self._read_json_body()
                text = str(payload.get("text", ""))
                system_voice = bool(payload.get("system_voice", False))
            except Exception as exc:  # noqa: BLE001
                logger.exception("Synthesis failed")
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
                return

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            try:
                for chunk in self.runtime.iter_chunks(text, system_voice=system_voice):
                    line = (json.dumps(chunk, ensure_ascii=False) + "\n").encode("utf-8")
                    self.wfile.write(line)
                    self.wfile.flush()
                self.wfile.write(b'{"done": true}\n')
                self.wfile.flush()
            except Exception as exc:  # noqa: BLE001
                logger.exception("Streaming synthesis failed")
                error_line = json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8") + b"\n"
                try:
                    self.wfile.write(error_line)
                    self.wfile.flush()
                except Exception:
                    pass
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def log_message(self, format: str, *args: Any) -> None:
        logger.info("%s - %s", self.address_string(), format % args)

    def _read_json_body(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length) if content_length > 0 else b"{}"
        return json.loads(body.decode("utf-8"))

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=voice_config.cosyvoice_service_host)
    parser.add_argument("--port", type=int, default=voice_config.cosyvoice_service_port)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    runtime = CosyVoiceRuntime()
    runtime.load()
    RequestHandler.runtime = runtime

    server = ThreadingHTTPServer((args.host, args.port), RequestHandler)
    logger.info("CosyVoice service listening on http://%s:%s", args.host, args.port)
    server.serve_forever()


if __name__ == "__main__":
    main()
