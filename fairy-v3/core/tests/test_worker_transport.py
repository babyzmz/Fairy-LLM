from __future__ import annotations

import sys

import pytest

from fairy_core.workspace.worker_transport import SubprocessWorkerTransport, WorkerRpcError


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
