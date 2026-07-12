# ADR 0013: Keep Tool Protocol Out of the User Chat Projection

- Status: Accepted
- Date: 2026-07-12

## Context

Assistant tool results are durable messages because retries, approvals, and
audits require an exact record. Rendering those messages in the ordinary chat
surface exposed provider payloads and protocol wrappers as if they were Fairy's
answer, even when Developer Mode was disabled.

## Decision

The Ledger continues to persist user, assistant, tool, and system-notice
messages. The ordinary desktop chat projection excludes messages whose role is
`tool`. Developer Mode may render them with Task, Turn, and sequence metadata
for explicit inspection. User and assistant messages, user-visible Core notices,
approval state, and the final assistant response remain visible normally.

Tool approval decisions continue through Core. Rejecting a tool resumes the
same durable Turn exactly once with a rejection result; the Renderer does not
invent a replacement response or execute the requested effect.

## Consequences

- Third-party payloads and tool protocol framing do not appear as user-facing
  assistant prose.
- Full durable evidence remains available for development and audit.
- Tool execution and rejection remain recoverable without duplicating a Turn.
