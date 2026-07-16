from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from threading import RLock
from typing import Any, Protocol
from uuid import UUID

import httpx
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from fairy_core.mcp.http_transport import (
    McpHttpResolver,
    PinnedMcpHttpTransport,
    authorize_mcp_http_endpoint,
)
from fairy_core.mcp.models import (
    MCP_PROTOCOL_VERSION,
    McpCallContext,
    McpCallResult,
    McpConnection,
    McpDiscovery,
    McpToolDescriptor,
    McpTransport,
)
from fairy_core.mcp.ports import (
    McpCancelledError,
    McpError,
    McpTransportInterrupted,
)

_MAX_TOOLS = 256
_MAX_TEXT_BYTES = 256 * 1024


class CredentialResolver(Protocol):
    def resolve(self, reference: str) -> str | None: ...


class MappingCredentialResolver:
    def __init__(self, values: Mapping[str, str] = {}) -> None:
        self._values = {str(key): str(value) for key, value in values.items()}

    def resolve(self, reference: str) -> str | None:
        value = self._values.get(reference)
        return value if value else None


@dataclass(frozen=True, slots=True)
class _ActiveCall:
    loop: asyncio.AbstractEventLoop
    task: asyncio.Task[Any]


class OfficialMcpConnector:
    """Synchronous Core adapter over the official asynchronous MCP SDK."""

    def __init__(
        self,
        *,
        credentials: CredentialResolver | None = None,
        base_environment: Mapping[str, str] | None = None,
        timeout_seconds: float = 30.0,
        http_resolver: McpHttpResolver | None = None,
    ) -> None:
        if not 1 <= timeout_seconds <= 120:
            raise ValueError("MCP timeout_seconds must be between 1 and 120")
        self._credentials = credentials or MappingCredentialResolver()
        self._base_environment = {
            str(name): str(value) for name, value in (base_environment or {}).items() if value
        }
        self._timeout = timeout_seconds
        self._http_resolver = http_resolver
        self._active: dict[UUID, _ActiveCall] = {}
        self._lock = RLock()
        self._closed = False

    def credential_configured(self, connection: McpConnection) -> bool:
        references = set(connection.environment_refs.values())
        if connection.credential_ref is not None:
            references.add(connection.credential_ref)
        return all(self._credentials.resolve(reference) is not None for reference in references)

    def discover(self, connection: McpConnection) -> McpDiscovery:
        self._ensure_open()
        try:
            return asyncio.run(self._discover(connection))
        except McpError:
            raise
        except ValueError as error:
            raise McpError(
                "MCP discovery returned an invalid schema",
                error_code="MCP_SCHEMA_INVALID",
            ) from error
        except Exception as error:
            raise McpError(
                "MCP discovery failed without trusted diagnostics",
                error_code="MCP_UNAVAILABLE",
            ) from error

    def call_tool(
        self,
        connection: McpConnection,
        tool_name: str,
        arguments: dict[str, object],
        context: McpCallContext,
    ) -> McpCallResult:
        self._ensure_open()
        try:
            return asyncio.run(self._call_tool(connection, tool_name, arguments, context))
        except McpCancelledError:
            raise
        except McpError:
            raise
        except Exception as error:
            raise McpTransportInterrupted(
                "MCP transport ended before a trusted result was received",
                response_started=True,
            ) from error

    def cancel(self, command_run_id: UUID) -> None:
        with self._lock:
            active = self._active.get(command_run_id)
        if active is not None and not active.loop.is_closed():
            active.loop.call_soon_threadsafe(active.task.cancel)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            active = tuple(self._active.values())
        for call in active:
            if not call.loop.is_closed():
                call.loop.call_soon_threadsafe(call.task.cancel)

    async def _discover(self, connection: McpConnection) -> McpDiscovery:
        async with self._session(connection) as session:
            initialized = await session.initialize()
            _validate_initialize(initialized)
            tools: list[McpToolDescriptor] = []
            cursor: str | None = None
            while True:
                page = await session.list_tools(cursor=cursor)
                for tool in page.tools:
                    if len(tools) >= _MAX_TOOLS:
                        raise McpError(
                            "MCP server exposes too many tools", error_code="MCP_SCHEMA_INVALID"
                        )
                    tools.append(
                        McpToolDescriptor.create(
                            name=tool.name,
                            title=tool.title,
                            description=tool.description or tool.title or tool.name,
                            input_schema=tool.inputSchema,
                            output_schema=tool.outputSchema,
                        )
                    )
                cursor = page.nextCursor
                if cursor is None:
                    break
                if not cursor or len(cursor) > 2_048:
                    raise McpError(
                        "MCP pagination cursor is invalid", error_code="MCP_SCHEMA_INVALID"
                    )
            return McpDiscovery.create(
                server_name=connection.server_id,
                protocol_version=MCP_PROTOCOL_VERSION,
                tools=tuple(tools),
            )

    async def _call_tool(
        self,
        connection: McpConnection,
        tool_name: str,
        arguments: dict[str, object],
        context: McpCallContext,
    ) -> McpCallResult:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("MCP call has no asyncio task")
        loop = asyncio.get_running_loop()
        with self._lock:
            if self._closed:
                raise McpError("MCP connector is closed", error_code="MCP_UNAVAILABLE")
            if context.command_run_id in self._active:
                raise McpError(
                    "MCP CommandRun is already active", error_code="MCP_RESULT_UNCERTAIN"
                )
            self._active[context.command_run_id] = _ActiveCall(loop=loop, task=task)
        request_started = False
        try:
            async with self._session(connection) as session:
                initialized = await session.initialize()
                _validate_initialize(initialized)
                request_started = True
                result = await session.call_tool(
                    tool_name,
                    arguments=arguments,
                    read_timeout_seconds=timedelta(seconds=self._timeout),
                )
                return _call_result(result)
        except asyncio.CancelledError as error:
            raise McpCancelledError("MCP call was cancelled") from error
        except McpError:
            raise
        except Exception as error:
            raise McpTransportInterrupted(
                "MCP transport ended before a trusted result was received",
                response_started=request_started,
            ) from error
        finally:
            with self._lock:
                self._active.pop(context.command_run_id, None)

    @asynccontextmanager
    async def _session(self, connection: McpConnection):
        if connection.transport is McpTransport.STDIO:
            environment = dict(self._base_environment)
            for name, reference in connection.environment_refs.items():
                value = self._credentials.resolve(reference)
                if value is None:
                    raise McpError(
                        "MCP credential reference is unavailable",
                        error_code="MCP_CREDENTIAL_UNAVAILABLE",
                    )
                environment[name] = value
            parameters = StdioServerParameters(
                command=connection.command or "",
                args=list(connection.arguments),
                env=environment,
                encoding="utf-8",
                encoding_error_handler="strict",
            )
            async with (
                stdio_client(
                    parameters,
                    errlog=subprocess.DEVNULL,  # type: ignore[arg-type]
                ) as (read, write),
                ClientSession(
                    read,
                    write,
                    read_timeout_seconds=timedelta(seconds=self._timeout),
                ) as session,
            ):
                yield session
            return
        headers: dict[str, str] = {}
        assert connection.endpoint is not None
        target = await asyncio.to_thread(
            authorize_mcp_http_endpoint,
            connection.endpoint,
            resolver=self._http_resolver,
        )
        if connection.credential_ref is not None:
            credential = self._credentials.resolve(connection.credential_ref)
            if credential is None:
                raise McpError(
                    "MCP credential reference is unavailable",
                    error_code="MCP_CREDENTIAL_UNAVAILABLE",
                )
            headers["Authorization"] = f"Bearer {credential}"
        timeout = httpx.Timeout(self._timeout, connect=min(10.0, self._timeout))
        async with (
            httpx.AsyncClient(
                headers=headers,
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
                transport=PinnedMcpHttpTransport(target),
            ) as client,
            streamable_http_client(
                connection.endpoint or "",
                http_client=client,
                terminate_on_close=True,
            ) as (read, write, _session_id),
            ClientSession(
                read,
                write,
                read_timeout_seconds=timedelta(seconds=self._timeout),
            ) as session,
        ):
            yield session

    def _ensure_open(self) -> None:
        with self._lock:
            if self._closed:
                raise McpError("MCP connector is closed", error_code="MCP_UNAVAILABLE")


def _validate_initialize(result: types.InitializeResult) -> None:
    if str(result.protocolVersion) != MCP_PROTOCOL_VERSION:
        raise McpError(
            "MCP protocol version is not accepted",
            error_code="MCP_PROTOCOL_MISMATCH",
        )
    if result.capabilities.tools is None:
        raise McpError("MCP server does not expose tools", error_code="MCP_CAPABILITY_MISSING")


def _call_result(result: types.CallToolResult) -> McpCallResult:
    try:
        text_parts: list[str] = []
        total = 0
        for block in result.content:
            if not isinstance(block, types.TextContent):
                raise McpError(
                    "MCP result contains an unsupported content block",
                    error_code="MCP_OUTPUT_UNSUPPORTED",
                )
            encoded = block.text.encode("utf-8")
            total += len(encoded)
            if total > _MAX_TEXT_BYTES:
                raise McpError(
                    "MCP result text is too large",
                    error_code="MCP_OUTPUT_INVALID",
                )
            text_parts.append(block.text)
        text = "\n".join(text_parts).strip() or None
        structured = result.structuredContent
        digest_payload = json.dumps(
            {"text": text, "structured_content": structured, "is_error": result.isError},
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return McpCallResult.create(
            text=text,
            structured_content=structured,
            is_error=result.isError,
            response_id=hashlib.sha256(digest_payload).hexdigest(),
        )
    except McpError:
        raise
    except (TypeError, UnicodeError, ValueError) as error:
        raise McpError(
            "MCP result is not bounded canonical JSON",
            error_code="MCP_OUTPUT_INVALID",
        ) from error


__all__ = [
    "CredentialResolver",
    "MappingCredentialResolver",
    "OfficialMcpConnector",
]
