"""Debug helpers for WebResearchAgent."""
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)


def log_pipeline_step(step: str, **kwargs) -> None:
    parts = " ".join(f"{k}={v!r}" for k, v in kwargs.items())
    logger.info("web_research_%s %s", step, parts)
