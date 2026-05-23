from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import soundfile as sf

from app.config import system_config


_AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}


@dataclass(slots=True)
class VoicePromptCandidate:
    audio_path: Path
    text_path: Path | None
    text_source: str
    text: str
    sample_rate: int
    channels: int
    duration_sec: float
    peak: float
    score: float


def list_prompt_candidates(root: Path) -> list[VoicePromptCandidate]:
    if not root.exists():
        return []
    candidates: list[VoicePromptCandidate] = []
    for audio_path in sorted(root.iterdir()):
        if not audio_path.is_file() or audio_path.suffix.lower() not in _AUDIO_EXTENSIONS:
            continue
        try:
            prompt_text, text_path, text_source = _load_prompt_text(audio_path)
            data, sample_rate = sf.read(str(audio_path), always_2d=False)
        except Exception:
            continue
        if not prompt_text:
            continue
        frames = len(data) if getattr(data, "ndim", 1) == 1 else len(data[:, 0])
        channels = 1 if getattr(data, "ndim", 1) == 1 else int(data.shape[1])
        duration_sec = frames / sample_rate if sample_rate else 0.0
        if duration_sec <= 0.0:
            continue
        peak = float(abs(data).max()) if hasattr(data, "max") else 0.0
        score = _score_candidate(
            duration_sec=duration_sec,
            sample_rate=sample_rate,
            channels=channels,
            peak=peak,
            text_length=len(prompt_text),
        )
        if audio_path.stem.lower() == "fairy_clone_core":
            score += 100.0
        candidates.append(
            VoicePromptCandidate(
                audio_path=audio_path,
                text_path=text_path,
                text_source=text_source,
                text=prompt_text,
                sample_rate=sample_rate,
                channels=channels,
                duration_sec=duration_sec,
                peak=peak,
                score=score,
            )
        )
    return sorted(
        candidates,
        key=lambda item: (item.score, item.duration_sec, -item.channels, item.audio_path.name.lower()),
        reverse=True,
    )


def select_best_prompt_candidate(root: Path) -> VoicePromptCandidate | None:
    candidates = list_prompt_candidates(root)
    return candidates[0] if candidates else None


def _read_prompt_text(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8-sig").split()).strip()


def _load_prompt_text(audio_path: Path) -> tuple[str, Path | None, str]:
    text_path = audio_path.with_suffix(".txt")
    if text_path.exists():
        return _read_prompt_text(text_path), text_path, str(text_path)
    inferred = _infer_prompt_text(audio_path)
    return inferred, None, f"inferred:{audio_path.name}" if inferred else ""


def _infer_prompt_text(audio_path: Path) -> str:
    normalized_stem = _normalize_label(audio_path.stem)
    if not normalized_stem:
        return ""
    if normalized_stem == _normalize_label("欢迎语音"):
        return " ".join(system_config.welcome_voice_text.split()).strip()
    for line in system_config.startup_status_lines:
        if normalized_stem == _normalize_label(line):
            return line.strip()
    return ""


def _normalize_label(value: str) -> str:
    text = str(value or "").strip().lower()
    return "".join(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]+", text))


def _score_candidate(*, duration_sec: float, sample_rate: int, channels: int, peak: float, text_length: int) -> float:
    duration_target = 20.0
    duration_score = max(0.0, 45.0 - min(abs(duration_sec - duration_target), 20.0) * 2.0)
    channel_score = 25.0 if channels == 1 else 12.0 if channels == 2 else 0.0
    sample_rate_score = 20.0 if sample_rate == 16000 else 14.0 if sample_rate in {22050, 24000, 32000, 44100, 48000} else 8.0
    clipping_penalty = 18.0 if peak >= 0.98 else 0.0
    text_score = min(12.0, max(0.0, text_length / 12.0))
    return duration_score + channel_score + sample_rate_score + text_score - clipping_penalty
