# Evidence-First Tool Calls Acceptance

## Observable behavior

- Stable conversation and durable common knowledge may complete without an external tool.
- Claims about the current Workspace, current Runtime, current private data, or recent public
  information complete only after the Turn has collected matching, current evidence.
- A completed evidence-sensitive answer shows a collapsed, keyboard-accessible Sources footer.
- Missing, stale, failed, cancelled, cross-Scope, or cross-Version evidence cannot satisfy a Turn.
- Project questions can discover files through governed list/search tools and can run bounded
  inspection commands only in an attested, networkless, read-only FairySandbox snapshot.
- A Workspace modification reads every existing planned file at the exact planned content hash
  before it can create an Execution Plan or propose a Changeset.
- When evidence cannot be obtained, Fairy states that the current fact was not verified, exposes
  a retry path, and does not replace the result with an unverified model claim.

## State ownership and scope keys

| State | Owner | Scope key | Persistence rule |
|---|---|---|---|
| Evidence requirements | Assistant routing | `turn_id + scope_digest` | immutable after routing |
| Evidence receipt | Tool Invocation | `turn_id + tool_invocation_id + receipt_id` | committed atomically with successful command result |
| Final evidence selection | Assistant Turn | `turn_id + receipt_id` | committed with final assistant message |
| Project discovery cursor | Core Workspace tools | `version_id + index_generation + query_digest` | opaque and rejected outside its issuing Scope |
| Terminal inspection | Sandbox Command | `task_id + version_id + workspace_generation + lease_fence` | output is bounded and hash-bound |
| Public source projection | Turn Trace | `turn_id + trace_revision` | derived from cited receipts only |

Core is the authority for classification, receipt sealing, completion validation, Source
projection, and safe opening targets. Providers propose tools and receipt IDs but cannot supply
Scope, Version, Snapshot, expiry, path roots, or source authority metadata. Desktop renders the
projection and owns no evidence state.

## Invariants

- Evidence requirement kinds are closed: Workspace structure, Workspace content, current web,
  current Runtime, and current private data.
- Every cited receipt belongs to a successful Tool Invocation in the same Turn and Scope.
- Workspace evidence binds the immutable Version, Project Index generation, and content hash.
- Private evidence binds the Turn's Memory, Knowledge, or Document Snapshot identity and hash.
- Runtime evidence binds the current Runtime or page revision and expires after a bounded time.
- Web search is discovery; current public claims require a fetched or structured provider result.
- Receipts contain safe metadata only. They do not duplicate source content, screenshots,
  credentials, secret-bearing URLs, absolute Vault paths, or hidden reasoning.
- Plain model text cannot finish an evidence-sensitive Turn. Completion requires a validated
  `direct_answer` receipt selection.
- Old Turns without evidence fields remain readable and render no empty Sources control.
- A late result from conversation A cannot satisfy or render in conversation B in either
  direction, including after rapid switching and restart recovery.

## Acceptance scenarios

1. Stable greeting: route with no requirements, make no external call, and complete normally.
2. Current code question: list/search the managed Version, inspect or read matching source, cite
   the same-Turn receipt, and open the cited read-only file and line from the final reply.
3. Existing-file modification: reject planning before the exact current file hash was read;
   accept a new file without a pre-read; reject a stale read after the index changes.
4. Recent public fact: treat search as discovery, require fetch or a structured information tool,
   reject an expired receipt, and show a sanitized public domain and observation time.
5. Current Preview: require matching Task, Version, Runtime and page revision; reject the receipt
   after navigation or Runtime replacement.
6. Private data: require the Turn-bound Snapshot, never expose a device Vault root, and reject a
   receipt copied from a second Project or Snapshot.
7. Manual model selection: perform the same closed evidence classification as Auto routing;
   report an explicit compatibility failure when the selected provider supports neither
   structured output nor tool calls.
8. Terminal inspection: allow the documented argv subset; reject shell syntax, unsafe flags,
   absolute/traversing operands, interpreter execution, network, output floods, timeouts, and
   cancellation races without touching the host Workspace.
9. Recovery: close and reopen Core after tool completion and before finalization; resume with the
   same receipts exactly once and produce one final message.
10. UI isolation: switch quickly between two conversations while Sources are loading; late trace
    A never appears under reply B, and returning to A restores only A's Sources.

## Required environments and evidence

- Structural: repository boundary checks, generated contract drift, migration upgrade/downgrade,
  Ruff, TypeScript, Rust format, and Clippy with warnings denied.
- Automated: focused Core routing/tool/persistence tests, Sandbox Runner tests, Cloud execution and
  migration tests, Desktop Vitest, and chat Playwright at 880x680 and 640x700.
- Real provider: one configured tool-capable model in Auto and Manual modes covering direct chat,
  Workspace evidence, web evidence, Runtime evidence, unavailable evidence, and restart recovery.
- Real local sandbox: current FairySandbox attestation, read-only inspection, cancellation,
  timeout, no-network, and no-host-mount probes.
- Native Desktop: one Tauri WebView2 pass for Sources focus/open behavior and constrained-window
  layout. Browser automation does not substitute for native file opening or WebView focus.

## Failure, cancellation, and timeout behavior

- Malformed or unsupported classification ends with `EVIDENCE_CLASSIFICATION_FAILED`.
- Missing or wrong-kind receipts are rejected with `EVIDENCE_REQUIRED` and use the existing
  bounded reconciliation retry; an identical repeated failure is terminal.
- Expired or revision-mismatched evidence is rejected with `EVIDENCE_STALE`.
- Unavailable required tools end with `EVIDENCE_UNAVAILABLE` and preserve their public failure
  state for retry. They never create a successful receipt.
- Cancellation fences pending classification, tool execution, receipt persistence, finalization,
  and Source projection. Terminal Turns stop workers, polling, spinners, and subscriptions.

## Automation boundaries

- Scripted models prove dispatch and hard-gate behavior but not real provider tool selection.
- Fixture Sandbox executors prove Core binding but not a real WSL mount, seccomp, or network
  boundary; the attested WSL gate is reported separately.
- Vitest and Playwright cannot prove native file opening, WebView2 focus, or Windows DPI behavior.
- SQLite recovery does not prove PostgreSQL RLS or worker claims; Cloud/PostgreSQL evidence requires
  the real integration environment and must not be inferred from static schema checks.

