from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from app.ai.voice.prompt_assets import select_best_prompt_candidate
from app.ai.voice.fairy_tts import FairyTTS
from app.config import voice_config

from .dataset_exporter import export_metadata
from .dataset_models import TrainingEntry


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class BatchGenerationSummary:
    total: int
    processed: int
    succeeded: int
    failed: int
    skipped: int
    output_dir: Path


def generate_training_audio(
    entries: Iterable[TrainingEntry],
    output_dir: Path,
    *,
    raw_text: str | None = None,
    only_missing: bool = False,
    selected_ids: set[int] | None = None,
    stop_requested: Callable[[], bool] | None = None,
    item_callback: Callable[[TrainingEntry, int, int], None] | None = None,
) -> tuple[list[TrainingEntry], BatchGenerationSummary]:
    generator = BatchTTSGenerator()
    return generator.generate(
        list(entries),
        output_dir=output_dir,
        raw_text=raw_text,
        only_missing=only_missing,
        selected_ids=selected_ids,
        stop_requested=stop_requested,
        item_callback=item_callback,
    )


class BatchTTSGenerator:
    def __init__(self) -> None:
        self._tts: FairyTTS | None = None

    def generate(
        self,
        entries: list[TrainingEntry],
        *,
        output_dir: Path,
        raw_text: str | None = None,
        only_missing: bool = False,
        selected_ids: set[int] | None = None,
        stop_requested: Callable[[], bool] | None = None,
        item_callback: Callable[[TrainingEntry, int, int], None] | None = None,
    ) -> tuple[list[TrainingEntry], BatchGenerationSummary]:
        selected = selected_ids or set()
        audio_dir = output_dir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)

        if voice_config.backend != "cosyvoice2_service":
            raise RuntimeError(
                f"当前训练导出要求使用 CosyVoice2，检测到 voice backend={voice_config.backend}。"
            )
        if not voice_config.cosyvoice_python_path.exists():
            raise RuntimeError(
                f"CosyVoice Python 运行时不存在：{voice_config.cosyvoice_python_path}"
            )
        if voice_config.uses_clone_profile():
            candidate = select_best_prompt_candidate(voice_config.voice_prompt_dir)
            if candidate is None:
                raise FileNotFoundError(f"缺少可用 Fairy prompt 对：{voice_config.voice_prompt_dir}")

        self._tts = FairyTTS()
        succeeded = 0
        failed = 0
        skipped = 0
        processed = 0
        provider_name = _provider_name()

        logger.info(
            "batch_generate_start total=%s output_dir=%s provider=%s",
            len(entries),
            output_dir,
            provider_name,
        )
        try:
            for entry in entries:
                if stop_requested and stop_requested():
                    if entry.status == "queued":
                        entry.status = "skipped"
                    break

                should_process = entry.enabled
                if selected and entry.id not in selected:
                    should_process = False
                if only_missing and entry.audio_path:
                    should_process = False

                if not should_process:
                    skipped += 1
                    processed += 1
                    if item_callback is not None:
                        item_callback(entry, processed, len(entries))
                    continue

                entry.status = "generating"
                logger.info("batch_generate_item_start id=%s text=%s", entry.id, entry.text[:80])
                if item_callback is not None:
                    item_callback(entry, processed, len(entries))

                try:
                    tmp_audio = self._tts.synthesize_to_file(entry.text)
                    target_name = f"{entry.id:04d}.wav"
                    relative_audio = Path("audio") / target_name
                    target_path = output_dir / relative_audio
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(tmp_audio, target_path)
                    tmp_audio.unlink(missing_ok=True)
                    entry.touch_generated(
                        audio_path=relative_audio.as_posix(),
                        voice_provider=provider_name,
                    )
                    succeeded += 1
                    logger.info(
                        "batch_generate_item_done id=%s audio_path=%s",
                        entry.id,
                        entry.audio_path,
                    )
                except Exception as exc:  # noqa: BLE001
                    entry.status = "failed"
                    entry.error_message = str(exc)
                    failed += 1
                    logger.info(
                        "batch_generate_item_failed id=%s error=%s",
                        entry.id,
                        entry.error_message,
                    )
                finally:
                    processed += 1
                    if item_callback is not None:
                        item_callback(entry, processed, len(entries))
        finally:
            if self._tts is not None:
                self._tts.shutdown()
                self._tts = None

        export_metadata(entries, output_dir, raw_text=raw_text)
        summary = BatchGenerationSummary(
            total=len(entries),
            processed=processed,
            succeeded=succeeded,
            failed=failed,
            skipped=skipped,
            output_dir=output_dir,
        )
        return entries, summary


def _provider_name() -> str:
    return f"{voice_config.backend}/{voice_config.voice_profile}"
