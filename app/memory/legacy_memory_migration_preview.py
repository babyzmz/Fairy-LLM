from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from app.config import memory_config
from app.rag.importance_scorer import DECISION_HINTS, NOISE_HINTS


logger = logging.getLogger(__name__)


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def _load_text_rows(conn: sqlite3.Connection, table_name: str, column_names: list[str]) -> list[str]:
    if not _table_exists(conn, table_name):
        return []
    columns = ", ".join(column_names)
    rows = conn.execute(f"SELECT {columns} FROM {table_name}").fetchall()
    payloads: list[str] = []
    for row in rows:
        joined = "\n".join(str(value or "").strip() for value in row if str(value or "").strip())
        if joined:
            payloads.append(joined)
    return payloads


def build_legacy_preview(db_path: Path | None = None) -> dict[str, Any]:
    path = Path(db_path or memory_config.db_path)
    if not path.exists():
        return {
            "db_path": str(path),
            "exists": False,
            "total_count": 0,
            "decision_like_count": 0,
            "noise_estimate": 0,
            "sample_titles": [],
        }

    conn = sqlite3.connect(path)
    try:
        corpus: list[str] = []
        corpus.extend(_load_text_rows(conn, "structured_memory", ["type", "scope", "content"]))
        corpus.extend(_load_text_rows(conn, "project_memory", ["project", "module", "summary"]))
        corpus.extend(_load_text_rows(conn, "experience_memory", ["content"]))

        decision_like_count = 0
        noise_estimate = 0
        samples: list[str] = []
        for item in corpus:
            lowered = item.lower()
            if any(token.lower() in lowered for token in DECISION_HINTS):
                decision_like_count += 1
            if any(token.lower() in lowered for token in NOISE_HINTS):
                noise_estimate += 1
            if len(samples) < 8:
                first_line = item.splitlines()[0].strip()
                if first_line:
                    samples.append(first_line[:120])
        result = {
            "db_path": str(path),
            "exists": True,
            "total_count": len(corpus),
            "decision_like_count": decision_like_count,
            "noise_estimate": noise_estimate,
            "sample_titles": samples,
        }
        logger.info(
            "legacy_preview_scan_completed total=%s decision_like=%s noise=%s",
            result["total_count"],
            result["decision_like_count"],
            result["noise_estimate"],
        )
        return result
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview legacy Fairy memory migration candidates.")
    parser.add_argument("--db", default=str(memory_config.db_path), help="Path to legacy fairy_memory.db")
    parser.add_argument("--json", action="store_true", help="Print JSON only")
    parser.add_argument("--output", default="", help="Optional file path to save JSON preview")
    args = parser.parse_args()

    preview = build_legacy_preview(Path(args.db))
    payload = json.dumps(preview, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
    if args.json or args.output:
        print(payload)
        return

    print(f"Legacy DB: {preview['db_path']}")
    print(f"Exists: {preview['exists']}")
    print(f"Total count: {preview['total_count']}")
    print(f"Decision-like count: {preview['decision_like_count']}")
    print(f"Noise estimate: {preview['noise_estimate']}")
    if preview["sample_titles"]:
        print("Sample titles:")
        for item in preview["sample_titles"]:
            print(f"- {item}")


if __name__ == "__main__":
    main()
