from __future__ import annotations

import io
import json
from pathlib import Path

from fairy_core.transports.stdio import build_local_dispatcher, process_stream


def test_stdio_processes_one_jsonrpc_response_per_input_line(tmp_path: Path) -> None:
    dispatcher = build_local_dispatcher(tmp_path)
    source = io.StringIO(
        "\n".join(
            (
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "health", "params": {}}),
                "{invalid-json",
                "",
            )
        )
    )
    destination = io.StringIO()

    process_stream(dispatcher, source, destination)

    responses = [json.loads(line) for line in destination.getvalue().splitlines()]
    assert responses[0]["id"] == 1
    assert responses[0]["result"]["status"] == "ok"
    assert responses[1]["id"] is None
    assert responses[1]["error"]["code"] == -32700
    assert len(responses) == 2
