# ADR 0009: Govern Product Skills and MCP Extensions Through Core

- Status: accepted
- Date: 2026-07-12

## Context

Fairy needs reusable product instructions and third-party tools without creating
a second execution authority. Both inputs are untrusted: a Skill package can
contain prompt injection or excessive capability claims, while an MCP server can
change its tool schema, return hostile content, disconnect after a side effect,
or attempt to acquire Scope and credentials from the model.

The standards baseline for this decision is MCP revision `2025-11-25` and the
stable MCP Python SDK `1.28.1`. MCP v2 remains a prerelease, so Fairy pins
`mcp>=1.28.1,<2`. Local servers use stdio and remote servers use Streamable HTTP.
The removed HTTP+SSE transport is not accepted. Fairy Skill manifests are a
product extension around the Agent Skills `SKILL.md` format; they are not
presented as part of the Agent Skills standard.

## Decision

### Skills

A Fairy Skill is an immutable package with a strict `SKILL.md` and a separate,
versioned `fairy-skill.json` manifest. The manifest declares package identity,
content digest, provenance, instruction entry point, JSON input contract,
required Fairy capabilities, and compatible MCP server IDs. Core validates the
package before registration and exposes only metadata until the Skill is
selected for a Task. It then loads bounded instructions as untrusted guidance.

Skills cannot contain executable hooks, resolve credentials, mutate policy,
hold Project or Conversation state, add arbitrary tools, or supply Scope fields.
Optional package resources are read-only prompt material and remain inside the
validated package root. Fairy's product Skill directories are independent of
Codex's local skill system.

### MCP

MCP servers require explicit, durable trust configuration. Configuration owns
the server ID, transport, endpoint or fixed argv, environment credential
references, enabled state, and trust revision. None of those fields enter model
context. Stdio commands are administrator/user configured and are never model
arguments. Streamable HTTP endpoints must use HTTPS outside loopback and are
connected by the Cloud adapter; bearer credentials are resolved at composition
time and sent only by the transport.

After MCP lifecycle negotiation, Core imports only server tools. Each tool name
is sanitized and materialized as `mcp.<server_id>.<tool_name>` in the single
`ToolRegistry`. Input and output schemas are bounded JSON Schema objects. MCP
annotations are treated as advisory; the Fairy trust record defines side
effect, risk, approval, idempotency, and required profiles. A tool-list change
or reconnect creates a new schema digest and Registry generation. A running
Turn keeps the definition generation it was offered; schema drift fails the
call closed and requires a fresh model round.

Every call follows the existing pipeline:

`Task Scope -> Capability Manifest -> Policy -> Approval -> CommandRun -> MCP adapter -> Artifact/Event`

Core strips model-supplied identity, path, endpoint, credential, transport,
capability, and Scope fields before validation, then injects the immutable Task
Scope. Results are bounded and labelled as untrusted data. Structured output is
validated against the negotiated output schema. User-visible MCP output is
persisted as a Task-owned Artifact before the CommandRun succeeds.

### Recovery

Read-only calls explicitly marked idempotent may be retried only with the same
CommandRun identity after reconnect. A disconnect or process exit during any
other call is an uncertain side effect: the run fails with
`MCP_RESULT_UNCERTAIN`, no automatic replay occurs, and a later user action must
create a new invocation. Duplicate terminal responses are ignored by durable
run identity. Cancellation closes the active request/session and records the
normal cancelled transition. Credentials, transport logs, raw protocol frames,
and server diagnostics are never written to events or model messages.

Lifecycle mutations reserve their idempotency key before changing trust state.
The durable result is a server record, deletion tombstone, explicit error, or
pending uncertain outcome. Tombstones are not deleted with the server. A crash
around discovery therefore cannot silently reconnect or reinterpret deletion.

Cloud does not proxy arbitrary MCP traffic through the public API. Tenant-bound
Core services receive a Cloud MCP connector configured from deployment-owned
trust records and secret references. Local Core receives a stdio connector from
device-owned configuration. Both implement the same Core port and contract
tests.

## Consequences

- Skills and MCP extend Agent behavior without extending model authority.
- Tool availability, frontend metadata, policy, and Agent exposure continue to
  come from one Registry.
- Server schema changes can temporarily remove tools until the user refreshes
  and accepts the new trust revision.
- Non-idempotent MCP effects favor explicit interruption over unsafe replay.
- MCP resources, prompts, sampling, elicitation, and experimental tasks are not
  part of the first V3 extension surface; they can be added behind separate
  governed contracts later.

## References

- <https://modelcontextprotocol.io/specification/2025-11-25>
- <https://modelcontextprotocol.io/specification/2025-11-25/basic/transports>
- <https://modelcontextprotocol.io/specification/2025-11-25/server/tools>
- <https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization>
- <https://github.com/modelcontextprotocol/python-sdk/tree/v1.x>
- <https://agentskills.io/specification>

## Verification

Core tests cover Skill package traversal and digest checks, malicious and
oversized schemas, reserved argument stripping, policy/approval matrices,
artifact ownership, schema drift, cancellation, duplicate responses, and
fail-closed disconnect recovery. Adapter contract tests exercise both stdio and
Streamable HTTP through the official SDK. Real remote-service evidence is
reported separately from deterministic in-process protocol tests.
