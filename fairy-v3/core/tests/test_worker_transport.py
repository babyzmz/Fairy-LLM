from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic

import pytest

from fairy_core.workspace.worker_transport import (
    RestartingWorkerTransport,
    RustSystemActionWorker,
    SubprocessWorkerTransport,
    WorkerRpcError,
)


class RecordingTransport:
    def __init__(self, result: dict[str, object]) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, object]]] = []

    def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
        self.calls.append((method, params))
        return self.result

    def close(self) -> None:
        return None


class InterruptingTransport(RecordingTransport):
    def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
        self.calls.append((method, params))
        raise WorkerRpcError("worker stopped")


def test_close_interrupts_an_unresponsive_worker_even_through_restart_wrapper():
    entered = Event()

    class ObservedTransport(SubprocessWorkerTransport):
        def _drain_stderr(self, source):
            for line in source:
                if line.strip() == "entered":
                    entered.set()

    script = """
import json, sys, time
for line in sys.stdin:
    request = json.loads(line)
    print("entered", file=sys.stderr, flush=True)
    time.sleep(3)
    print(json.dumps({"id": request["id"], "result": {"done": True}}), flush=True)
"""
    child = ObservedTransport(program=sys.executable, args=("-u", "-c", script), environment={})
    created = []

    def factory():
        created.append(child)
        return child

    transport = RestartingWorkerTransport(factory)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(transport.call, "browser.actions.execute", {})
        try:
            assert entered.wait(2)
            started = monotonic()
            transport.close()
            elapsed = monotonic() - started
            assert elapsed < 2.8, f"close waited for the blocked request: {elapsed:.2f}s"
            with pytest.raises(WorkerRpcError):
                pending.result(timeout=1)
            assert len(created) == 1, "closing must not restart the Worker"
        finally:
            transport.close()


def test_worker_request_deadline_is_public_and_does_not_replay_side_effects():
    script = """
import sys, time
for line in sys.stdin:
    time.sleep(3)
"""
    transport = SubprocessWorkerTransport(
        program=sys.executable,
        args=("-u", "-c", script),
        environment={},
        request_timeout_seconds=0.1,
    )
    try:
        with pytest.raises(WorkerRpcError) as error:
            transport.call("browser.actions.execute", {"idempotency_key": "once"})
        assert error.value.error_code == "WORKER_TIMEOUT"
        assert "unknown" in str(error.value).lower()
        with pytest.raises(WorkerRpcError):
            transport.call("browser.actions.execute", {"idempotency_key": "once"})
    finally:
        transport.close()


def test_subprocess_transport_keeps_one_worker_and_matches_response_ids() -> None:
    script = """
import json, sys
for line in sys.stdin:
    request = json.loads(line)
    response = {
        "jsonrpc": "2.0",
        "id": request["id"],
        "result": {"method": request["method"]},
    }
    print(json.dumps(response), flush=True)
"""
    transport = SubprocessWorkerTransport(
        program=sys.executable,
        args=("-u", "-c", script),
        environment={},
    )

    first = transport.call("workspace.create_empty", {})
    second = transport.call("workspace.diff", {})
    transport.close()

    assert first == {"method": "workspace.create_empty"}
    assert second == {"method": "workspace.diff"}


def test_subprocess_transport_does_not_inherit_host_secrets(monkeypatch) -> None:
    monkeypatch.setenv("FAIRY_TEST_HOST_SECRET", "must-not-leak")
    script = """
import json, os, sys
for line in sys.stdin:
    request = json.loads(line)
    print(json.dumps({
        "jsonrpc": "2.0",
        "id": request["id"],
        "result": {
            "allowed": os.environ.get("FAIRY_ALLOWED"),
            "secret": os.environ.get("FAIRY_TEST_HOST_SECRET"),
        },
    }), flush=True)
"""
    transport = SubprocessWorkerTransport(
        program=sys.executable,
        args=("-u", "-c", script),
        environment={"FAIRY_ALLOWED": "yes"},
    )

    result = transport.call("environment.inspect", {})
    transport.close()

    assert result == {"allowed": "yes", "secret": None}


def test_subprocess_transport_surfaces_typed_worker_errors() -> None:
    script = """
import json, sys
for line in sys.stdin:
    request = json.loads(line)
    response = {
        "jsonrpc": "2.0",
        "id": request["id"],
        "error": {
            "code": -32000,
            "message": "blocked",
            "data": {"error_code": "PATH_OUT_OF_SCOPE"},
        },
    }
    print(json.dumps(response), flush=True)
"""
    transport = SubprocessWorkerTransport(
        program=sys.executable,
        args=("-u", "-c", script),
        environment={},
    )

    with pytest.raises(WorkerRpcError, match="blocked") as captured:
        transport.call("workspace.write_text", {})

    assert captured.value.error_code == "PATH_OUT_OF_SCOPE"
    transport.close()


def test_restarting_transport_recovers_health_without_replaying_writes() -> None:
    interrupted = InterruptingTransport({})
    healthy = RecordingTransport({"available": True})
    transports = iter((interrupted, healthy))
    transport = RestartingWorkerTransport(lambda: next(transports))

    with pytest.raises(WorkerRpcError, match="worker stopped"):
        transport.call("browser.actions.execute", {"idempotency_key": "write-once"})

    assert healthy.calls == []
    assert transport.generation == 1
    assert transport.call("browser.health", {}) == {"available": True}
    assert healthy.calls == [("browser.health", {})]
    transport.close()


def test_restarting_transport_does_not_restart_for_typed_policy_failures() -> None:
    class PolicyFailureTransport(RecordingTransport):
        def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
            raise WorkerRpcError("blocked", error_code="PATH_OUT_OF_SCOPE")

    created = 0

    def factory() -> RecordingTransport:
        nonlocal created
        created += 1
        return PolicyFailureTransport({})

    transport = RestartingWorkerTransport(factory)

    with pytest.raises(WorkerRpcError, match="blocked"):
        transport.call("browser.actions.execute", {})

    assert created == 1
    transport.close()


def test_system_action_transport_accepts_only_exact_tagged_payloads() -> None:
    transport = RecordingTransport(
        {"action_type": "open_settings", "completed": True, "replayed": False}
    )
    worker = RustSystemActionWorker(transport)

    result = worker.execute(
        action={"type": "open_settings", "page": "display"},
        idempotency_key="system:one",
    )

    assert result.completed is True
    assert transport.calls == [
        (
            "system.execute",
            {
                "idempotency_key": "system:one",
                "action": {"type": "open_settings", "page": "display"},
            },
        )
    ]
    with pytest.raises(ValueError, match="unexpected fields"):
        worker.execute(
            action={
                "type": "open_settings",
                "page": "display",
                "command": "whoami",
            },
            idempotency_key="system:two",
        )
    assert len(transport.calls) == 1
