from __future__ import annotations

import logging
from pathlib import Path

from app.config import BASE_DIR


def configure_logging(log_path: Path | None = None) -> Path:
    target = log_path or (BASE_DIR / "data" / "app.log")
    target.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    if root.handlers:
        return target

    formatter = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")

    file_handler = logging.FileHandler(target, encoding="utf-8")
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)
    return target
