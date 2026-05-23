from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import os
import re
import shutil
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from zipfile import ZipFile

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.ai.voice.prompt_assets import list_prompt_candidates
from app.config import BASE_DIR, system_config, voice_config

LOG_PATH = BASE_DIR / "data" / "cosyvoice_service.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=str(LOG_PATH),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

COSYVOICE_REPO_CANDIDATES = (
    BASE_DIR / "third_party" / "CosyVoice",
    BASE_DIR / "third_party" / "CosyVoice-main",
)
MATCHA_TTS_ZIP_URL = "https://codeload.github.com/shivammehta25/Matcha-TTS/zip/refs/heads/main"
CLONE_PROFILES = {"clone_clean", "clone_mecha"}
VALID_PROFILES = CLONE_PROFILES | {"sft_clean"}
FIXED_CORE_STEM = "fairy_clone_core"
FIXED_CORE_SEGMENTS: tuple[tuple[str, str], ...] = (
    ("欢迎语音.mp3", " ".join(system_config.welcome_voice_text.split()).strip()),
    ("主人，系统已恢复在线.WAV", "主人，系统已恢复在线。"),
    ("核心链路运行正常.WAV", "核心链路运行正常。"),
)
MECHA_PRESETS: dict[str, dict[str, Any]] = {
    "reply_mecha_v1": {
        "carrier_freq": 13.5,
        "modulation_depth": 0.0055,
        "crush_steps": 960.0,
        "delay_divisor": 700,
        "mix": (0.989, 0.005, 0.003, 0.003),
        "drive_gain": 1.01,
        "output_gain": 0.985,
    },
    "system_mecha_v1": {
        "carrier_freq": 19.0,
        "modulation_depth": 0.018,
        "crush_steps": 480.0,
        "delay_divisor": 300,
        "mix": (0.947, 0.028, 0.016, 0.009),
        "drive_gain": 1.03,
        "output_gain": 0.98,
    },
    "reply_mecha_v2": {
        "carrier_freq": 12.5,
        "modulation_depth": 0.0035,
        "crush_steps": 1440.0,
        "delay_divisor": 900,
        "mix": (0.993, 0.003, 0.002, 0.002),
        "drive_gain": 1.005,
        "output_gain": 0.99,
        "presence_mix": 0.0,
        "smoothing_window": 0,
    },
    "system_mecha_v2": {
        "carrier_freq": 18.0,
        "modulation_depth": 0.015,
        "crush_steps": 560.0,
        "delay_divisor": 340,
        "mix": (0.955, 0.022, 0.013, 0.01),
        "drive_gain": 1.025,
        "output_gain": 0.982,
        "presence_mix": 0.04,
        "smoothing_window": 25,
    },
    "reply_mecha_v3": {
        "carrier_freq": 11.0,
        "modulation_depth": 0.0022,
        "crush_steps": 2048.0,
        "delay_divisor": 1200,
        "mix": (0.995, 0.002, 0.0015, 0.0015),
        "drive_gain": 1.002,
        "output_gain": 0.992,
        "presence_mix": 0.0,
        "smoothing_window": 0,
    },
    "system_mecha_v3": {
        "carrier_freq": 17.0,
        "modulation_depth": 0.012,
        "crush_steps": 720.0,
        "delay_divisor": 420,
        "mix": (0.962, 0.017, 0.011, 0.01),
        "drive_gain": 1.018,
        "output_gain": 0.984,
        "presence_mix": 0.055,
        "smoothing_window": 29,
    },
}
DEFAULT_REPLY_MECHA_PRESET = "reply_mecha_v3"
DEFAULT_SYSTEM_MECHA_PRESET = "system_mecha_v3"


def _first_existing_path(paths: tuple[Path, ...]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def _ensure_matcha_repo(repo_path: Path | None) -> Path | None:
    if repo_path is None:
        return None
    package_init = repo_path / "matcha" / "__init__.py"
    if package_init.exists():
        return repo_path

    parent = repo_path.parent
    extracted_dir = parent / "Matcha-TTS-main"
    try:
        parent.mkdir(parents=True, exist_ok=True)
        if extracted_dir.exists():
            shutil.rmtree(extracted_dir)
        import requests

        response = requests.get(MATCHA_TTS_ZIP_URL, timeout=180)
        response.raise_for_status()
        with ZipFile(io.BytesIO(response.content)) as archive:
            archive.extractall(parent)
        if repo_path.exists():
            shutil.rmtree(repo_path)
        extracted_dir.rename(repo_path)
        logger.info("Provisioned Matcha-TTS runtime at %s", repo_path)
    except Exception:
        logger.exception("Failed to provision Matcha-TTS runtime at %s", repo_path)
    return repo_path if package_init.exists() else None


COSYVOICE_REPO = _first_existing_path(COSYVOICE_REPO_CANDIDATES)
MATCHA_REPO = _ensure_matcha_repo(COSYVOICE_REPO / "third_party" / "Matcha-TTS") if COSYVOICE_REPO is not None else None
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
        self.requested_profile = self._normalize_profile(voice_config.voice_profile)
        self.active_profile = "sft_clean"
        self.prompt_source: str | None = None
        self.prompt_text_source: str | None = None
        self.prompt_duration_sec: float = 0.0
        self.prompt_candidate_count = 0
        self.prompt_segment_count = 0
        self.postprocess_enabled = False

    def load(self) -> None:
        if self.model is not None:
            return
        self.requested_profile = self._normalize_profile(voice_config.voice_profile)
        self._ensure_model_downloaded()
        CosyVoice, CosyVoice2 = _import_cosyvoice_classes()
        model_cls = CosyVoice2 or CosyVoice
        self.model = model_cls(str(self.model_dir))
        self.prompt_text, self.prompt_wav_path = self._prepare_prompt_assets()
        self._register_zero_shot_speaker()
        self.active_profile = self._resolve_active_profile()
        self.postprocess_enabled = self.active_profile == "clone_mecha"
        self._prime_voice_path()
        logger.info(
            "CosyVoice loaded on device=%s requested_profile=%s active_profile=%s clone_cached=%s prompt=%s",
            self.device,
            self.requested_profile,
            self.active_profile,
            self.clone_speaker_ready,
            self.prompt_source or "",
        )

    def warmup(self) -> None:
        self.load()
        warmup_text = "主人，我已准备就绪。"
        try:
            for _ in self.iter_chunks(warmup_text, system_voice=False):
                break
        except Exception:
            logger.exception("Warmup failed on active profile; retrying with fallback text")
            for _ in self.iter_chunks("我已准备就绪。", system_voice=True):
                break

    def health(self) -> dict[str, Any]:
        return {
            "ok": self.model is not None,
            "device": self.device,
            "model_dir": str(self.model_dir),
            "requested_profile": self.requested_profile,
            "active_profile": self.active_profile,
            "clone_enabled": self.requested_profile in CLONE_PROFILES,
            "clone_cached": self.clone_speaker_ready,
            "selected_prompt_source": self.prompt_source,
            "selected_prompt_text_source": self.prompt_text_source,
            "candidate_count": self.prompt_candidate_count,
            "prompt_segment_count": self.prompt_segment_count,
            "prompt_duration_sec": self.prompt_duration_sec,
            "postprocess_enabled": self.postprocess_enabled,
            "reply_mecha_preset": DEFAULT_REPLY_MECHA_PRESET,
            "system_mecha_preset": DEFAULT_SYSTEM_MECHA_PRESET,
        }

    def generate_chunks(self, text: str, *, system_voice: bool = False) -> list[dict[str, Any]]:
        return list(self.iter_chunks(text, system_voice=system_voice))

    def iter_chunks(self, text: str, *, system_voice: bool = False) -> Any:
        self.load()
        assert self.model is not None

        normalized = self._normalize_text(text)
        if not normalized:
            return

        iterator = self._build_inference_iterator(normalized, profile=self.active_profile)
        for item in iterator:
            speech = item.get("tts_speech")
            if speech is None:
                continue
            audio = self._speech_to_numpy(speech)
            if audio.size == 0:
                continue
            sample_rate = int(item.get("sample_rate") or item.get("sampling_rate") or voice_config.audio_sample_rate)
            audio = self._apply_voice_profile(audio, sample_rate, system_voice=system_voice)
            wav_bytes = self._encode_wav_bytes(audio, sample_rate)
            yield {
                "sample_rate": sample_rate,
                "audio_b64": base64.b64encode(wav_bytes).decode("ascii"),
                "profile": self.active_profile,
                "system_voice": bool(system_voice),
            }

    def _build_inference_iterator(self, text: str, *, profile: str) -> Any:
        assert self.model is not None
        if profile in CLONE_PROFILES and self.clone_speaker_ready:
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

    def _ensure_fixed_core_prompt_asset(self) -> None:
        voice_dir = voice_config.voice_prompt_dir
        voice_dir.mkdir(parents=True, exist_ok=True)
        fixed_text_path = voice_dir / f"{FIXED_CORE_STEM}.txt"
        fixed_wav_path = voice_dir / f"{FIXED_CORE_STEM}.wav"
        expected_text = " ".join(text.strip() for _, text in FIXED_CORE_SEGMENTS if text.strip()).strip()

        text_changed = False
        current_text = ""
        if fixed_text_path.exists():
            try:
                current_text = " ".join(fixed_text_path.read_text(encoding="utf-8-sig").split()).strip()
            except Exception:
                current_text = ""
        if current_text != expected_text:
            fixed_text_path.write_text(expected_text + "\n", encoding="utf-8")
            text_changed = True

        sources = [(voice_dir / filename, text) for filename, text in FIXED_CORE_SEGMENTS]
        missing_sources = [str(path) for path, _ in sources if not path.exists()]
        if missing_sources:
            logger.warning("Cannot build Fairy fixed core prompt; missing sources: %s", ", ".join(missing_sources))
            return

        newest_source_mtime = max(path.stat().st_mtime for path, _ in sources)
        if (
            fixed_wav_path.exists()
            and fixed_wav_path.stat().st_mtime >= newest_source_mtime
            and not text_changed
        ):
            return

        sample_rate = 16000
        gap = torch.zeros((1, int(0.12 * sample_rate)), dtype=torch.float32)
        wav_parts: list[torch.Tensor] = []
        for audio_path, _ in sources:
            wav, source_rate = torchaudio.load(str(audio_path))
            if wav.ndim == 2 and wav.shape[0] > 1:
                wav = wav.mean(dim=0, keepdim=True)
            if source_rate != sample_rate:
                wav = torchaudio.functional.resample(wav, source_rate, sample_rate)
            wav = self._normalize_prompt_audio(wav)
            if wav.shape[-1] <= 0:
                continue
            if wav_parts:
                wav_parts.append(gap.clone())
            wav_parts.append(wav)

        if not wav_parts:
            logger.warning("Fairy fixed core prompt rebuild skipped because no usable source clips were loaded.")
            return

        merged = torch.cat(wav_parts, dim=1)
        sf.write(fixed_wav_path, merged.squeeze(0).cpu().numpy(), sample_rate)
        logger.info(
            "Rebuilt Fairy fixed core prompt asset at %s from %d source clips",
            fixed_wav_path,
            len(sources),
        )

    def _prepare_prompt_assets(self) -> tuple[str, str | None]:
        self.prompt_candidate_count = 0
        self.prompt_source = None
        self.prompt_text_source = None
        self.prompt_duration_sec = 0.0
        self.prompt_segment_count = 0
        self._ensure_fixed_core_prompt_asset()

        candidates = list_prompt_candidates(voice_config.voice_prompt_dir)
        self.prompt_candidate_count = len(candidates)
        if not candidates:
            return "", None

        if self.requested_profile not in CLONE_PROFILES:
            return "", None

        selected_candidates = self._select_prompt_bundle_candidates(candidates)
        wav_parts: list[torch.Tensor] = []
        text_parts: list[str] = []
        source_parts: list[str] = []
        text_source_parts: list[str] = []
        total_duration = 0.0
        sample_rate = 16000
        silence_gap = torch.zeros((1, int(0.12 * sample_rate)), dtype=torch.float32)

        for candidate in selected_candidates:
            segment_text, segment_wav = self._prepare_prompt_candidate(candidate)
            if not segment_text or segment_wav is None or segment_wav.shape[-1] <= 0:
                continue
            segment_duration = segment_wav.shape[-1] / sample_rate
            projected_duration = total_duration + segment_duration + (0.12 if wav_parts else 0.0)
            if wav_parts and projected_duration > 18.0 and total_duration >= (voice_config.prompt_min_sec + 2.0):
                continue
            if wav_parts:
                wav_parts.append(silence_gap.clone())
                total_duration += 0.12
            wav_parts.append(segment_wav)
            text_parts.append(segment_text)
            source_parts.append(str(candidate.audio_path))
            text_source_parts.append(candidate.text_source)
            total_duration += segment_duration
            self.prompt_segment_count += 1
            if total_duration >= min(18.0, max(voice_config.prompt_target_sec, 14.0)):
                break

        if not wav_parts:
            logger.warning("No usable Fairy prompt candidates were prepared for cloning.")
            return "", None

        wav = torch.cat(wav_parts, dim=1)
        prompt_text = " ".join(part.strip() for part in text_parts if part.strip()).strip()
        self.prompt_source = " | ".join(source_parts)
        self.prompt_text_source = " | ".join(text_source_parts)
        self.prompt_duration_sec = wav.shape[-1] / sample_rate if sample_rate else 0.0
        logger.info(
            "Prepared Fairy prompt bundle with %d segments, %.2fs total: %s",
            self.prompt_segment_count,
            self.prompt_duration_sec,
            self.prompt_source,
        )
        if self.prompt_duration_sec < voice_config.prompt_min_sec:
            logger.warning("Prepared prompt bundle is too short for cloning: %.2fs", self.prompt_duration_sec)
            return "", None

        prepared_dir = voice_config.prepared_prompt_dir
        prepared_dir.mkdir(parents=True, exist_ok=True)
        prepared_path = prepared_dir / "fairy_prompt_bundle_prepared.wav"
        sf.write(prepared_path, wav.squeeze(0).cpu().numpy(), 16000)
        return prompt_text, str(prepared_path)

    def _select_prompt_bundle_candidates(self, candidates: list[Any]) -> list[Any]:
        if not candidates:
            return []
        fixed_core = [item for item in candidates if str(item.audio_path.stem).lower() == FIXED_CORE_STEM]
        if fixed_core:
            return fixed_core[:1]
        explicit = [item for item in candidates if str(item.audio_path.stem).lower() == "fairy_prompt"]
        mono_mid = [
            item
            for item in candidates
            if item not in explicit and item.channels == 1 and 1.5 <= float(item.duration_sec) <= 10.5
        ]
        short_clean = [
            item
            for item in candidates
            if item not in explicit and item not in mono_mid and 1.2 <= float(item.duration_sec) <= 5.5
        ]
        ordered = [*explicit, *mono_mid, *short_clean, *candidates]
        selected: list[Any] = []
        seen: set[str] = set()
        for item in ordered:
            key = str(item.audio_path)
            if key in seen:
                continue
            seen.add(key)
            selected.append(item)
            if len(selected) >= 5:
                break
        return selected

    def _prepare_prompt_candidate(self, candidate: Any) -> tuple[str, torch.Tensor | None]:
        prompt_text = str(getattr(candidate, "text", "") or "").strip()
        if not prompt_text:
            return "", None
        wav, sample_rate = torchaudio.load(str(candidate.audio_path))
        if wav.ndim == 2 and wav.shape[0] > 1:
            wav = wav.mean(dim=0, keepdim=True)
        if sample_rate != 16000:
            wav = torchaudio.functional.resample(wav, sample_rate, 16000)
            sample_rate = 16000
        audio_path = str(getattr(candidate, "audio_path", "") or "")
        if Path(audio_path).stem.lower() != FIXED_CORE_STEM:
            prompt_text, wav = self._refine_clone_prompt(
                prompt_text,
                wav,
                sample_rate,
                source_name=audio_path,
            )
        wav = self._normalize_prompt_audio(wav)
        segment_duration = wav.shape[-1] / sample_rate if sample_rate else 0.0
        if segment_duration < 1.0:
            return "", None
        return prompt_text, wav

    def _normalize_prompt_audio(self, wav: torch.Tensor) -> torch.Tensor:
        if wav.ndim != 2 or wav.shape[0] != 1 or wav.shape[-1] <= 0:
            return wav
        tensor = wav.clone()
        peak = float(tensor.abs().max().item()) if tensor.numel() else 0.0
        if peak > 0.0:
            tensor = tensor * min(0.92 / peak, 1.6)
        rms = float(torch.sqrt(torch.mean(tensor.pow(2))).item()) if tensor.numel() else 0.0
        if rms > 0.0:
            target_rms = 0.12
            tensor = tensor * min(target_rms / rms, 1.4)
        fade_frames = min(int(0.015 * 16000), tensor.shape[-1] // 8)
        if fade_frames > 2:
            fade = torch.linspace(0.0, 1.0, fade_frames, dtype=tensor.dtype, device=tensor.device)
            tensor[:, :fade_frames] *= fade
            tensor[:, -fade_frames:] *= torch.flip(fade, dims=[0])
        return tensor

    def _register_zero_shot_speaker(self) -> None:
        if self.requested_profile not in CLONE_PROFILES:
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

    def _resolve_active_profile(self) -> str:
        if self.requested_profile in CLONE_PROFILES and self.clone_speaker_ready:
            return self.requested_profile
        return "sft_clean"

    def _prime_voice_path(self) -> None:
        if self.model is None:
            return
        text = "主人，我已准备就绪。"
        try:
            iterator = self._build_inference_iterator(text, profile=self.active_profile)
            next(iterator, None)
        except Exception:
            logger.exception("Failed to prime CosyVoice inference path")

    def _clip_prompt_text(self, text: str) -> str:
        sentence = re.split(r"(?<=[\u3002\uff01\uff1f.!?])", text, maxsplit=1)[0].strip()
        return sentence or text[:48].strip()

    def _refine_clone_prompt(
        self,
        prompt_text: str,
        wav: torch.Tensor,
        sample_rate: int,
        *,
        source_name: str = "",
    ) -> tuple[str, torch.Tensor]:
        full_text = " ".join(str(prompt_text or "").split()).strip()
        if not full_text:
            return self._clip_prompt_text(prompt_text), wav
        sentence_entries = self._build_prompt_sentence_entries(full_text)
        if not sentence_entries:
            return self._clip_prompt_text(prompt_text), wav

        selected_entries = self._select_prompt_sentence_entries(sentence_entries, source_name=source_name)
        if not selected_entries:
            selected_entries = [sentence_entries[0]]

        total_chars = max(len(full_text), 1)
        total_duration = wav.shape[-1] / max(sample_rate, 1)
        gap = torch.zeros((1, int(0.08 * sample_rate)), dtype=wav.dtype, device=wav.device)
        wav_parts: list[torch.Tensor] = []
        selected_texts: list[str] = []
        for idx, entry in enumerate(selected_entries):
            start_ratio = entry["start_char"] / total_chars
            end_ratio = entry["end_char"] / total_chars
            pad_frames = int(0.12 * sample_rate)
            start_frame = max(0, int(wav.shape[-1] * start_ratio) - pad_frames)
            end_frame = min(wav.shape[-1], int(wav.shape[-1] * end_ratio) + pad_frames)
            if end_frame <= start_frame:
                continue
            if idx > 0:
                wav_parts.append(gap.clone())
            wav_parts.append(wav[:, start_frame:end_frame])
            selected_texts.append(str(entry["text"]))

        if not wav_parts:
            return self._clip_prompt_text(prompt_text), wav

        refined_wav = torch.cat(wav_parts, dim=1)
        selected_text = "".join(selected_texts).strip() or self._clip_prompt_text(prompt_text)
        logger.info(
            "Refined clone prompt from %.2fs/%d chars to %.2fs/%d chars using %d sentence spans from %s",
            total_duration,
            total_chars,
            refined_wav.shape[-1] / max(sample_rate, 1),
            len(selected_text),
            len(selected_entries),
            Path(source_name).name or "prompt",
        )
        return selected_text, refined_wav

    def _build_prompt_sentence_entries(self, text: str) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        cursor = 0
        for match in re.finditer(r".+?(?:[\u3002\uff01\uff1f.!?]|$)", text):
            sentence = match.group(0).strip()
            if not sentence:
                continue
            start = text.find(sentence, cursor)
            if start < 0:
                start = cursor
            end = start + len(sentence)
            entries.append({"text": sentence, "start_char": start, "end_char": end})
            cursor = end
        return entries

    def _select_prompt_sentence_entries(self, entries: list[dict[str, Any]], *, source_name: str) -> list[dict[str, Any]]:
        if len(entries) <= 2:
            return entries

        source_label = Path(source_name).stem.lower()
        scored: list[tuple[float, int, dict[str, Any]]] = []
        for idx, entry in enumerate(entries):
            text = str(entry["text"])
            length = len(text)
            punctuation_penalty = text.count("，") + text.count(",")
            score = 24.0 - abs(length - 16) * 0.75 - punctuation_penalty * 1.5
            if any(token in text for token in ("主人", "Fairy", "fairy", "声音", "人工智能")):
                score += 4.0
            if any(token in text for token in ("叮咚", "快递", "摄像头", "马桶", "雕塑")):
                score -= 8.0
            if "欢迎" in source_label and any(token in text for token in ("Fairy", "主人", "人工智能", "开发代号")):
                score += 5.0
            scored.append((score, idx, entry))

        selected = sorted(scored, key=lambda item: item[0], reverse=True)[:2]
        selected.sort(key=lambda item: item[1])
        return [item[2] for item in selected]

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

    def _mecha_preset_name(self, *, system_voice: bool) -> str:
        return DEFAULT_SYSTEM_MECHA_PRESET if system_voice else DEFAULT_REPLY_MECHA_PRESET

    def _mecha_preset(self, *, system_voice: bool) -> dict[str, Any]:
        return MECHA_PRESETS[self._mecha_preset_name(system_voice=system_voice)]

    def _apply_presence_tilt(self, audio: np.ndarray, *, amount: float, window: int) -> np.ndarray:
        if audio.size == 0 or amount <= 0.0 or window <= 1:
            return audio
        kernel = np.ones(int(window), dtype=np.float32) / float(window)
        smoothed = np.convolve(audio, kernel, mode="same").astype(np.float32, copy=False)
        presence = audio - smoothed
        return (audio + (presence * amount)).astype(np.float32, copy=False)

    def _apply_voice_profile(self, audio: np.ndarray, sample_rate: int, *, system_voice: bool) -> np.ndarray:
        if self.active_profile != "clone_mecha" or audio.size == 0:
            return audio
        safe_rate = max(int(sample_rate), 1)
        time_axis = np.arange(audio.shape[0], dtype=np.float32) / float(safe_rate)
        preset = self._mecha_preset(system_voice=system_voice)
        carrier_freq = float(preset["carrier_freq"])
        modulation_depth = float(preset["modulation_depth"])
        crush_steps = float(preset["crush_steps"])
        delay_limit = max(1, safe_rate // max(int(preset["delay_divisor"]), 1))
        dry, crush_mix, delay_mix, edge_mix = preset["mix"]
        drive_gain = float(preset["drive_gain"])
        output_gain = float(preset["output_gain"])
        presence_mix = float(preset.get("presence_mix", 0.0))
        smoothing_window = int(preset.get("smoothing_window", 0))
        carrier = np.sin(2.0 * np.pi * carrier_freq * time_axis).astype(np.float32)
        modulated = audio * ((1.0 - modulation_depth) + modulation_depth * carrier)
        crushed = np.round(modulated * crush_steps) / crush_steps
        delay_frames = max(1, min(audio.shape[0] // 12, delay_limit))
        delayed = np.roll(crushed, delay_frames)
        delayed[:delay_frames] = 0.0
        edge = np.concatenate(([0.0], np.diff(audio))).astype(np.float32)
        processed = (dry * audio) + (crush_mix * crushed) + (delay_mix * delayed) + (edge_mix * edge)
        processed = self._apply_presence_tilt(processed, amount=presence_mix, window=smoothing_window)
        processed = np.tanh(processed * drive_gain).astype(np.float32) * output_gain
        peak = float(np.max(np.abs(processed))) if processed.size else 0.0
        if peak > 0.98:
            processed = processed * (0.98 / peak)
        return processed.astype(np.float32, copy=False)

    def _encode_wav_bytes(self, audio: np.ndarray, sample_rate: int) -> bytes:
        buffer = io.BytesIO()
        sf.write(buffer, audio, sample_rate, format="WAV")
        return buffer.getvalue()

    def _resolve_device(self) -> str:
        configured = voice_config.cosyvoice_device.strip().lower()
        if configured == "cuda" and torch.cuda.is_available():
            return "cuda"
        return "cpu"

    def _normalize_profile(self, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in VALID_PROFILES:
            return normalized
        return "clone_mecha"


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
