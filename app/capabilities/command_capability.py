from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Iterable

from app.config import agent_config
from app.mcp_client_layer import MCPClientLayer
from app.tool_registry import ToolRegistry


class CommandCapability:
    def __init__(self, registry: ToolRegistry, mcp: MCPClientLayer) -> None:
        self.registry = registry
        self.mcp = mcp
        self.allowed_roots = tuple(path.resolve() for path in agent_config.allowed_roots)
        self._register_tools()

    def _register_tools(self) -> None:
        self.registry.register("run_command", "Run a terminal command in an allowed working directory.", self._tool_run_command)

    def run_command(
        self,
        command: str,
        *,
        allowed_tools: Iterable[str],
        cwd: str | None = None,
        timeout_sec: int | None = None,
    ) -> dict[str, Any]:
        return self.mcp.call_tool(
            "run_command",
            allowed_tools=allowed_tools,
            command=command,
            cwd=cwd or str(Path.cwd()),
            timeout_sec=timeout_sec or agent_config.command_timeout_sec,
        ).data

    def _tool_run_command(self, command: str, cwd: str, timeout_sec: int) -> dict[str, Any]:
        workdir = self._resolve_allowed_path(cwd)
        self.mcp.emit_event("validation_started", {"command": command, "cwd": str(workdir)})

        process = subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", command],
            cwd=str(workdir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            shell=False,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        try:
            stdout_text, stderr_text = process.communicate(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout_text, stderr_text = process.communicate()
            self.mcp.emit_event("validation_failed", {"command": command, "cwd": str(workdir), "reason": "timeout"})
            raise RuntimeError(f"Command timed out after {timeout_sec}s: {command}")

        for line in stdout_text.splitlines():
            stdout_lines.append(line)
            self.mcp.emit_event("command_stdout", {"command": command, "cwd": str(workdir), "line": line})
        for line in stderr_text.splitlines():
            stderr_lines.append(line)
            self.mcp.emit_event("command_stderr", {"command": command, "cwd": str(workdir), "line": line})

        if process.returncode == 0:
            self.mcp.emit_event("validation_passed", {"command": command, "cwd": str(workdir)})
        else:
            self.mcp.emit_event(
                "validation_failed",
                {"command": command, "cwd": str(workdir), "returncode": process.returncode},
            )

        return {
            "command": command,
            "cwd": str(workdir),
            "returncode": process.returncode,
            "stdout": "\n".join(stdout_lines),
            "stderr": "\n".join(stderr_lines),
            "ok": process.returncode == 0,
        }

    def _resolve_allowed_path(self, path: str) -> Path:
        raw = Path(path).expanduser()
        candidate = (raw if raw.is_absolute() else (Path.cwd() / raw)).resolve()
        for root in self.allowed_roots:
            try:
                candidate.relative_to(root)
                return candidate
            except ValueError:
                continue
        raise PermissionError(f"Path not allowed: {candidate}")

