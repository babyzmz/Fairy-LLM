# MCP Streamable HTTP Peer Pinning

## Scope and completion rule

This acceptance specification covers outbound Streamable HTTP MCP connections in
local and cloud Core compositions. The change is complete only when every MCP HTTP
request connects to an address from the one authorized DNS snapshot while preserving
the original HTTP authority and TLS server name.

## Observable behavior and invariants

- Public MCP endpoints resolve exactly once when a session opens. Every request in
  that session connects to one address from that immutable result.
- Loopback HTTP remains available only for the exact development endpoint names
  accepted by `McpConnection`: `localhost`, `127.0.0.1`, and `::1`.
- A public hostname is rejected when any resolved address is private, loopback,
  link-local, reserved, multicast, or otherwise non-global.
- The wire connection uses the pinned IP, while `Host` and TLS SNI continue to use
  the original endpoint authority. Certificate verification is never disabled.
- Authorization is resolved only after destination authorization and can only be
  sent through the pinned transport. Environment proxies and redirects remain off.
- A missing or mismatched connected peer closes the response and fails with
  `MCP_DESTINATION_BLOCKED`.
- Cloud host allowlisting remains an additional policy boundary; it does not replace
  peer pinning.

## State ownership and risks

| State | Owner | Lifetime | Recovery rule |
| --- | --- | --- | --- |
| Authorized DNS snapshot | MCP HTTP session | One SDK session | Re-resolve only for a new session |
| Pinned address | MCP HTTP transport | One SDK session | Session fails closed if unavailable |
| Credential value | Credential resolver and request headers | One session | Never persisted by this adapter |
| MCP session ID | Official MCP SDK | One SDK session | Cannot migrate to a newly resolved peer |

Primary risks are DNS rebinding between validation and connection, leaking a bearer
credential to a private service, breaking TLS hostname verification while connecting
by IP, and accidentally accepting a proxy peer. The transport therefore pins the
network URL, restores Host/SNI explicitly, disables proxy use, and validates the
actual peer exposed by HTTPX.

## Acceptance scenarios

| Scenario | Required result | Forbidden result | Environment |
| --- | --- | --- | --- |
| DNS changes after authorization | Request still targets the original public IP; resolver called once | Second lookup reaches private IP | Core unit test with scripted resolver and transport |
| Public peer mismatch | Response is closed and request fails closed | MCP payload is accepted | Core unit test |
| Mixed public/private DNS answer | Session is rejected before credential resolution | Any network request | Core unit test |
| Local contract server | Discovery and tool call succeed over pinned loopback | Development MCP regression | Real loopback MCP server |
| Cloud allowlist plus rebinding | Allowlisted host still uses pinned public peer | Host allowlist bypasses peer validation | Cloud connector test |
| HTTPS endpoint | Original Host and SNI are retained | Certificate validation against raw IP | Transport unit test; live TLS smoke when configured |

## Evidence and automation boundaries

Scripted unit transports prove destination rewriting, header/SNI preservation,
single-resolution behavior, peer mismatch handling, and response closure without
using external DNS. The existing loopback MCP contract process proves compatibility
with the official SDK and real sockets. A public live MCP endpoint is not required
for deterministic CI; therefore external CA, DNS, and Internet availability are not
claimed as tested by the unit suite.

## Evidence log

Pre-fix reproduction on 2026-07-16:

```powershell
cd core
.venv\Scripts\python.exe -m pytest tests\mcp\test_sdk_adapter.py -q
```

Collection failed because the Core exposed no peer-pinned MCP transport. The old
adapter performed `_validate_http_destination` and then handed the original hostname
to a separate HTTPX connection, leaving a second DNS resolution on the credentialed
request path.

Post-fix evidence on 2026-07-16:

- `tests\mcp`: 25 passed, including the real loopback MCP process.
- `cloud\tests\test_mcp_connector.py`: 3 passed for transport and host allowlisting.
- Targeted Ruff check, Ruff format check, repository boundary check, TOML parse, and
  lock metadata assertion passed.
- A live HTTPS smoke request to `https://example.com/` returned 200 through the pinned
  transport. This confirms real socket peer reporting and TLS SNI behavior on the
  current Windows environment; it is supporting evidence rather than deterministic
  CI coverage.
