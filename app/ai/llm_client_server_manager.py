from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse

import requests

from app.config import BASE_DIR


class ServerManager:
    def __init__(self, config: Any, base_dir: Path = BASE_DIR) -> None:
        self.config = config
        self.base_dir = base_dir
        self.server_started = False
        self.startup_error: str | None = None
        self.backend_summary = "uninitialized"
        self._server_lock = threading.Lock()
        self._server_process: subprocess.Popen | None = None
        self._owns_server_process = False

    def start_background(self) -> None:
        threading.Thread(target=self.try_start_server, daemon=True).start()

    def try_start_server(self) -> None:
        try:
            self.ensure_server_running()
        except Exception as exc:  # noqa: BLE001
            self.startup_error = str(exc)

    def parse_port(self) -> int:
        parsed = urlparse(self.config.api_base)
        return parsed.port or 80

    def build_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def ensure_utf8_response(self, resp: requests.Response) -> requests.Response:
        resp.encoding = "utf-8"
        return resp

    def probe_server(self, timeout: float = 3.0) -> None:
        url = self.config.api_base.rstrip("/") + "/health"
        resp = requests.get(url, headers=self.build_headers(), timeout=timeout)
        resp.raise_for_status()

    def read_log_tail(self, log_path: Path, limit: int = 4000) -> str:
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
            return text[-limit:]
        except Exception:  # noqa: BLE001
            return ""

    def update_backend_summary_from_log(self, log_tail: str) -> None:
        parts: list[str] = []
        if "loaded CUDA backend" in log_tail:
            parts.append("CUDA")
        if "loaded CPU backend" in log_tail:
            parts.append("CPU")
        if "CLIP using CUDA0 backend" in log_tail:
            parts.append("Vision=GPU")
        elif "CLIP using CPU backend" in log_tail:
            parts.append("Vision=CPU")
        if (
            "offloaded 33/33 layers to GPU" in log_tail
            or "CUDA0 model buffer size" in log_tail
            or "using device CUDA0" in log_tail
        ):
            parts.append("LLM=GPU")
        elif "offloaded 0/" in log_tail:
            parts.append("LLM=CPU")
        if parts:
            self.backend_summary = ", ".join(parts)

    def format_http_error(self, resp: requests.Response) -> str:
        try:
            data = resp.json()
            error = data.get("error", {})
            message = error.get("message") or data.get("message")
            if message:
                if "image input is not supported" in str(message):
                    mmproj_hint = (
                        f" Current mmproj_path: {self.config.mmproj_path}"
                        if self.config.mmproj_path
                        else " Current mmproj_path: not configured."
                    )
                    return (
                        "Image input is not enabled on the local server. "
                        "This model needs an mmproj vision projector file for image analysis."
                        + mmproj_hint
                    )
                return str(message)
        except Exception:
            pass
        return f"HTTP {resp.status_code}: {resp.text[:300]}"

    def diagnose_startup_error(self, log_tail: str) -> str:
        if "unknown model architecture: 'qwen35'" in log_tail:
            return (
                "Detected unsupported architecture `qwen35`. "
                "Your bundled llama.cpp build cannot load Qwen3.5 GGUF yet. "
                "Use a newer runtime that supports qwen35, or switch to a model architecture "
                "supported by the current `app/ai/llama-server.exe`."
            )
        if "failed to open GGUF file" in log_tail:
            return "GGUF file could not be opened. Check model_path, file permissions, and disk availability."
        return ""

    def ensure_shards(self, model_path: Path) -> None:
        shard_match = re.match(r"(.+)-00001-of-(\d+)\.gguf$", model_path.name)
        if not shard_match:
            return

        base_prefix, total = shard_match.groups()
        total_parts = int(total)
        for idx in range(2, total_parts + 1):
            companion = model_path.with_name(f"{base_prefix}-{idx:05d}-of-{total}.gguf")
            if not companion.exists():
                raise FileNotFoundError(
                    f"Model shard missing: {companion} (expected total {total_parts} shards)"
                )

    def is_owned_server_alive(self) -> bool:
        return self._server_process is not None and self._server_process.poll() is None

    def ensure_server_running(self) -> None:
        with self._server_lock:
            try:
                self.probe_server(timeout=2.0)
                log_tail = self.read_log_tail(self.base_dir / "data" / "llama_server.log")
                self.update_backend_summary_from_log(log_tail)
                self.server_started = True
                self.startup_error = None
                self._owns_server_process = False
                return
            except Exception:
                pass

            if self.config.runtime_mode == "external_api":
                raise RuntimeError(
                    "No external OpenAI-compatible server is reachable at "
                    f"{self.config.api_base}. Start your Qwen3.5 server first, then retry."
                )

            startup_deadline = time.time() + max(10, self.config.startup_timeout_sec)

            if self.is_owned_server_alive():
                while time.time() < startup_deadline:
                    try:
                        self.probe_server(timeout=1.0)
                        self.server_started = True
                        self.startup_error = None
                        return
                    except Exception:
                        time.sleep(0.5)

            exe = self.config.server_executable
            model_path = self.config.model_path
            port = self.parse_port()

            if not exe.exists():
                raise FileNotFoundError(f"llama.cpp executable not found: {exe}")
            if not model_path.exists():
                raise FileNotFoundError(f"Model file not found: {model_path}")
            self.ensure_shards(model_path)

            args = [
                str(exe),
                "--model",
                str(model_path),
                "--port",
                str(port),
                "--ctx-size",
                str(self.config.ctx_size),
                "--gpu-layers",
                str(self.config.gpu_layers),
                "--host",
                "127.0.0.1",
            ]
            if self.config.mmproj_path:
                args.extend(["--mmproj", str(self.config.mmproj_path)])
            if self.config.threads and self.config.threads > 0:
                args.extend(["--threads", str(self.config.threads)])
            if self.config.reasoning_budget >= 0:
                args.extend(["--reasoning-budget", str(self.config.reasoning_budget)])
            if getattr(self.config, "reasoning_format", ""):
                args.extend(["--reasoning-format", str(self.config.reasoning_format)])

            log_dir = self.base_dir / "data"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / "llama_server.log"
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

            with log_path.open("a", encoding="utf-8") as log_file:
                try:
                    proc = subprocess.Popen(  # noqa: S603
                        args,
                        stdout=log_file,
                        stderr=log_file,
                        creationflags=creationflags,
                    )
                except Exception as exc:  # noqa: BLE001
                    raise RuntimeError(f"Failed to start llama.cpp server: {exc}") from exc

            self._server_process = proc
            self._owns_server_process = True

            while time.time() < startup_deadline:
                if proc.poll() is not None:
                    log_tail = self.read_log_tail(log_path)
                    self.update_backend_summary_from_log(log_tail)
                    diagnosis = self.diagnose_startup_error(log_tail)
                    diag_text = f"\nDiagnosis: {diagnosis}" if diagnosis else ""
                    raise RuntimeError(
                        f"llama.cpp server exited early (code {proc.returncode}).\n"
                        f"Log: {log_path}{diag_text}\n{log_tail}"
                    )
                try:
                    self.probe_server(timeout=1.0)
                    log_tail = self.read_log_tail(log_path)
                    self.update_backend_summary_from_log(log_tail)
                    self.server_started = True
                    self.startup_error = None
                    return
                except Exception:
                    time.sleep(1.0)

            raise RuntimeError(
                f"llama.cpp server startup timed out after {self.config.startup_timeout_sec}s. "
                f"Check log: {log_path}"
            )

    def shutdown(self) -> None:
        with self._server_lock:
            if not self._owns_server_process or self._server_process is None:
                return

            proc = self._server_process
            if proc.poll() is None:
                try:
                    if os.name == "nt":
                        subprocess.run(  # noqa: S603
                            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                            capture_output=True,
                            text=True,
                            timeout=8,
                            check=False,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                        )
                        proc.wait(timeout=3)
                    else:
                        proc.terminate()
                        proc.wait(timeout=5)
                except Exception:  # noqa: BLE001
                    try:
                        proc.kill()
                        proc.wait(timeout=2)
                    except Exception:  # noqa: BLE001
                        pass

            self._server_process = None
            self._owns_server_process = False
            self.server_started = False

    def get_runtime_status(self) -> str:
        if self.server_started:
            return f"{self.config.runtime_mode} | {self.config.model} | {self.backend_summary}"
        if self.startup_error:
            return f"{self.config.runtime_mode} | startup error"
        return f"{self.config.runtime_mode} | idle"

    def get_runtime_snapshot(self) -> Dict[str, Any]:
        model_online: bool | None
        if self.server_started:
            model_online = True
        elif self.startup_error:
            model_online = False
        else:
            model_online = None
        return {
            "runtime_mode": self.config.runtime_mode,
            "model": self.config.model,
            "model_online": model_online,
            "network_online": None,
            "backend_summary": self.backend_summary,
            "startup_error": self.startup_error,
        }
