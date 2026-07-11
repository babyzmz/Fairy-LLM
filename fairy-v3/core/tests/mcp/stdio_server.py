from __future__ import annotations

import sys

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

http_mode = len(sys.argv) == 3 and sys.argv[1] == "--http"
server = FastMCP(
    "Fairy MCP contract server",
    host="127.0.0.1",
    port=int(sys.argv[2]) if http_mode else 8000,
    stateless_http=http_mode,
    json_response=http_mode,
)


class EchoResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


@server.tool()
def echo(value: str) -> EchoResult:
    """Echo one bounded value."""

    return EchoResult(value=value)


if __name__ == "__main__":
    server.run(transport="streamable-http" if http_mode else "stdio")
