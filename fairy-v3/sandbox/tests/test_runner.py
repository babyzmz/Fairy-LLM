from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import struct
import sys
import threading
import time
import zipfile
from pathlib import Path
from types import ModuleType

import pytest


RUNNER_PATH = Path(__file__).parents[1] / "runner" / "fairy_sandbox_runner.py"


def _load_runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fairy_sandbox_runner", RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _archive(
    files: dict[str, bytes] | None = None,
    *,
    symlink: str | None = None,
) -> bytes:
    destination = io.BytesIO()
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in (files or {"src/main.py": b"print('ok')\n"}).items():
            archive.writestr(name, content)
        if symlink is not None:
            info = zipfile.ZipInfo(symlink)
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            archive.writestr(info, "../../outside")
    return destination.getvalue()


def _frame(
    archive: bytes,
    **overrides: object,
) -> bytes:
    header: dict[str, object] = {
        "schema_version": 1,
        "job_id": "01980f66-b740-7dc8-9e1b-2714cf0c8801",
        "project_id": "01980f66-b740-7dc8-9e1b-2714cf0c8802",
        "conversation_id": "01980f66-b740-7dc8-9e1b-2714cf0c8803",
        "task_id": "01980f66-b740-7dc8-9e1b-2714cf0c8804",
        "version_id": "01980f66-b740-7dc8-9e1b-2714cf0c8805",
        "scope_digest": "a" * 64,
        "workspace_generation": 3,
        "lease_fence": 7,
        "argv": ["python3", "-c", "print('ok')"],
        "cwd": ".",
        "environment": {"FAIRY_MODE": "test"},
        "timeout_seconds": 10,
        "output_limit_bytes": 4096,
        "network_policy": "none",
        "archive_byte_length": len(archive),
        "archive_sha256": hashlib.sha256(archive).hexdigest(),
    }
    header.update(overrides)
    encoded = json.dumps(header, separators=(",", ":"), sort_keys=True).encode()
    return struct.pack(">I", len(encoded)) + encoded + archive


def test_runner_parses_a_bounded_structured_request() -> None:
    runner = _load_runner()
    archive = _archive()

    request, decoded_archive = runner.parse_request_frame(_frame(archive))

    assert request.argv == ("python3", "-c", "print('ok')")
    assert request.cwd == "."
    assert request.environment == {"FAIRY_MODE": "test"}
    assert request.workspace_generation == 3
    assert decoded_archive == archive


@pytest.mark.parametrize(
    ("override", "message"),
    (
        ({"argv": ["python3", "bad\0arg"]}, "argv"),
        ({"cwd": "../outside"}, "cwd"),
        ({"environment": {"LD_PRELOAD": "/tmp/hook.so"}}, "environment"),
        ({"timeout_seconds": 901}, "timeout"),
        ({"output_limit_bytes": 1}, "output"),
        ({"scope_digest": "not-a-digest"}, "scope"),
        ({"network_policy": "host"}, "network"),
    ),
)
def test_runner_revalidates_untrusted_request_headers(
    override: dict[str, object],
    message: str,
) -> None:
    runner = _load_runner()
    archive = _archive()

    with pytest.raises(runner.RunnerProtocolError, match=message):
        runner.parse_request_frame(_frame(archive, **override))


def test_runner_rejects_archive_path_traversal(tmp_path: Path) -> None:
    runner = _load_runner()
    archive = _archive({"../escape.txt": b"escaped"})
    request, decoded = runner.parse_request_frame(_frame(archive))

    with pytest.raises(runner.RunnerProtocolError, match="archive path"):
        runner.synchronize_workspace(request, decoded, tmp_path)

    assert not (tmp_path.parent / "escape.txt").exists()


def test_runner_rejects_archive_symlinks(tmp_path: Path) -> None:
    runner = _load_runner()
    archive = _archive(symlink="src/link")
    request, decoded = runner.parse_request_frame(_frame(archive))

    with pytest.raises(runner.RunnerProtocolError, match="symlink"):
        runner.synchronize_workspace(request, decoded, tmp_path)


def test_runner_reuses_exact_workspace_generation_and_rejects_rollback(
    tmp_path: Path,
) -> None:
    runner = _load_runner()
    archive = _archive({"app.txt": b"generation-three"})
    request, decoded = runner.parse_request_frame(_frame(archive))
    first = runner.synchronize_workspace(request, decoded, tmp_path)
    second = runner.synchronize_workspace(request, decoded, tmp_path)

    assert first == second
    assert (second / "app.txt").read_bytes() == b"generation-three"

    older_request, older_archive = runner.parse_request_frame(
        _frame(archive, workspace_generation=2)
    )
    with pytest.raises(runner.RunnerProtocolError, match="generation rollback"):
        runner.synchronize_workspace(older_request, older_archive, tmp_path)


def test_runner_rejects_same_generation_with_different_archive(tmp_path: Path) -> None:
    runner = _load_runner()
    first_archive = _archive({"app.txt": b"first"})
    first_request, decoded_first = runner.parse_request_frame(_frame(first_archive))
    runner.synchronize_workspace(first_request, decoded_first, tmp_path)
    second_archive = _archive({"app.txt": b"second"})
    second_request, decoded_second = runner.parse_request_frame(_frame(second_archive))

    with pytest.raises(runner.RunnerProtocolError, match="generation hash"):
        runner.synchronize_workspace(second_request, decoded_second, tmp_path)


def test_runner_recovers_a_complete_generation_when_current_marker_is_missing(
    tmp_path: Path,
) -> None:
    runner = _load_runner()
    archive = _archive({"app.txt": b"complete"})
    request, decoded = runner.parse_request_frame(_frame(archive))
    workspace = runner.synchronize_workspace(request, decoded, tmp_path)
    current = workspace.parent / "current.json"
    current.unlink()

    recovered = runner.synchronize_workspace(request, decoded, tmp_path)

    assert recovered == workspace
    assert current.is_file()


def test_bounded_process_stops_an_output_flood(tmp_path: Path) -> None:
    runner = _load_runner()
    result = runner.run_bounded_process(
        (
            sys.executable,
            "-c",
            "import os; [os.write(1, b'x' * 4096) for _ in range(1024)]",
        ),
        cwd=tmp_path,
        environment={},
        timeout_seconds=10,
        output_limit_bytes=4096,
        cancellation_path=tmp_path / "cancel",
    )

    assert result.output_truncated is True
    assert len(result.stdout) + len(result.stderr) <= 4096
    assert result.status == "failed"


def test_bounded_process_terminates_on_timeout(tmp_path: Path) -> None:
    runner = _load_runner()
    started = time.monotonic()

    result = runner.run_bounded_process(
        (sys.executable, "-c", "import time; time.sleep(30)"),
        cwd=tmp_path,
        environment={},
        timeout_seconds=1,
        output_limit_bytes=4096,
        cancellation_path=tmp_path / "cancel",
    )

    assert result.status == "timed_out"
    assert result.exit_code is None
    assert time.monotonic() - started < 5


def test_bounded_process_observes_durable_cancellation_marker(tmp_path: Path) -> None:
    runner = _load_runner()
    cancellation_path = tmp_path / "cancel"

    def cancel() -> None:
        time.sleep(0.2)
        cancellation_path.write_text("cancel", encoding="ascii")

    thread = threading.Thread(target=cancel)
    thread.start()
    try:
        result = runner.run_bounded_process(
            (sys.executable, "-c", "import time; time.sleep(30)"),
            cwd=tmp_path,
            environment={},
            timeout_seconds=10,
            output_limit_bytes=4096,
            cancellation_path=cancellation_path,
        )
    finally:
        thread.join()

    assert result.status == "cancelled"
    assert result.exit_code is None


def test_cancellation_that_arrives_before_spawn_is_not_lost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    archive = _archive()
    frame = _frame(archive)
    request, _decoded = runner.parse_request_frame(frame)
    runner.cancel_job(request.job_id, tmp_path)
    monkeypatch.setattr(
        runner,
        "build_isolation_command",
        lambda _request, _workspace: (
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
        ),
    )

    result = runner.execute_frame(frame, tmp_path)

    assert result["status"] == "cancelled"


def test_each_job_executes_in_a_fresh_copy_of_the_immutable_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    archive = _archive({"source.txt": b"immutable"})
    monkeypatch.setattr(
        runner,
        "build_isolation_command",
        lambda request, _workspace: request.argv,
    )
    first = _frame(
        archive,
        job_id="01980f66-b740-7dc8-9e1b-2714cf0c8811",
        argv=[
            sys.executable,
            "-c",
            "from pathlib import Path; Path('created.txt').write_text('job one')",
        ],
    )
    second = _frame(
        archive,
        job_id="01980f66-b740-7dc8-9e1b-2714cf0c8812",
        argv=[
            sys.executable,
            "-c",
            "from pathlib import Path; print(Path('created.txt').exists())",
        ],
    )

    first_result = runner.execute_frame(first, tmp_path)
    second_result = runner.execute_frame(second, tmp_path)

    assert first_result["status"] == "completed"
    assert second_result["stdout"].strip() == "False"


def test_job_materialization_rejects_a_generation_symlink_before_copying(
    tmp_path: Path,
) -> None:
    runner = _load_runner()
    source = tmp_path / "generation"
    source.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("must-not-copy", encoding="utf-8")
    try:
        (source / "link.txt").symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    job_root = tmp_path / "job"
    job_root.mkdir()

    with pytest.raises(runner.RunnerProtocolError, match="symlink"):
        runner.materialize_job_workspace(source, job_root)

    assert not (job_root / "workspace" / "link.txt").exists()


def test_runner_declares_bounded_posix_resource_limits() -> None:
    runner = _load_runner()

    limits = runner.resource_limits(timeout_seconds=30)

    assert limits == {
        "address_space_bytes": 2 * 1024 * 1024 * 1024,
        "core_bytes": 0,
        "cpu_seconds": 35,
        "file_bytes": 256 * 1024 * 1024,
        "open_files": 256,
        "processes": 512,
    }


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX process groups run inside FairySandbox"
)
def test_timeout_terminates_the_spawned_process_group(tmp_path: Path) -> None:
    runner = _load_runner()
    child_pid_path = tmp_path / "child.pid"
    parent = (
        "import pathlib, subprocess, sys, time; "
        "child=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
        f"pathlib.Path({str(child_pid_path)!r}).write_text(str(child.pid)); "
        "time.sleep(30)"
    )

    result = runner.run_bounded_process(
        (sys.executable, "-c", parent),
        cwd=tmp_path,
        environment={},
        timeout_seconds=1,
        output_limit_bytes=4096,
        cancellation_path=tmp_path / "cancel",
    )

    assert result.status == "timed_out"
    child_pid = int(child_pid_path.read_text(encoding="ascii"))
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


def test_isolation_command_never_invokes_a_shell() -> None:
    runner = _load_runner()
    request, _archive_bytes = runner.parse_request_frame(_frame(_archive()))

    command = runner.build_isolation_command(request, Path("/srv/fairy/workspace"))

    assert command[0] == "/usr/bin/bwrap"
    assert "--unshare-net" in command
    assert command[-3:] == ("python3", "-c", "print('ok')")
    assert all(item not in {"sh", "bash", "-c"} for item in command[:-3])


def test_public_network_binds_only_minimum_resolution_and_tls_configuration() -> None:
    runner = _load_runner()
    request, _archive_bytes = runner.parse_request_frame(
        _frame(_archive(), project_id=None, version_id=None, network_policy="public")
    )

    command = runner.build_isolation_command(request, Path("/srv/fairy/workspace"))

    triples = tuple(command[index : index + 3] for index in range(len(command) - 2))
    assert "--unshare-net" not in command
    assert "/etc/resolv.conf" in command
    assert "/etc/ssl" in command
    assert "/mnt" not in command
    assert ("--ro-bind", "/", "/") not in triples
