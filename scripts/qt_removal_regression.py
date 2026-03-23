from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ROOT_STR = str(ROOT)
if ROOT_STR not in sys.path:
    sys.path.insert(0, ROOT_STR)
SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "cosyvoice_env",
    "node_modules",
    "__pycache__",
    "vendor",
    ".vendor",
}
COMPAT_FIELDS = ("assistant_text", "structured", "sources", "skill_name")


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str


def _iter_source_files() -> list[Path]:
    paths: list[Path] = []
    for base in (ROOT / "app", ROOT / "fairy-desktop" / "src"):
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            if path.suffix.lower() in {".py", ".ts", ".tsx"}:
                paths.append(path)
    paths.append(ROOT / "main.py")
    return [path for path in paths if path.exists()]


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def collect_qt_dependencies() -> dict[str, list[str]]:
    qt_runtime: list[str] = []
    qt_shell_only: list[str] = []
    for path in _iter_source_files():
        text = _read_text(path)
        if "PySide6" not in text:
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel == "main.py" or rel == "app/assistant_mode.py":
            qt_runtime.append(rel)
        elif rel.startswith("app/ui/"):
            qt_shell_only.append(rel)
    return {
        "runtime_coupled": sorted(qt_runtime),
        "qt_shell_only": sorted(qt_shell_only),
    }


def collect_compat_field_usage() -> dict[str, list[str]]:
    frontend: list[str] = []
    backend: list[str] = []
    qt_shell: list[str] = []
    compat_patterns = tuple(f'"{field}"' for field in COMPAT_FIELDS) + tuple(f"'{field}'" for field in COMPAT_FIELDS)
    for path in _iter_source_files():
        text = _read_text(path)
        if not any(pattern in text for pattern in compat_patterns):
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("fairy-desktop/src/"):
            frontend.append(rel)
        elif rel == "app/assistant_mode.py" or rel.startswith("app/ui/") or rel == "main.py":
            qt_shell.append(rel)
        else:
            backend.append(rel)
    return {
        "frontend": sorted(set(frontend)),
        "backend_or_runtime": sorted(set(backend)),
        "qt_shell": sorted(set(qt_shell)),
    }


def request_json(base_url: str, method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(f"{base_url}{path}", data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw or "{}")
        except json.JSONDecodeError:
            return exc.code, {"errors": [{"code": "http_error", "message": raw or str(exc)}]}


def request_text(base_url: str, path: str) -> tuple[int, str, str]:
    req = urllib.request.Request(f"{base_url}{path}", method="GET")
    with urllib.request.urlopen(req, timeout=90) as response:
        return response.status, response.headers.get_content_type(), response.read().decode("latin-1", errors="ignore")


def collect_sse_events(
    base_url: str,
    payload: dict[str, Any],
    *,
    cancel_after_first_progress: bool = False,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    cancelled = False
    req = urllib.request.Request(
        f"{base_url}/chat/stream",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as response:
        current_event = ""
        current_data: list[str] = []
        while True:
            line = response.readline()
            if not line:
                break
            decoded = line.decode("utf-8", errors="replace").rstrip("\r\n")
            if decoded.startswith("event:"):
                current_event = decoded[6:].strip()
                continue
            if decoded.startswith("data:"):
                current_data.append(decoded[5:].strip())
                continue
            if decoded:
                continue
            if not current_event or not current_data:
                current_event = ""
                current_data = []
                continue
            payload_text = "\n".join(current_data)
            event = json.loads(payload_text)
            events.append(event)
            if cancel_after_first_progress and current_event == "progress" and not cancelled:
                request_json(
                    base_url,
                    "POST",
                    "/system/actions",
                    {"action": "cancel_current_request", "payload": {}},
                )
                cancelled = True
            if current_event in {"message_end", "error"}:
                break
            current_event = ""
            current_data = []
    return events


def wait_for_health(base_url: str, *, timeout_seconds: float = 60.0) -> bool:
    started = time.time()
    while time.time() - started < timeout_seconds:
        try:
            status, payload = request_json(base_url, "GET", "/health")
            if status == 200 and payload.get("status") == "ok":
                return True
        except Exception:
            pass
        time.sleep(1.0)
    return False


def ensure_server(host: str, port: int) -> tuple[subprocess.Popen[str] | None, str]:
    base_url = f"http://{host}:{port}"
    if wait_for_health(base_url, timeout_seconds=2.0):
        return None, base_url
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.api.main:app", "--host", host, "--port", str(port)],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if not wait_for_health(base_url, timeout_seconds=90.0):
        proc.terminate()
        raise RuntimeError(f"Backend did not become healthy on {base_url}")
    return proc, base_url


def stop_server(proc: subprocess.Popen[str] | None) -> None:
    if proc is None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _extract_card_types(contract: dict[str, Any]) -> list[str]:
    return [str(card.get("type") or "") for card in list(contract.get("cards") or [])]


def _first_asset_path(contract: dict[str, Any]) -> str:
    for card in list(contract.get("cards") or []):
        data = card.get("data") if isinstance(card.get("data"), dict) else {}
        for key in ("map_preview_path", "image_path", "icon_path"):
            value = str(data.get(key) or "").strip()
            if value:
                return value
        items = data.get("items") if isinstance(data.get("items"), list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            value = str(item.get("image_path") or "").strip()
            if value:
                return value
    return ""


def run_api_regression(base_url: str) -> list[CheckResult]:
    results: list[CheckResult] = []
    session_weather = "qt-removal-weather"
    session_location = "qt-removal-location"
    session_news = "qt-removal-news"

    status, text_contract = request_json(
        base_url,
        "POST",
        "/chat/invoke",
        {"message": "这个项目是做什么的？", "session_id": "qt-removal-text", "attachments": None},
    )
    results.append(
        CheckResult(
            "文本问答",
            "OK" if status == 200 and bool(text_contract.get("text")) else "FAIL",
            f"cards={_extract_card_types(text_contract)} compat={((text_contract.get('meta') or {}).get('runtime') or {}).get('compat_fields_emitted')}",
        )
    )

    _, weather_contract = request_json(
        base_url,
        "POST",
        "/chat/invoke",
        {"message": "墨尔本天气怎么样", "session_id": session_weather, "attachments": None},
    )
    results.append(
        CheckResult(
            "天气",
            "OK" if "weather" in _extract_card_types(weather_contract) else "FAIL",
            f"cards={_extract_card_types(weather_contract)}",
        )
    )

    _, weather_follow = request_json(
        base_url,
        "POST",
        "/chat/invoke",
        {"message": "纽约呢", "session_id": session_weather, "attachments": None},
    )
    weather_follow_cards = _extract_card_types(weather_follow)
    weather_follow_city = str((((weather_follow.get("cards") or [{}])[0].get("data") or {}).get("city") or ""))
    results.append(
        CheckResult(
            "follow-up 天气",
            "OK" if "weather" in weather_follow_cards else "FAIL",
            f"cards={weather_follow_cards} city={weather_follow_city or '?'}",
        )
    )

    _, location_contract = request_json(
        base_url,
        "POST",
        "/chat/invoke",
        {"message": "成都在哪里", "session_id": session_location, "attachments": None},
    )
    location_cards = _extract_card_types(location_contract)
    results.append(
        CheckResult(
            "地点",
            "OK" if "location" in location_cards else "FAIL",
            f"cards={location_cards}",
        )
    )

    _, map_follow = request_json(
        base_url,
        "POST",
        "/chat/invoke",
        {"message": "显示地图", "session_id": session_location, "attachments": None},
    )
    map_cards = _extract_card_types(map_follow)
    results.append(
        CheckResult(
            "地图预览",
            "OK" if "location" in map_cards else "FAIL",
            f"cards={map_cards}",
        )
    )

    _, location_weather = request_json(
        base_url,
        "POST",
        "/chat/invoke",
        {"message": "那天气呢", "session_id": session_location, "attachments": None},
    )
    location_weather_cards = _extract_card_types(location_weather)
    results.append(
        CheckResult(
            "地点后 follow-up 天气",
            "OK" if "weather" in location_weather_cards else "FAIL",
            f"cards={location_weather_cards}",
        )
    )

    _, news_contract = request_json(
        base_url,
        "POST",
        "/chat/invoke",
        {"message": "今天科技新闻", "session_id": session_news, "attachments": None},
    )
    news_cards = _extract_card_types(news_contract)
    news_status = "OK" if any(card in {"news_list", "generic_info"} for card in news_cards) else "FAIL"
    results.append(
        CheckResult(
            "新闻",
            news_status,
            f"cards={news_cards} errors={len(news_contract.get('errors') or [])}",
        )
    )

    stream_events = collect_sse_events(
        base_url,
        {"message": "墨尔本天气怎么样", "session_id": "qt-removal-stream", "attachments": None},
    )
    stream_names = [str(event.get("event") or "") for event in stream_events]
    results.append(
        CheckResult(
            "/chat/stream",
            "OK" if {"message_start", "message_end"}.issubset(set(stream_names)) else "FAIL",
            f"events={stream_names}",
        )
    )
    results.append(
        CheckResult(
            "stream progress/text/card",
            "OK"
            if {"progress", "text_delta", "card"}.issubset(set(stream_names))
            else "PARTIAL",
            f"events={stream_names}",
        )
    )

    cancel_events = collect_sse_events(
        base_url,
        {"message": "今天科技新闻", "session_id": "qt-removal-cancel", "attachments": None},
        cancel_after_first_progress=True,
    )
    _, cancel_state = request_json(base_url, "GET", "/system/state")
    cancel_status = (
        "OK"
        if not cancel_state.get("is_streaming") and not cancel_state.get("active_stream_request")
        else "PARTIAL"
    )
    results.append(
        CheckResult(
            "cancel / abort",
            cancel_status,
            f"events={[event.get('event') for event in cancel_events]} active_stream_request={cancel_state.get('active_stream_request')}",
        )
    )

    asset_path = _first_asset_path(map_follow) or _first_asset_path(location_contract) or _first_asset_path(news_contract)
    if asset_path:
        asset_status, content_type, _ = request_text(
            base_url,
            f"/assets/local?path={urllib.parse.quote(asset_path, safe='')}",
        )
        results.append(
            CheckResult(
                "asset 图片显示",
                "OK" if asset_status == 200 and content_type.startswith("image/") else "FAIL",
                f"path={asset_path} content_type={content_type}",
            )
        )
    else:
        results.append(CheckResult("asset 图片显示", "PARTIAL", "No local asset path was returned by the tested cards."))

    _, capabilities = request_json(base_url, "GET", "/capabilities")
    results.append(
        CheckResult(
            "backend lifecycle",
            "OK" if capabilities.get("streaming") is True else "FAIL",
            f"streaming={capabilities.get('streaming')} system_actions={capabilities.get('system_actions')}",
        )
    )
    return results


def collect_runtime_fallback_summary() -> dict[str, dict[str, Any]]:
    from app.api.dependencies import get_runtime_service

    service = get_runtime_service()
    runtime = service.runtime
    capabilities = ["realtime_lookup", "web_research", "location_lookup", "generic_search", "explanation", "system_action"]
    summary: dict[str, dict[str, Any]] = {}
    for capability in capabilities:
        summary[capability] = {
            "invocation_service": bool(runtime._can_use_invocation_service(capability)),
            "direct_executor": capability in runtime._capability_executors,
            "direct_streaming_executor": capability in runtime._streaming_capability_executors,
            "still_requires_legacy_fallback": not runtime._can_use_invocation_service(capability)
            and capability not in runtime._capability_executors,
        }
    return summary


def render_markdown(checks: list[CheckResult]) -> str:
    lines = ["| 功能 | 状态 | 说明 |", "| --- | --- | --- |"]
    for item in checks:
        lines.append(f"| {item.name} | {item.status} | {item.detail.replace('|', '/')} |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Qt removal stabilization regression runner")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8014)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    qt_dependencies = collect_qt_dependencies()
    compat_usage = collect_compat_field_usage()
    fallback_summary = collect_runtime_fallback_summary()

    proc: subprocess.Popen[str] | None = None
    try:
        proc, base_url = ensure_server(args.host, args.port)
        checks = run_api_regression(base_url)
    finally:
        stop_server(proc)

    report = {
        "generated_at": int(time.time() * 1000),
        "base_url": base_url,
        "qt_dependencies": qt_dependencies,
        "compat_field_usage": compat_usage,
        "runtime_fallback_summary": fallback_summary,
        "checklist": [asdict(item) for item in checks],
        "markdown": render_markdown(checks),
        "asset_strategy": {
            "dev": "Frontend consumes backend /assets/local URLs only.",
            "packaged": "Frontend consumes backend /assets/local URLs only; no file:// direct access and no business remote image fetches.",
        },
    }
    if args.output:
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = ROOT / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
