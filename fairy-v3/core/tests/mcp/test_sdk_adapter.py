from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Iterable
from pathlib import Path

import httpx
import pytest
from mcp import types

from fairy_core.domain.ids import new_id
from fairy_core.mcp.http_transport import (
    PinnedMcpHttpTransport,
    authorize_mcp_http_endpoint,
)
from fairy_core.mcp.models import McpCallContext, McpConnection, McpTransport
from fairy_core.mcp.ports import McpError
from fairy_core.mcp.sdk import OfficialMcpConnector, _call_result

PUBLIC_ADDRESS = "93.184.216.34"


class _ScriptedResolver:
    def __init__(self, answers: Iterable[tuple[str, ...]]) -> None:
        self._answers = iter(answers)
        self.calls: list[tuple[str, int]] = []

    def resolve(self, host: str, port: int) -> tuple[str, ...]:
        self.calls.append((host, port))
        return next(self._answers)


class _PeerStream:
    def __init__(self, address: str) -> None:
        self._address = address

    def get_extra_info(self, name: str) -> tuple[str, int] | None:
        return (self._address, 443) if name == "server_addr" else None


class _TrackedBody(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"{}"

    async def aclose(self) -> None:
        self.closed = True


class _RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self, *, peer_address: str = PUBLIC_ADDRESS) -> None:
        self.peer_address = peer_address
        self.request: httpx.Request | None = None
        self.closed = False
        self.body = _TrackedBody()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.request = request
        return httpx.Response(
            200,
            request=request,
            stream=self.body,
            extensions={"network_stream": _PeerStream(self.peer_address)},
        )

    async def aclose(self) -> None:
        self.closed = True


def test_official_sdk_stdio_adapter_negotiates_discovers_and_calls() -> None:
    server = Path(__file__).with_name("stdio_server.py")
    connector = OfficialMcpConnector(timeout_seconds=10)
    connection = McpConnection(
        server_id="contract-server",
        display_name="Contract server",
        transport=McpTransport.STDIO,
        command=sys.executable,
        arguments=(str(server),),
    )
    task_id = new_id()

    discovery = connector.discover(connection)
    result = connector.call_tool(
        connection,
        "echo",
        {"value": "hello"},
        McpCallContext(
            command_run_id=new_id(),
            project_id=None,
            conversation_id=new_id(),
            task_id=task_id,
            version_id=None,
            scope_digest="a" * 64,
            lease_fence=1,
        ),
    )
    connector.close()

    assert discovery.protocol_version == "2025-11-25"
    assert [tool.imported_name for tool in discovery.tools] == ["mcp.contract-server.echo"]
    assert result.structured_content == {"value": "hello"}
    assert result.is_error is False


def test_official_sdk_streamable_http_adapter_negotiates_discovers_and_calls() -> None:
    server = Path(__file__).with_name("stdio_server.py")
    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, str(server), "--http", str(port)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    connector = OfficialMcpConnector(timeout_seconds=10)
    try:
        _wait_for_port(port, process)
        connection = McpConnection(
            server_id="http-contract",
            display_name="HTTP contract server",
            transport=McpTransport.STREAMABLE_HTTP,
            endpoint=f"http://127.0.0.1:{port}/mcp",
        )
        discovery = connector.discover(connection)
        result = connector.call_tool(
            connection,
            "echo",
            {"value": "streamable"},
            McpCallContext(
                command_run_id=new_id(),
                project_id=None,
                conversation_id=new_id(),
                task_id=new_id(),
                version_id=None,
                scope_digest="b" * 64,
                lease_fence=2,
            ),
        )

        assert discovery.protocol_version == "2025-11-25"
        assert [tool.imported_name for tool in discovery.tools] == ["mcp.http-contract.echo"]
        assert result.structured_content == {"value": "streamable"}
    finally:
        connector.close()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def test_official_sdk_classifies_noncanonical_structured_output_without_retry() -> None:
    result = types.CallToolResult(
        content=[types.TextContent(type="text", text="invalid structured value")],
        structuredContent={"value": float("nan")},
        isError=False,
    )

    with pytest.raises(McpError) as error:
        _call_result(result)

    assert error.value.error_code == "MCP_OUTPUT_INVALID"


def test_official_sdk_blocks_nonpublic_http_destinations_before_connecting() -> None:
    connector = OfficialMcpConnector(timeout_seconds=2)
    connection = McpConnection(
        server_id="private-target",
        display_name="Private target",
        transport=McpTransport.STREAMABLE_HTTP,
        endpoint="https://127.0.0.2/mcp",
    )
    try:
        with pytest.raises(McpError) as error:
            connector.discover(connection)
        assert error.value.error_code == "MCP_DESTINATION_BLOCKED"
    finally:
        connector.close()


def test_mcp_http_transport_pins_the_authorized_peer_without_second_dns_lookup() -> None:
    async def exercise() -> None:
        resolver = _ScriptedResolver(((PUBLIC_ADDRESS,), ("127.0.0.1",)))
        target = authorize_mcp_http_endpoint(
            "https://mcp.example.test/mcp",
            resolver=resolver,
        )
        delegate = _RecordingTransport()
        transport = PinnedMcpHttpTransport(target, transport=delegate)
        request = httpx.Request(
            "POST",
            target.endpoint,
            headers={"Authorization": "Bearer deployment-secret"},
            content=b"{}",
        )

        response = await transport.handle_async_request(request)
        await response.aclose()
        await transport.aclose()

        assert resolver.calls == [("mcp.example.test", 443)]
        assert delegate.request is not None
        assert delegate.request.url.host == PUBLIC_ADDRESS
        assert delegate.request.headers["host"] == "mcp.example.test"
        assert delegate.request.headers["authorization"] == "Bearer deployment-secret"
        assert delegate.request.extensions["sni_hostname"] == "mcp.example.test"
        assert delegate.closed is True

    asyncio.run(exercise())


def test_mcp_http_transport_rejects_a_connected_peer_outside_the_dns_snapshot() -> None:
    async def exercise() -> None:
        target = authorize_mcp_http_endpoint(
            "https://mcp.example.test/mcp",
            resolver=_ScriptedResolver(((PUBLIC_ADDRESS,),)),
        )
        delegate = _RecordingTransport(peer_address="8.8.8.8")
        transport = PinnedMcpHttpTransport(target, transport=delegate)

        with pytest.raises(McpError) as error:
            await transport.handle_async_request(httpx.Request("GET", target.endpoint))

        await transport.aclose()
        assert error.value.error_code == "MCP_DESTINATION_BLOCKED"
        assert delegate.body.closed is True

    asyncio.run(exercise())


def test_mcp_http_authorization_rejects_mixed_public_and_private_dns_answers() -> None:
    resolver = _ScriptedResolver(((PUBLIC_ADDRESS, "10.0.0.7"),))

    with pytest.raises(McpError) as error:
        authorize_mcp_http_endpoint("https://mcp.example.test/mcp", resolver=resolver)

    assert error.value.error_code == "MCP_DESTINATION_BLOCKED"


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_port(port: int, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("MCP HTTP contract server exited before readiness")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError("MCP HTTP contract server did not become ready")
