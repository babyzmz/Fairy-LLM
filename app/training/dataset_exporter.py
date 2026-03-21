from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .dataset_models import TrainingEntry
from .dataset_parser import entries_to_numbered_text


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ExportPaths:
    output_dir: Path
    audio_dir: Path
    metadata_csv: Path
    metadata_jsonl: Path
    train_list: Path
    raw_text: Path


def export_metadata(
    entries: Iterable[TrainingEntry],
    output_dir: Path,
    *,
    raw_text: str | None = None,
) -> ExportPaths:
    items = list(entries)
    output_dir.mkdir(parents=True, exist_ok=True)
    audio_dir = output_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    metadata_csv = output_dir / "metadata.csv"
    metadata_jsonl = output_dir / "metadata.jsonl"
    train_list = output_dir / "train_list.txt"
    raw_text_path = output_dir / "raw_text.txt"

    fieldnames = [
        "id",
        "text",
        "audio_path",
        "category",
        "length_type",
        "emotion",
        "status",
        "enabled",
        "note",
        "generated_at",
        "voice_provider",
        "error_message",
    ]

    with metadata_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for entry in items:
            writer.writerow(_entry_row(entry))

    with metadata_jsonl.open("w", encoding="utf-8") as handle:
        for entry in items:
            handle.write(json.dumps(_entry_row(entry), ensure_ascii=False) + "\n")

    with train_list.open("w", encoding="utf-8") as handle:
        for entry in items:
            if entry.audio_path:
                handle.write(f"{entry.audio_path}|{entry.text}\n")

    source_text = raw_text if raw_text is not None else entries_to_numbered_text(items)
    raw_text_path.write_text(source_text, encoding="utf-8")

    logger.info("export_metadata_done output_dir=%s count=%s", output_dir, len(items))
    return ExportPaths(
        output_dir=output_dir,
        audio_dir=audio_dir,
        metadata_csv=metadata_csv,
        metadata_jsonl=metadata_jsonl,
        train_list=train_list,
        raw_text=raw_text_path,
    )


def prepare_cosyvoice3_dataset(
    entries: Iterable[TrainingEntry],
    output_dir: Path,
    *,
    raw_text: str | None = None,
) -> ExportPaths:
    return export_metadata(entries, output_dir, raw_text=raw_text)


def _entry_row(entry: TrainingEntry) -> dict[str, object]:
    return {
        "id": entry.id,
        "text": entry.text,
        "audio_path": entry.audio_path or "",
        "category": entry.category or "",
        "length_type": entry.length_type or "",
        "emotion": entry.emotion or "",
        "status": entry.status,
        "enabled": entry.enabled,
        "note": entry.note,
        "generated_at": entry.generated_at or "",
        "voice_provider": entry.voice_provider or "",
        "error_message": entry.error_message,
    }
