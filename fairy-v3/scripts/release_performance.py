from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

CORE_READY_BUDGET_MS = 3_000
INITIAL_GZIP_BUDGET_BYTES = 800 * 1024
_STATIC_IMPORT = re.compile(r"(?:\bfrom|\bimport)\s*['\"]([^'\"]+)['\"]")


@dataclass(frozen=True, slots=True)
class BundleMeasurement:
    files: tuple[Path, ...]
    gzip_bytes: int


@dataclass(frozen=True, slots=True)
class CoreReadyMeasurement:
    elapsed_ms: float
    protocol: str


class _EntryParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.references: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "script" and values.get("type") == "module" and values.get("src"):
            self.references.append(str(values["src"]))
        if tag == "link" and values.get("rel") == "stylesheet" and values.get("href"):
            self.references.append(str(values["href"]))


def measure_initial_gzip(dist_root: Path) -> BundleMeasurement:
    root = dist_root.resolve(strict=True)
    manifest_path = root / ".vite" / "manifest.json"
    measured = (
        _manifest_files(root, manifest_path)
        if manifest_path.is_file()
        else _html_entry_files(root)
    )
    files = tuple(sorted((path.relative_to(root) for path in measured), key=str))
    gzip_bytes = sum(
        len(gzip.compress((root / path).read_bytes(), compresslevel=9, mtime=0))
        for path in files
    )
    return BundleMeasurement(files=files, gzip_bytes=gzip_bytes)


def _manifest_files(root: Path, manifest_path: Path) -> set[Path]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("Vite manifest must be an object")
    pending = [
        key
        for key, value in manifest.items()
        if isinstance(value, dict) and value.get("isEntry") is True
    ]
    if not pending:
        raise ValueError("Vite manifest has no entry chunk")
    visited: set[str] = set()
    measured: set[Path] = set()
    while pending:
        key = pending.pop()
        if key in visited:
            continue
        visited.add(key)
        item = manifest.get(key)
        if not isinstance(item, dict):
            raise ValueError(f"Vite manifest dependency is missing: {key}")
        references = [item.get("file"), *(item.get("css") or [])]
        for reference in references:
            if not isinstance(reference, str):
                raise ValueError(f"Vite manifest asset is invalid: {key}")
            path = _resolve_reference(root, root, f"/{reference}")
            if path.suffix in {".js", ".mjs", ".css"}:
                measured.add(path)
        for dependency in (
            *(item.get("imports") or []),
            *(item.get("dynamicImports") or []),
        ):
            if not isinstance(dependency, str):
                raise ValueError(f"Vite manifest dependency is invalid: {key}")
            pending.append(dependency)
    return measured


def _html_entry_files(root: Path) -> set[Path]:
    index_path = root / "index.html"
    parser = _EntryParser()
    parser.feed(index_path.read_text(encoding="utf-8"))
    pending = [
        _resolve_reference(root, root, reference) for reference in parser.references
    ]
    measured: set[Path] = set()
    while pending:
        path = pending.pop()
        if path in measured:
            continue
        measured.add(path)
        if path.suffix not in {".js", ".mjs"}:
            continue
        source = path.read_text(encoding="utf-8")
        for match in _STATIC_IMPORT.finditer(source):
            reference = match.group(1)
            if Path(urlsplit(reference).path).suffix not in {".js", ".mjs", ".css"}:
                continue
            pending.append(_resolve_reference(root, path.parent, reference))
    return measured


def measure_core_ready(timeout_seconds: float = 3.0) -> CoreReadyMeasurement:
    request = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "health", "params": {}},
        separators=(",", ":"),
    )
    with tempfile.TemporaryDirectory(prefix="fairy-v3-ready-") as data_dir:
        environment = dict(os.environ)
        environment["FAIRY_V3_DATA_DIR"] = data_dir
        started = time.perf_counter()
        process = subprocess.Popen(
            [sys.executable, "-m", "fairy_capabilities.stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        try:
            assert process.stdin is not None
            assert process.stdout is not None
            process.stdin.write(f"{request}\n")
            process.stdin.flush()
            executor = ThreadPoolExecutor(max_workers=1)
            try:
                response_line = executor.submit(process.stdout.readline).result(
                    timeout=timeout_seconds
                )
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
            elapsed_ms = (time.perf_counter() - started) * 1_000
            response = json.loads(response_line)
            result = response.get("result")
            if response.get("id") != 1 or not isinstance(result, dict):
                raise RuntimeError(
                    "Core readiness probe returned an invalid JSON-RPC response"
                )
            if result.get("status") != "ok":
                raise RuntimeError("Core readiness probe did not report status=ok")
            return CoreReadyMeasurement(
                elapsed_ms=elapsed_ms,
                protocol=str(result.get("protocol", "")),
            )
        except FutureTimeoutError as error:
            raise RuntimeError("Core readiness probe exceeded its timeout") from error
        finally:
            if process.stdin is not None:
                process.stdin.close()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)


def _resolve_reference(root: Path, base: Path, reference: str) -> Path:
    parsed = urlsplit(reference)
    if parsed.scheme or parsed.netloc:
        raise ValueError("initial bundle contains an external reference")
    relative = Path(parsed.path.lstrip("/"))
    candidate = (
        root / relative if parsed.path.startswith("/") else base / relative
    ).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError(
            "initial bundle reference is outside the desktop dist directory"
        )
    if not candidate.is_file():
        raise ValueError(
            f"initial bundle reference is missing: {candidate.relative_to(root)}"
        )
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure Fairy V3 release performance gates"
    )
    parser.add_argument(
        "--desktop-dist",
        type=Path,
        default=Path(__file__).parents[1] / "desktop" / "dist",
    )
    arguments = parser.parse_args()
    core = measure_core_ready(timeout_seconds=CORE_READY_BUDGET_MS / 1_000)
    bundle = measure_initial_gzip(arguments.desktop_dist)
    print(
        f"Core ready: {core.elapsed_ms:.1f} ms / {CORE_READY_BUDGET_MS} ms; "
        f"protocol={core.protocol}"
    )
    print(
        f"Initial renderer gzip: {bundle.gzip_bytes / 1024:.1f} KiB / "
        f"{INITIAL_GZIP_BUDGET_BYTES / 1024:.0f} KiB; files={len(bundle.files)}"
    )
    if core.elapsed_ms > CORE_READY_BUDGET_MS:
        return 1
    if bundle.gzip_bytes > INITIAL_GZIP_BUDGET_BYTES:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
