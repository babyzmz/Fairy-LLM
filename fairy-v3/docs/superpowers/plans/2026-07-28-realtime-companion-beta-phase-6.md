# Realtime Companion Beta Phase 6 Implementation Plan

> **Design:** `docs/superpowers/specs/2026-07-28-realtime-companion-beta-phase-6-design.md`
>
> **Authority:** the frozen Phase 0–5 contracts and the user-approved
> Realtime Companion Beta final design.

## Goal

Route governed Realtime knowledge gaps into one durable, scoped Core Assistance
request; write the complete Fairy answer into the linked main conversation;
return only a bounded spoken summary to the current Worker; and preserve
Command Bus, approval, restart, privacy, and identity boundaries.

## Non-goals

This phase does not automate external writes, input control, login, payment,
email, messages, memory promotion, four-hour soak, GPU pressure governance,
Sidecar quarantine, installer packaging, paid-provider calls, or production
answer-quality claims.

## Task 1: Persist the Realtime Assistance aggregate

**Files**

- Modify `core/src/fairy_core/contracts/realtime.py`
- Modify `core/src/fairy_core/contracts/methods.py`
- Modify `core/src/fairy_core/realtime/models.py`
- Modify `core/src/fairy_core/realtime/ports.py`
- Modify `core/src/fairy_core/realtime/repository.py`
- Modify `core/src/fairy_core/storage/realtime_schema.py`
- Modify `core/src/fairy_core/storage/schema.py`
- Modify Core serialization/export modules where required
- Add focused Core tests

**Implementation**

1. Add strict bounded request/get/cancel contracts and a public result model.
2. Add immutable request provenance, request fingerprint, status, links,
   result, error, timestamps, and revision to `RealtimeAssistance`.
3. Add a tenant-scoped table with unique `(tenant, session, request_id)`,
   session and conversation foreign keys, status/revision checks, and indexes.
4. Add repository create/replay/get/update/list-nonterminal operations with
   optimistic revision checks.
5. Reject mismatched replays and never persist raw media, hidden prompts, or
   credentials.

**Tests**

- contract bounds and enum serialization;
- idempotent replay and conflict;
- tenant/session/conversation isolation;
- optimistic revision conflict;
- status transitions and terminal immutability;
- SQLite/PostgreSQL metadata shape.

**Commit**

`feat(core): persist realtime assistance`

## Task 2: Orchestrate Assistance through Task and Assistant Turn

**Files**

- Modify `core/src/fairy_core/realtime/application.py`
- Modify `core/src/fairy_core/application/realtime_service.py`
- Modify `core/src/fairy_core/application/service.py`
- Modify Assistant/Research policy only where an explicit Realtime restriction
  is required
- Add `core/tests/test_realtime_assistance.py`
- Update method-catalog and service-integration tests

**Implementation**

1. Validate the linked nonterminal Session and Conversation.
2. Admit one request only when no conflicting Conversation Task is active;
   otherwise preserve a queued, side-effect-free state.
3. Create an idempotent `ANSWER` Task, current-model Assistant Turn, and durable
   scheduler work.
4. Reconcile running, awaiting-approval, completed, failed, and cancelled Turn
   state into the aggregate.
5. Use the final assistant Message as `display_markdown`; derive a bounded
   plain-text first-semantic-paragraph spoken summary.
6. Extract only validated research citation metadata.
7. Cancel through the ordinary Assistant cancellation path.
8. Enforce the Realtime capability denylist before tool execution and require
   explicit `allow_network` for public research.
9. Emit bounded Ledger events for request, state, and result.

**Tests**

- exactly one Task/Turn/Message per request;
- busy Conversation queues without changing `active_task_id`;
- project Knowledge snapshot and Harness are bound;
- web/game-guide access requires explicit online permission;
- external write and input-control capabilities are denied or approved only in
  the main workspace;
- approval resumes the same request;
- full answer and spoken summary are separated;
- restart reconciliation, failure, cancellation, and duplicate calls converge.

**Commit**

`feat(core): orchestrate realtime assistance`

## Task 3: Route Worker candidates through Core natively

**Files**

- Create `desktop/src-tauri/src/realtime_assistance.rs`
- Modify `desktop/src-tauri/src/lib.rs`
- Modify `desktop/src-tauri/src/realtime_worker.rs`
- Modify `desktop/src-tauri/crates/realtime-worker/src/protocol.rs`
- Modify `desktop/src-tauri/crates/realtime-worker/src/main.rs`
- Modify `desktop/src-tauri/crates/realtime-worker/src/runtime.rs`
- Add focused Rust tests

**Implementation**

1. Inject the shared Core bridge and current session projection into a bounded
   native Assistance router.
2. Convert Director-approved `assistance_request` projections into the three
   Core methods; no tool name or payload crosses from Worker.
3. Deduplicate by request ID, poll with a bounded backoff, and survive
   Companion reload.
4. Keep unfinished requests across same-session Context Epoch rotation and
   deliver results only to the current Worker identity.
5. Cancel unfinished work on session stop and fence stale sessions.
6. Send only a bounded public spoken summary via
   `HostCommand::AssistanceResult`.
7. Project safe state/error events and leave the Realtime Session alive when
   Core Assistance fails.

**Tests**

- duplicate events create one router job;
- Core unavailable pauses Assistance without stopping Realtime;
- online-disabled candidates fail before Core work;
- old session events and results are dropped;
- rotation delivers to the current epoch;
- stop cancels pending Core work;
- no credential, raw media, full answer, citation body, or tool payload enters
  the Worker protocol.

**Commit**

`feat(desktop): route realtime core assistance`

## Task 4: Expose bounded Assistance state in Companion

**Files**

- Modify `desktop/src/core/client.ts`
- Modify `desktop/src/realtime/realtimePresence.ts`
- Modify `desktop/src/realtime/RealtimeCompanion.tsx`
- Modify `desktop/src/realtime/realtime-companion.css`
- Modify Realtime Vitest fixtures/tests
- Modify Realtime Playwright fixtures/specs

**Implementation**

1. Add typed public Assistance projection state.
2. Show Searching, queued, approval, completed, failed, and cancelled without
   exposing full result or hidden state.
3. Add `Open main chat` and governed cancellation.
4. Keep approval controls out of the Companion.
5. Restore state after window close/reload and clear it on another session.
6. Preserve narrow-window, keyboard, focus, Reduced Motion, and no-overflow
   behavior.

**Tests**

- state and summary rendering;
- no full markdown/citation/tool content in the secondary window;
- open-main-chat navigation preserves Conversation;
- cancel is available only while nonterminal;
- remount recovery and session isolation;
- no approval control exists;
- screenshot/default/narrow accessibility checks.

**Commit**

`feat(desktop): present realtime assistance`

## Task 5: Certify Phase 6

**Files**

- Add `docs/acceptance/realtime-companion-beta-phase-6.md`
- Modify tests only for verified defects discovered by certification

**Verification**

1. Run Core focused tests, then the complete Core test suite and static checks.
2. Run focused Rust tests, then workspace tests and strict Clippy.
3. Run TypeScript, focused Vitest, complete Vitest, focused Playwright, and
   complete Playwright.
4. Exercise a deterministic fake-provider Assistance completion and approval
   flow; do not claim live web/provider/MCP quality without configuration.
5. Run one controlled Tauri dev WebView2 check: close/reopen Companion, open
   main chat, inspect approval separation, and verify ordinary startup leaves
   on-demand workers cold.
6. Stop every controlled process; confirm no Node, Cargo, Tauri, Core,
   Realtime, Omni, Voice, or Python worker remains.
7. Record passed evidence and explicitly deferred environment-dependent gates.

**Commit**

`test(realtime): certify core assistance`

## Completion

Phase 6 is complete only after all five commits are present, the acceptance
record matches observed evidence, the worktree contains no Phase 6 changes,
and no controlled process or transient test artifact remains.
