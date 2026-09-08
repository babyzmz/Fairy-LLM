from __future__ import annotations

import io
import json
from threading import Event, Thread

from fairy_core.transports.stdio import process_stream
from fairy_core.transports.stdio_dispatch import LANE_CAPACITY, StdioRequestDispatcher


def _request(key: int, method: str) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": key, "method": method, "params": {}}) + "\n"


def test_negotiated_control_and_reads_bypass_a_blocked_serial_request():
    slow_started, release, control_done, read_done = (Event() for _ in range(4))

    class Dispatcher:
        def dispatch(self, request):
            method = request["method"]
            if method == "slow.write":
                slow_started.set()
                assert release.wait(5)
            if method == "assistant.turns.pause":
                control_done.set()
            if method == "assistant.turns.get":
                read_done.set()
            return {"jsonrpc": "2.0", "id": request["id"], "result": method}

    source = io.StringIO(
        _request(0, "transport.negotiate")
        + _request(1, "slow.write")
        + _request(2, "assistant.turns.pause")
        + _request(3, "assistant.turns.get")
    )
    destination = io.StringIO()
    thread = Thread(target=process_stream, args=(Dispatcher(), source, destination))
    thread.start()
    try:
        assert slow_started.wait(2)
        assert control_done.wait(0.5), "control request is blocked behind slow work"
        assert read_done.wait(0.5), "declared read is blocked behind slow work"
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    responses = [json.loads(line) for line in destination.getvalue().splitlines()]
    ids = [response["id"] for response in responses]
    assert sorted(ids) == [0, 1, 2, 3]
    assert ids.index(2) < ids.index(1)
    assert ids.index(3) < ids.index(1)


def test_unnegotiated_client_keeps_sequential_dispatch_and_output():
    methods = []

    class Dispatcher:
        def dispatch(self, request):
            methods.append(request["method"])
            return {"id": request["id"], "result": {}}

    destination = io.StringIO()
    process_stream(
        Dispatcher(), io.StringIO(_request(1, "write") + _request(2, "health")), destination
    )
    assert methods == ["write", "health"]
    assert [json.loads(line)["id"] for line in destination.getvalue().splitlines()] == [1, 2]


def test_unknown_get_methods_remain_serial_and_capacity_is_bounded():
    started, release, second_started = Event(), Event(), Event()

    class Dispatcher:
        def dispatch(self, request):
            if request["id"] == 1:
                started.set()
                assert release.wait(5)
            else:
                second_started.set()
            return {"id": request["id"], "result": {}}

    destination = io.StringIO()
    transport = StdioRequestDispatcher(Dispatcher(), destination)
    try:
        transport.dispatch(json.loads(_request(0, "transport.negotiate")))
        transport.dispatch(json.loads(_request(1, "extension.get")))
        assert started.wait(2)
        for key in range(2, LANE_CAPACITY + 2):
            transport.dispatch(json.loads(_request(key, "extension.get")))
        assert not second_started.is_set()
        responses = [json.loads(line) for line in destination.getvalue().splitlines()]
        assert responses[-1]["id"] == LANE_CAPACITY + 1
        assert responses[-1]["error"]["data"]["error_code"] == "RPC_CAPACITY_EXCEEDED"
        transport.dispatch(json.loads(_request(1, "health")))
        assert json.loads(destination.getvalue().splitlines()[-1])["error"]["data"][
            "error_code"
        ] == ("RPC_DUPLICATE_REQUEST_ID")
    finally:
        release.set()
        transport.close()
    responses = [json.loads(line) for line in destination.getvalue().splitlines()]
    assert len([response for response in responses if "result" in response]) == LANE_CAPACITY + 1


def test_invalid_method_envelope_does_not_stop_following_requests():
    class Dispatcher:
        def dispatch(self, request):
            return {"id": request["id"], "result": {}}

    destination = io.StringIO()
    process_stream(
        Dispatcher(),
        io.StringIO(
            _request(0, "transport.negotiate")
            + json.dumps({"id": 1, "method": []})
            + "\n"
            + _request(2, "health")
        ),
        destination,
    )
    assert sorted(json.loads(line)["id"] for line in destination.getvalue().splitlines()) == [
        0,
        1,
        2,
    ]


def test_broken_output_does_not_start_queued_side_effects():
    started, release = Event(), Event()
    calls = []

    class Destination(io.StringIO):
        def write(self, value):
            if self.getvalue():
                raise BrokenPipeError("client disconnected")
            return super().write(value)

    class Dispatcher:
        def dispatch(self, request):
            calls.append(request["id"])
            if request["id"] == 1:
                started.set()
                assert release.wait(5)
            return {"id": request["id"], "result": {}}

    transport = StdioRequestDispatcher(Dispatcher(), Destination())
    try:
        transport.dispatch(json.loads(_request(0, "transport.negotiate")))
        transport.dispatch(json.loads(_request(1, "write")))
        assert started.wait(2)
        transport.dispatch(json.loads(_request(2, "write")))
        transport.dispatch(json.loads(_request(3, "write")))
    finally:
        release.set()
        transport.close()
    assert calls == [1]
