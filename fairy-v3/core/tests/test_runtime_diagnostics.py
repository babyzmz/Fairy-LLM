import io
import json

import pytest
from sqlalchemy import text

from fairy_core.diagnostics import RuntimeCounters, local_runtime_snapshot
from fairy_core.transports.jsonrpc import JsonRpcDispatcher
from fairy_core.transports.stdio import build_local_service, process_stream


def test_counters_are_bounded_and_never_accept_user_content():
    counters = RuntimeCounters()
    counters.observe("sql", 2_000_000)
    counters.observe("sql", -2)
    assert counters.snapshot() == {"sql": {"count": 2, "total_ms": 2, "max_ms": 2}}
    with pytest.raises(ValueError):
        counters.observe("select secret from credentials")


def test_negotiated_local_diagnostics_never_starts_models_or_exposes_sql(tmp_path):
    service = build_local_service(tmp_path)
    try:
        with service._unit_of_work_factory._engine.connect() as connection:
            connection.execute(
                text("SELECT :private_value"), {"private_value": "PRIVATE_DIAGNOSTIC_SENTINEL"}
            )
        snapshot = local_runtime_snapshot(service)
        assert snapshot["database"]["sql"]["count"] > 0
        assert snapshot["workflow"]["worker_limit"] == 4
        assert snapshot["workflow"]["active_nodes"] == 0
        output = io.StringIO()
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "transport.diagnostics", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "transport.negotiate", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "transport.diagnostics", "params": {}},
        ]
        process_stream(
            JsonRpcDispatcher(service),
            io.StringIO("\n".join(json.dumps(item) for item in requests) + "\n"),
            output,
        )
        serialized = output.getvalue()
        assert "PRIVATE_DIAGNOSTIC_SENTINEL" not in serialized
        assert str(tmp_path) not in serialized
        rows = {item["id"]: item for item in map(json.loads, serialized.splitlines())}
        assert rows[1]["error"]["data"]["error_code"] == "RPC_NOT_NEGOTIATED"
        assert rows[3]["result"]["schema_version"] == 1
        assert "rpc.read.queue" in rows[3]["result"]["rpc"]
    finally:
        service.close()
