from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import struct
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest

RUNNER_PATH = Path(__file__).parents[1] / "runner" / "fairy_runtime_supervisor.py"


def _load_runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "fairy_runtime_supervisor", RUNNER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True, slots=True)
class FakeIdentity:
    pid: int
    start_ticks: int
    port: int


class FakeProcesses:
    def __init__(self) -> None:
        self.starts: list[tuple[str, ...]] = []
        self.running: set[tuple[int, int]] = set()
        self.stops: list[tuple[int, int]] = []

    def allocate_port(self) -> int:
        return 43125

    def start(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        startup_timeout_seconds: int,
        readiness_path: str,
        log_path: Path,
        port: int,
    ) -> FakeIdentity:
        del cwd, startup_timeout_seconds, readiness_path, log_path
        self.starts.append(argv)
        identity = FakeIdentity(700 + len(self.starts), 900 + len(self.starts), port)
        self.running.add((identity.pid, identity.start_ticks))
        return identity

    def is_running(
        self, pid: int, start_ticks: int, port: int, readiness_path: str
    ) -> bool:
        del port, readiness_path
        return (pid, start_ticks) in self.running

    def stop(self, pid: int, start_ticks: int) -> None:
        self.stops.append((pid, start_ticks))
        self.running.discard((pid, start_ticks))


def test_runtime_health_attests_wsl_isolation_configuration(tmp_path: Path) -> None:
    runner = _load_runner()
    bwrap = tmp_path / "bwrap"
    bwrap.write_bytes(b"fixture")
    config = tmp_path / "wsl.conf"
    config.write_text(
        "[automount]\n"
        "enabled=false\n"
        "mountFsTab=false\n"
        "[interop]\n"
        "enabled=false\n"
        "appendWindowsPath=false\n",
        encoding="utf-8",
    )
    runner.BWRAP = bwrap
    runner.WSL_CONFIG = config
    runner._tool_version = lambda argv: {
        "node": "v24.18.0",
        "npm": "11.16.0",
        "pnpm": "10.34.4",
        "yarn": "1.22.22",
        "uv": "uv 0.11.28",
    }[Path(argv[0]).name]

    health = runner.health_document()

    assert health["config"] == {
        "automount.enabled": False,
        "automount.mountFsTab": False,
        "interop.enabled": False,
        "interop.appendWindowsPath": False,
    }


def test_runtime_request_is_strict_and_archive_bound() -> None:
    runner = _load_runner()
    archive = _archive()

    request, decoded = runner.parse_start_frame(_frame(archive))

    assert request.adapter == "vite"
    assert request.argv[-3:] == ("--port", "{port}", "--strictPort")
    assert request.scope_digest == "a" * 64
    assert decoded == archive


@pytest.mark.parametrize(
    "overrides",
    (
        {"argv": ["sh", "-c", "attacker"]},
        {"argv": ["vite", "--host", "0.0.0.0", "--port", "{port}"]},
        {"readiness_path": "http://attacker.invalid/"},
        {"dependency_key": "bad"},
        {"environment": {"TOKEN": "secret"}},
        {"host": "0.0.0.0"},
        {"port": 80},
    ),
)
def test_runtime_request_rejects_untrusted_or_extra_controls(
    overrides: dict[str, object],
) -> None:
    runner = _load_runner()

    with pytest.raises(runner.RuntimeProtocolError):
        runner.parse_start_frame(_frame(_archive(), **overrides))


def test_runtime_lifecycle_is_idempotent_and_fenced(tmp_path: Path) -> None:
    runner = _load_runner()
    archive = _archive()
    request, decoded = runner.parse_start_frame(_frame(archive))
    _dependency_layer(tmp_path, request.dependency_key)
    processes = FakeProcesses()

    first = runner.start_runtime(request, decoded, tmp_path, processes)
    replay = runner.start_runtime(request, decoded, tmp_path, processes)
    assert replay == first
    assert len(processes.starts) == 1
    assert first["url"] == "http://127.0.0.1:43125/"
    command = processes.starts[0]
    assert "--die-with-parent" not in command
    assert "--ro-bind" in command
    assert "--seccomp" in command
    assert runner.SECCOMP_FD_TOKEN in command
    assert "--unshare-net" not in command
    assert "127.0.0.1" in command
    assert "{port}" not in command

    stale, stale_archive = runner.parse_start_frame(_frame(archive, lease_fence=6))
    with pytest.raises(runner.RuntimeProtocolError, match="fence"):
        runner.start_runtime(stale, stale_archive, tmp_path, processes)

    replacement, replacement_archive = runner.parse_start_frame(
        _frame(archive, lease_fence=8)
    )
    replaced = runner.start_runtime(
        replacement,
        replacement_archive,
        tmp_path,
        processes,
    )
    assert replaced["lease_fence"] == 8
    assert processes.stops == [(701, 901)]
    assert len(processes.starts) == 2


def test_runtime_seccomp_filter_blocks_outbound_network_syscalls() -> None:
    runner = _load_runner()
    content = runner._network_filter("x86_64")
    instructions = tuple(
        struct.unpack("=HBBI", content[index : index + 8])
        for index in range(0, len(content), 8)
    )

    compared_syscalls = {
        argument for code, _true, _false, argument in instructions if code == 0x15
    }
    assert compared_syscalls == {42, 44, 46, 307, 425}
    assert instructions[-1] == (0x06, 0, 0, 0x7FFF0000)
    assert all(
        instructions[index + 1] == (0x06, 0, 0, 0x00050001)
        for index, instruction in enumerate(instructions[:-1])
        if instruction[0] == 0x15
    )


def test_probe_and_stop_reject_pid_reuse_and_preserve_fence(tmp_path: Path) -> None:
    runner = _load_runner()
    archive = _archive()
    request, decoded = runner.parse_start_frame(_frame(archive))
    _dependency_layer(tmp_path, request.dependency_key)
    processes = FakeProcesses()
    started = runner.start_runtime(request, decoded, tmp_path, processes)
    handle = str(started["executor_handle"])

    probe = runner.probe_runtime(handle, tmp_path, processes)
    assert probe["state"] == "running"
    processes.running.clear()
    interrupted = runner.probe_runtime(handle, tmp_path, processes)
    assert interrupted["state"] == "interrupted"

    stopped = runner.stop_runtime(handle, tmp_path, processes)
    assert stopped["stopped"] is True
    assert stopped["lease_fence"] == request.lease_fence


def test_runtime_requires_completed_matching_dependency_layer(tmp_path: Path) -> None:
    runner = _load_runner()
    archive = _archive()
    request, decoded = runner.parse_start_frame(_frame(archive))

    with pytest.raises(runner.RuntimeProtocolError, match="dependency layer"):
        runner.start_runtime(request, decoded, tmp_path, FakeProcesses())


def test_cloud_mode_uses_cloud_identity_target_and_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAIRY_RUNTIME_MODE", "cloud")
    runner = _load_runner()
    archive = _archive()
    request, decoded = runner.parse_start_frame(
        _frame(archive, execution_target="cloud", kind="cloud_oci")
    )
    _dependency_layer(tmp_path, request.dependency_key)

    started = runner.start_runtime(request, decoded, tmp_path, FakeProcesses())

    assert started["executor"] == "cloud_oci_runtime"
    assert str(started["executor_handle"]).startswith("cloud-dynamic:")


def _frame(archive: bytes, **overrides: object) -> bytes:
    header: dict[str, object] = {
        "schema_version": 1,
        "project_id": "01980f66-b740-7dc8-9e1b-2714cf0c8801",
        "conversation_id": "01980f66-b740-7dc8-9e1b-2714cf0c8802",
        "task_id": "01980f66-b740-7dc8-9e1b-2714cf0c8803",
        "version_id": "01980f66-b740-7dc8-9e1b-2714cf0c8804",
        "runtime_id": "01980f66-b740-7dc8-9e1b-2714cf0c8806",
        "preview_id": "01980f66-b740-7dc8-9e1b-2714cf0c8805",
        "execution_target": "local",
        "kind": "wsl_project",
        "adapter": "vite",
        "scope_digest": "a" * 64,
        "workspace_generation": 3,
        "lease_fence": 7,
        "argv": [
            "node_modules/.bin/vite",
            "--host",
            "127.0.0.1",
            "--port",
            "{port}",
            "--strictPort",
        ],
        "cwd": ".",
        "readiness_path": "/health",
        "startup_timeout_seconds": 45,
        "dependency_key": "b" * 64,
        "archive_byte_length": len(archive),
        "archive_sha256": hashlib.sha256(archive).hexdigest(),
    }
    header.update(overrides)
    encoded = json.dumps(header, separators=(",", ":"), sort_keys=True).encode()
    return struct.pack(">I", len(encoded)) + encoded + archive


def _archive() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("index.html", "<main></main>")
        archive.writestr("package.json", "{}")
    return output.getvalue()


def _dependency_layer(root: Path, key: str) -> None:
    layer = root / "dependencies" / key
    (layer / "node_modules").mkdir(parents=True)
    (layer / "complete.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dependency_key": key,
                "dependency_manager": "npm",
            }
        ),
        encoding="utf-8",
    )
