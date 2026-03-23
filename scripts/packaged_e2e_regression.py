from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FAIRY_DESKTOP = ROOT / "fairy-desktop"
SRC_TAURI = FAIRY_DESKTOP / "src-tauri"
REPORT_DIR = ROOT / "data" / "reports"
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_BRIDGE_URL = "http://127.0.0.1:8527"


def resolve_executable(*candidates: str) -> str:
    for candidate in candidates:
        if not candidate:
            continue
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
        if Path(candidate).exists():
            return str(Path(candidate))
    raise FileNotFoundError(f"Could not locate executable from candidates: {candidates}")


def rust_build_env() -> dict[str, str]:
    env = os.environ.copy()
    cargo_home = ROOT / ".cargo"
    rustup_home = ROOT / ".rustup"
    llvm_root = ROOT / "tools" / "llvm-mingw" / "llvm-mingw-20260311-msvcrt-x86_64"
    xwin_root = ROOT / "tools" / "xwin-splat"
    cargo_bin = cargo_home / "bin"
    env["RUSTUP_HOME"] = str(rustup_home)
    env["CARGO_HOME"] = str(cargo_home)
    env["PATH"] = str(cargo_bin) + os.pathsep + env.get("PATH", "")
    env["CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER"] = "rust-lld"
    env["CC_x86_64_pc_windows_msvc"] = str(ROOT / "tools" / "llvm-msvc-shim" / "cl.cmd")
    env["CXX_x86_64_pc_windows_msvc"] = env["CC_x86_64_pc_windows_msvc"]
    env["AR_x86_64_pc_windows_msvc"] = str(llvm_root / "bin" / "llvm-lib.exe")
    env["RC_x86_64_pc_windows_msvc"] = str(llvm_root / "bin" / "llvm-rc.exe")
    env["LIB"] = ";".join(
        [
            str(xwin_root / "crt" / "lib" / "x86_64"),
            str(xwin_root / "sdk" / "lib" / "ucrt" / "x86_64"),
            str(xwin_root / "sdk" / "lib" / "um" / "x86_64"),
        ]
    )
    env["INCLUDE"] = ";".join(
        [
            str(xwin_root / "crt" / "include"),
            str(xwin_root / "sdk" / "include" / "ucrt"),
            str(xwin_root / "sdk" / "include" / "shared"),
            str(xwin_root / "sdk" / "include" / "um"),
            str(xwin_root / "sdk" / "include" / "winrt"),
            str(xwin_root / "sdk" / "include" / "cppwinrt"),
        ]
    )
    return env


def generate_minimal_frontend_dist(*, reason: str) -> Path:
    dist = FAIRY_DESKTOP / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    index_html = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Fairy Desktop</title>
    <style>
      body { margin: 0; font-family: Segoe UI, sans-serif; background: #1f2430; color: #eef2f7; }
      .wrap { padding: 32px; }
      .badge { display: inline-block; padding: 6px 10px; border-radius: 999px; background: #2d3443; margin-bottom: 12px; }
      code { color: #8fd3ff; }
    </style>
  </head>
  <body>
    <div class="wrap">
      <div class="badge">Packaged E2E Smoke Shell</div>
      <h1>Fairy Desktop</h1>
      <p>The packaged regression harness generated a minimal frontend because the normal Vite build was unavailable in this environment.</p>
      <p><strong>Reason:</strong> <code>__REASON__</code></p>
      <p>The Tauri shell, backend lifecycle, HTTP API, streaming, cancel, and asset routes are still validated by this packaged run.</p>
    </div>
  </body>
</html>
"""
    (dist / "index.html").write_text(index_html.replace("__REASON__", reason), encoding="utf-8")
    return dist


def request_json(method: str, url: str, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw or "{}")
        except json.JSONDecodeError:
            return exc.code, {"errors": [{"code": "http_error", "message": raw or str(exc)}]}


def request_headless_asset(url: str) -> tuple[int, str]:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=120) as response:
        _ = response.read(32)
        return response.status, response.headers.get_content_type()


def request_bridge_action(action: str, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    return request_json("POST", f"{DEFAULT_BRIDGE_URL}/bridge/system_action", {"action": action, "payload": payload or {}})


def wait_for_health(base_url: str, *, timeout_seconds: float = 60.0) -> bool:
    started = time.time()
    while time.time() - started < timeout_seconds:
        try:
            status, payload = request_json("GET", f"{base_url}/health")
            if status == 200 and payload.get("status") == "ok":
                return True
        except Exception:
            pass
        time.sleep(1.0)
    return False


def wait_for_backend_down(base_url: str, *, timeout_seconds: float = 30.0) -> bool:
    started = time.time()
    while time.time() - started < timeout_seconds:
        try:
            status, payload = request_json("GET", f"{base_url}/health")
            if status != 200 or payload.get("status") != "ok":
                return True
        except Exception:
            return True
        time.sleep(0.5)
    return False


def terminate_backend_port_listeners() -> None:
    backend_port = 8000
    try:
        netstat = subprocess.run(
            ["cmd", "/c", "netstat -ano -p tcp"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception:
        return
    targets: set[str] = set()
    for raw_line in (netstat.stdout or "").splitlines():
        line = raw_line.strip()
        if not line or f":{backend_port}" not in line or "LISTENING" not in line.upper():
            continue
        parts = line.split()
        if parts:
            pid = parts[-1].strip()
            if pid.isdigit():
                targets.add(pid)
    for pid in targets:
        try:
            subprocess.run(
                ["taskkill", "/PID", pid, "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
            )
        except Exception:
            continue


def collect_sse_events(base_url: str, payload: dict[str, Any], *, cancel_after_progress: bool = False) -> list[dict[str, Any]]:
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
            event = json.loads("\n".join(current_data))
            events.append(event)
            if cancel_after_progress and current_event == "progress" and not cancelled:
                request_json(
                    "POST",
                    f"{base_url}/system/actions",
                    {"action": "cancel_current_request", "payload": {}},
                )
                cancelled = True
            if current_event in {"message_end", "error"}:
                break
            current_event = ""
            current_data = []
    return events


def discover_release_executable() -> Path:
    candidates = [
        SRC_TAURI / "target" / "release" / "fairy-desktop.exe",
        SRC_TAURI / "target" / "release" / "Fairy Desktop.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    bundle_candidates = list((SRC_TAURI / "target" / "release" / "bundle").rglob("*.exe"))
    if bundle_candidates:
        return sorted(bundle_candidates)[0]
    raise FileNotFoundError("Could not locate a packaged Fairy Desktop executable.")


def build_release_binary() -> tuple[Path, dict[str, Any]]:
    npm = resolve_executable(
        "npm.cmd",
        "npm",
        r"C:\Program Files\nodejs\npm.cmd",
        r"C:\Program Files\nodejs\npm",
    )
    cargo = resolve_executable(
        str(ROOT / ".cargo" / "bin" / "cargo.exe"),
        "cargo.exe",
        "cargo",
    )
    tauri_cli = resolve_executable(
        r"C:\Program Files\nodejs\node.exe",
        "node.exe",
        "node",
    )
    frontend_build: dict[str, Any] = {"mode": "vite", "reason": ""}
    try:
        subprocess.run([npm, "run", "build"], cwd=FAIRY_DESKTOP, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        reason = str(exc)
        generate_minimal_frontend_dist(reason=reason)
        frontend_build = {"mode": "minimal_dist_fallback", "reason": reason}
    env = rust_build_env()
    env["PATH"] = str(Path(cargo).parent) + os.pathsep + env.get("PATH", "")
    subprocess.run(
        [
            tauri_cli,
            str(FAIRY_DESKTOP / "node_modules" / "@tauri-apps" / "cli" / "tauri.js"),
            "build",
            "--no-bundle",
        ],
        cwd=FAIRY_DESKTOP,
        check=True,
        env=env,
    )
    return discover_release_executable(), frontend_build


def lifecycle_events(log_path: Path) -> list[dict[str, Any]]:
    if not log_path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def wait_for_lifecycle(log_path: Path, expected: set[str], *, timeout_seconds: float = 30.0) -> list[dict[str, Any]]:
    started = time.time()
    while time.time() - started < timeout_seconds:
        events = lifecycle_events(log_path)
        names = {str(item.get("event") or "") for item in events}
        if expected.issubset(names):
            return events
        time.sleep(0.5)
    return lifecycle_events(log_path)


def start_packaged_app(
    executable: Path,
    *,
    log_path: Path,
    auto_exit_ms: int = 0,
    test_mode: str = "",
    headless: bool = False,
) -> subprocess.Popen[str]:
    env = os.environ.copy()
    env["FAIRY_PYTHON"] = str(ROOT / "cosyvoice_env" / "Scripts" / "python.exe")
    env["FAIRY_LIFECYCLE_LOG_PATH"] = str(log_path)
    if headless:
        env["FAIRY_HEADLESS_E2E"] = "1"
    else:
        env.pop("FAIRY_HEADLESS_E2E", None)
    if auto_exit_ms > 0:
        env["FAIRY_AUTO_EXIT_AFTER_MS"] = str(auto_exit_ms)
    else:
        env.pop("FAIRY_AUTO_EXIT_AFTER_MS", None)
    if test_mode:
        env["FAIRY_BACKEND_TEST_MODE"] = test_mode
    else:
        env.pop("FAIRY_BACKEND_TEST_MODE", None)
    return subprocess.Popen([str(executable)], cwd=str(executable.parent), env=env)


def start_manual_backend() -> subprocess.Popen[str]:
    return subprocess.Popen(
        [
            str(ROOT / "cosyvoice_env" / "Scripts" / "python.exe"),
            "-m",
            "uvicorn",
            "app.api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def stop_process(proc: subprocess.Popen[str] | None, *, force: bool = False) -> None:
    if proc is None:
        return
    try:
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
    except Exception:
        try:
            if force:
                proc.kill()
            else:
                proc.terminate()
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


def first_asset_path(contract: dict[str, Any], key_names: tuple[str, ...]) -> str:
    for card in list(contract.get("cards") or []):
        data = card.get("data") if isinstance(card.get("data"), dict) else {}
        for key in key_names:
            value = str(data.get(key) or "").strip()
            if value:
                return value
        items = data.get("items") if isinstance(data.get("items"), list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            for key in key_names:
                value = str(item.get(key) or "").strip()
                if value:
                    return value
    return ""


def weather_asset_ref(contract: dict[str, Any]) -> str:
    direct = first_asset_path(contract, ("icon_path",))
    if direct:
        return direct
    for card in list(contract.get("cards") or []):
        data = card.get("data") if isinstance(card.get("data"), dict) else {}
        icon_key = str(data.get("icon_key") or "").strip()
        if icon_key:
            return f"weather/{icon_key}.png"
    return ""


def run_chat_checks_modern(base_url: str) -> dict[str, Any]:
    weather_session = "packaged-weather"
    location_session = "packaged-location"

    _, text_contract = request_json(
        "POST",
        f"{base_url}/chat/invoke",
        {"message": "What does this project do?", "session_id": "packaged-text", "attachments": None},
    )
    _, weather = request_json(
        "POST",
        f"{base_url}/chat/invoke",
        {"message": "墨尔本天气怎么样", "session_id": weather_session, "attachments": None},
    )
    _, weather_follow = request_json(
        "POST",
        f"{base_url}/chat/invoke",
        {"message": "纽约呢", "session_id": weather_session, "attachments": None},
    )
    _, location = request_json(
        "POST",
        f"{base_url}/chat/invoke",
        {"message": "成都在哪里", "session_id": location_session, "attachments": None},
    )
    _, map_follow = request_json(
        "POST",
        f"{base_url}/chat/invoke",
        {"message": "显示地图", "session_id": location_session, "attachments": None},
    )
    _, location_weather = request_json(
        "POST",
        f"{base_url}/chat/invoke",
        {"message": "那天气呢", "session_id": location_session, "attachments": None},
    )
    _, news = request_json(
        "POST",
        f"{base_url}/chat/invoke",
        {"message": "今天科技新闻", "session_id": "packaged-news", "attachments": None},
    )
    stream_events = collect_sse_events(
        base_url,
        {"message": "墨尔本天气怎么样", "session_id": "packaged-stream", "attachments": None},
    )
    cancel_events = collect_sse_events(
        base_url,
        {"message": "今天科技新闻", "session_id": "packaged-cancel", "attachments": None},
        cancel_after_progress=True,
    )
    _, state_after_cancel = request_json("GET", f"{base_url}/system/state")

    map_asset = first_asset_path(map_follow, ("map_preview_path", "image_path")) or first_asset_path(
        location,
        ("map_preview_path", "image_path"),
    )
    weather_asset = weather_asset_ref(weather)
    news_asset = first_asset_path(news, ("image_path",)) or "news/generic-news.png"

    asset_status: dict[str, Any] = {}
    for label, path_value in {
        "map_preview": map_asset,
        "weather_icon": weather_asset,
        "news_thumbnail": news_asset,
    }.items():
        if not path_value:
            asset_status[label] = {"status": "partial", "detail": "No asset path returned."}
            continue
        encoded = urllib.parse.quote(path_value, safe="")
        status, content_type = request_headless_asset(f"{base_url}/assets/local?path={encoded}")
        asset_status[label] = {"status": "ok" if status == 200 else "fail", "path": path_value, "content_type": content_type}

    return {
        "chat_invoke_status": {
            "text": bool(text_contract.get("text")),
            "weather": [card.get("type") for card in weather.get("cards", [])],
            "weather_followup": [card.get("type") for card in weather_follow.get("cards", [])],
            "location": [card.get("type") for card in location.get("cards", [])],
            "map_followup": [card.get("type") for card in map_follow.get("cards", [])],
            "location_weather_followup": [card.get("type") for card in location_weather.get("cards", [])],
            "news": [card.get("type") for card in news.get("cards", [])],
            "news_errors": len(news.get("errors") or []),
        },
        "chat_stream_status": {
            "events": [event.get("event") for event in stream_events],
            "cancel_events": [event.get("event") for event in cancel_events],
        },
        "cancel_status": {
            "is_streaming": state_after_cancel.get("is_streaming"),
            "active_stream_request": state_after_cancel.get("active_stream_request"),
        },
        "asset_status": asset_status,
    }


def run_chat_checks(base_url: str) -> dict[str, Any]:
    weather_session = "packaged-weather"
    location_session = "packaged-location"

    _, text_contract = request_json("POST", f"{base_url}/chat/invoke", {"message": "这个项目是做什么的？", "session_id": "packaged-text", "attachments": None})
    _, weather = request_json("POST", f"{base_url}/chat/invoke", {"message": "墨尔本天气怎么样", "session_id": weather_session, "attachments": None})
    _, weather_follow = request_json("POST", f"{base_url}/chat/invoke", {"message": "纽约呢", "session_id": weather_session, "attachments": None})
    _, location = request_json("POST", f"{base_url}/chat/invoke", {"message": "成都在哪里", "session_id": location_session, "attachments": None})
    _, map_follow = request_json("POST", f"{base_url}/chat/invoke", {"message": "显示地图", "session_id": location_session, "attachments": None})
    _, location_weather = request_json("POST", f"{base_url}/chat/invoke", {"message": "那天气呢", "session_id": location_session, "attachments": None})
    _, news = request_json("POST", f"{base_url}/chat/invoke", {"message": "今天科技新闻", "session_id": "packaged-news", "attachments": None})

    stream_events = collect_sse_events(base_url, {"message": "墨尔本天气怎么样", "session_id": "packaged-stream", "attachments": None})
    cancel_events = collect_sse_events(
        base_url,
        {"message": "今天科技新闻", "session_id": "packaged-cancel", "attachments": None},
        cancel_after_progress=True,
    )
    _, state_after_cancel = request_json("GET", f"{base_url}/system/state")

    map_asset = first_asset_path(map_follow, ("map_preview_path", "image_path"))
    weather_asset = weather_asset_ref(weather)
    news_asset = first_asset_path(news, ("image_path",))

    asset_status: dict[str, Any] = {}
    for label, path_value in {
        "map_preview": map_asset,
        "weather_icon": weather_asset,
        "news_thumbnail": news_asset,
    }.items():
        if not path_value:
            asset_status[label] = {"status": "partial", "detail": "No asset path returned."}
            continue
        encoded = urllib.parse.quote(path_value, safe="")
        status, content_type = request_headless_asset(f"{base_url}/assets/local?path={encoded}")
        asset_status[label] = {"status": "ok" if status == 200 else "fail", "path": path_value, "content_type": content_type}

    return {
        "chat_invoke_status": {
            "text": bool(text_contract.get("text")),
            "weather": [card.get("type") for card in weather.get("cards", [])],
            "weather_followup": [card.get("type") for card in weather_follow.get("cards", [])],
            "location": [card.get("type") for card in location.get("cards", [])],
            "map_followup": [card.get("type") for card in map_follow.get("cards", [])],
            "location_weather_followup": [card.get("type") for card in location_weather.get("cards", [])],
            "news": [card.get("type") for card in news.get("cards", [])],
            "news_errors": len(news.get("errors") or []),
        },
        "chat_stream_status": {
            "events": [event.get("event") for event in stream_events],
            "cancel_events": [event.get("event") for event in cancel_events],
        },
        "cancel_status": {
            "is_streaming": state_after_cancel.get("is_streaming"),
            "active_stream_request": state_after_cancel.get("active_stream_request"),
        },
        "asset_status": asset_status,
    }


def run_chat_checks_v2(base_url: str) -> dict[str, Any]:
    results = run_chat_checks_modern(base_url)
    _, focus_action = request_json("POST", f"{base_url}/chat/invoke", {"message": "focus window", "session_id": "packaged-system", "attachments": None})
    _, open_panel_action = request_json("POST", f"{base_url}/chat/invoke", {"message": "open system panel", "session_id": "packaged-system", "attachments": None})
    _, reveal_action = request_json("POST", f"{base_url}/chat/invoke", {"message": "reveal asset folder", "session_id": "packaged-system", "attachments": None})
    desktop_stream_events = collect_sse_events(
        base_url,
        {"message": "open system panel", "session_id": "packaged-system-stream", "attachments": None},
    )
    bridge_focus_status, bridge_focus = request_bridge_action("focus_window")
    bridge_notify_status, bridge_notify = request_bridge_action("show_notification", {"message": "Packaged E2E notification"})
    bridge_reveal_status, bridge_reveal = request_bridge_action("reveal_asset_folder")
    bridge_restart_status, bridge_restart = request_bridge_action("restart_backend")
    restart_health = wait_for_health(base_url, timeout_seconds=30.0)

    results["chat_stream_status"]["desktop_action_events"] = [event.get("event") for event in desktop_stream_events]
    results["desktop_action_status"] = {
        "invoke_focus_window": {
            "executor_path": ((focus_action.get("meta") or {}).get("runtime") or {}).get("executor_path"),
            "used_legacy_fallback": ((focus_action.get("meta") or {}).get("runtime") or {}).get("used_legacy_fallback"),
            "system_action_type": ((focus_action.get("meta") or {}).get("runtime") or {}).get("system_action_type"),
        },
        "invoke_open_panel": {
            "executor_path": ((open_panel_action.get("meta") or {}).get("runtime") or {}).get("executor_path"),
            "used_legacy_fallback": ((open_panel_action.get("meta") or {}).get("runtime") or {}).get("used_legacy_fallback"),
            "system_action_type": ((open_panel_action.get("meta") or {}).get("runtime") or {}).get("system_action_type"),
        },
        "invoke_reveal_asset_folder": {
            "executor_path": ((reveal_action.get("meta") or {}).get("runtime") or {}).get("executor_path"),
            "used_legacy_fallback": ((reveal_action.get("meta") or {}).get("runtime") or {}).get("used_legacy_fallback"),
            "system_action_type": ((reveal_action.get("meta") or {}).get("runtime") or {}).get("system_action_type"),
        },
        "bridge_focus_window": {"http_status": bridge_focus_status, "result": bridge_focus},
        "bridge_show_notification": {"http_status": bridge_notify_status, "result": bridge_notify},
        "bridge_reveal_asset_folder": {"http_status": bridge_reveal_status, "result": bridge_reveal},
        "bridge_restart_backend": {
            "http_status": bridge_restart_status,
            "result": bridge_restart,
            "backend_healthy_after_restart": restart_health,
        },
    }
    return results


def run_duration_probe_modern(base_url: str, *, minutes: int) -> dict[str, Any]:
    end_time = time.time() + max(minutes, 1) * 60
    invoke_count = 0
    stream_count = 0
    cancel_count = 0
    errors: list[str] = []
    while time.time() < end_time:
        try:
            request_json(
                "POST",
                f"{base_url}/chat/invoke",
                {"message": "墨尔本天气怎么样", "session_id": f"duration-invoke-{invoke_count}", "attachments": None},
            )
            invoke_count += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"invoke:{exc}")
        try:
            collect_sse_events(
                base_url,
                {"message": "成都在哪里", "session_id": f"duration-stream-{stream_count}", "attachments": None},
            )
            stream_count += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"stream:{exc}")
        try:
            collect_sse_events(
                base_url,
                {"message": "今天科技新闻", "session_id": f"duration-cancel-{cancel_count}", "attachments": None},
                cancel_after_progress=True,
            )
            cancel_count += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"cancel:{exc}")
        if errors:
            break
        time.sleep(2.0)
    health_ok = wait_for_health(base_url, timeout_seconds=5.0)
    return {
        "minutes": minutes,
        "invoke_cycles": invoke_count,
        "stream_cycles": stream_count,
        "cancel_cycles": cancel_count,
        "backend_healthy_after_probe": health_ok,
        "errors": errors,
    }


def run_duration_probe(base_url: str, *, minutes: int) -> dict[str, Any]:
    end_time = time.time() + max(minutes, 1) * 60
    invoke_count = 0
    stream_count = 0
    cancel_count = 0
    errors: list[str] = []
    while time.time() < end_time:
        try:
            request_json(
                "POST",
                f"{base_url}/chat/invoke",
                {"message": "墨尔本天气怎么样", "session_id": f"duration-invoke-{invoke_count}", "attachments": None},
            )
            invoke_count += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"invoke:{exc}")
        try:
            collect_sse_events(
                base_url,
                {"message": "成都在哪里", "session_id": f"duration-stream-{stream_count}", "attachments": None},
            )
            stream_count += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"stream:{exc}")
        try:
            collect_sse_events(
                base_url,
                {"message": "今天科技新闻", "session_id": f"duration-cancel-{cancel_count}", "attachments": None},
                cancel_after_progress=True,
            )
            cancel_count += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"cancel:{exc}")
        if errors:
            break
        time.sleep(2.0)
    health_ok = wait_for_health(base_url, timeout_seconds=5.0)
    return {
        "minutes": minutes,
        "invoke_cycles": invoke_count,
        "stream_cycles": stream_count,
        "cancel_cycles": cancel_count,
        "backend_healthy_after_probe": health_ok,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Packaged Fairy Desktop end-to-end regression")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--duration-minutes", type=int, default=20)
    parser.add_argument("--output", default="data/reports/packaged_e2e_report.json")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    log_dir = REPORT_DIR / "packaged_lifecycle"
    log_dir.mkdir(parents=True, exist_ok=True)

    frontend_build: dict[str, Any] = {"mode": "existing_dist", "reason": ""}
    if args.build:
        executable, frontend_build = build_release_binary()
    else:
        executable = discover_release_executable()
    base_url = DEFAULT_BASE_URL
    report: dict[str, Any] = {
        "generated_at": int(time.time() * 1000),
        "executable": str(executable),
        "frontend_build": frontend_build,
        "chat_invoke_status": {},
        "chat_stream_status": {},
        "desktop_action_status": {},
        "asset_status": {},
        "cancel_status": {},
        "backend_lifecycle": {},
        "errors": [],
        "duration_test": {},
    }

    app_proc: subprocess.Popen[str] | None = None
    reused_backend: subprocess.Popen[str] | None = None
    try:
        terminate_backend_port_listeners()
        wait_for_backend_down(base_url, timeout_seconds=10)
        # Fresh packaged launch
        fresh_log = log_dir / "fresh.jsonl"
        fresh_log.unlink(missing_ok=True)
        app_proc = start_packaged_app(executable, log_path=fresh_log, auto_exit_ms=120000, headless=args.headless)
        fresh_events = wait_for_lifecycle(fresh_log, {"backend://starting", "backend://ready"}, timeout_seconds=45)
        if not wait_for_health(base_url, timeout_seconds=45):
            raise RuntimeError("Packaged app did not expose a healthy backend.")
        report["backend_lifecycle"]["fresh"] = [event.get("event") for event in fresh_events if str(event.get("event") or "").startswith("backend://")]
        chat_results = run_chat_checks_v2(base_url)
        report["chat_invoke_status"] = chat_results["chat_invoke_status"]
        report["chat_stream_status"] = chat_results["chat_stream_status"]
        report["desktop_action_status"] = chat_results.get("desktop_action_status", {})
        report["asset_status"] = chat_results["asset_status"]
        report["cancel_status"] = chat_results["cancel_status"]

        # Reused backend launch
        if app_proc is not None:
            stop_process(app_proc)
            app_proc = None
            wait_for_backend_down(base_url, timeout_seconds=20)
            time.sleep(2.0)
        reused_backend = start_manual_backend()
        if not wait_for_health(base_url, timeout_seconds=45):
            raise RuntimeError("Manual backend did not become healthy for reused scenario.")
        reused_log = log_dir / "reused.jsonl"
        reused_log.unlink(missing_ok=True)
        app_proc = start_packaged_app(executable, log_path=reused_log, auto_exit_ms=10000, headless=args.headless)
        reused_events = wait_for_lifecycle(reused_log, {"backend://reused", "backend://ready"}, timeout_seconds=20)
        report["backend_lifecycle"]["reused"] = [event.get("event") for event in reused_events if str(event.get("event") or "").startswith("backend://")]
        stop_process(app_proc)
        app_proc = None
        stop_process(reused_backend)
        reused_backend = None
        wait_for_backend_down(base_url, timeout_seconds=20)
        terminate_backend_port_listeners()
        time.sleep(2.0)

        # Timeout scenario
        timeout_log = log_dir / "timeout.jsonl"
        timeout_log.unlink(missing_ok=True)
        app_proc = start_packaged_app(executable, log_path=timeout_log, auto_exit_ms=20000, test_mode="timeout", headless=args.headless)
        timeout_events = wait_for_lifecycle(timeout_log, {"backend://starting", "backend://timeout", "backend://spawn-failed"}, timeout_seconds=25)
        report["backend_lifecycle"]["timeout"] = [event.get("event") for event in timeout_events if str(event.get("event") or "").startswith("backend://")]
        stop_process(app_proc)
        app_proc = None
        wait_for_backend_down(base_url, timeout_seconds=20)
        terminate_backend_port_listeners()

        # Stopped event via auto-exit
        stopped_log = log_dir / "stopped.jsonl"
        stopped_log.unlink(missing_ok=True)
        app_proc = start_packaged_app(executable, log_path=stopped_log, auto_exit_ms=5000, headless=args.headless)
        stopped_events = wait_for_lifecycle(stopped_log, {"backend://starting", "backend://ready", "backend://stopped"}, timeout_seconds=20)
        report["backend_lifecycle"]["stopped"] = [event.get("event") for event in stopped_events if str(event.get("event") or "").startswith("backend://")]
        stop_process(app_proc)
        app_proc = None

        # Duration probe on packaged-launched backend
        duration_log = log_dir / "duration.jsonl"
        duration_log.unlink(missing_ok=True)
        app_proc = start_packaged_app(
            executable,
            log_path=duration_log,
            auto_exit_ms=(args.duration_minutes * 60 * 1000) + 30000,
            headless=args.headless,
        )
        if not wait_for_health(base_url, timeout_seconds=45):
            raise RuntimeError("Packaged app did not expose a healthy backend for the duration probe.")
        report["duration_test"] = run_duration_probe_modern(base_url, minutes=args.duration_minutes)
    except Exception as exc:  # noqa: BLE001
        report["errors"].append(str(exc))
    finally:
        stop_process(app_proc)
        stop_process(reused_backend)
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = ROOT / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not report["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
