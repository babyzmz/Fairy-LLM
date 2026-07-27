# Stability Audit Repairs Design

## Status and scope

This design covers the seven follow-up repairs approved after the July 27 Fairy V3
stability and usability audit. Each repair is independently testable, reversible,
and committed before the next repair begins.

The work stays inside the existing Core unit-of-work, canonical Ledger, scoped
React Query, and Tauri surface boundaries. It does not add a new persistence
service, public extension API, database technology, window, or background worker.
It does not change the Line Sidebar visual direction, Liquid Glass renderer,
provider routing, or Legacy Qt removal.

## Repair order

The repairs are implemented in this order:

1. visible and bounded Realtime transcript persistence recovery;
2. canonical transcript Ledger events and scoped desktop invalidation;
3. bounded Preview activation recovery;
4. deterministic embedded Settings E2E readiness;
5. Event Stream recovery-state clearing;
6. atomic Realtime scratch Conversation and Session creation;
7. Workspace Inspector resize and tab accessibility.

Every step receives focused regression tests and one Conventional Commit. A later
step may depend on a prior contract, but it may not silently broaden the prior
step's behavior.

## Realtime transcript persistence recovery

Stable captions remain visible immediately and never interrupt an active Realtime
session. Persistence is no longer fire-and-forget.

The Companion owns a bounded in-memory collection of transcript writes for the
current session. Each item has a stable local identity, speaker, normalized text,
attempt count, and one of `saving`, `saved`, or `failed`. The queue:

- accepts only stable, non-empty captions;
- attempts each write at most three times;
- uses short bounded backoff between automatic attempts;
- never logs or exposes transcript text through diagnostics;
- drops saved items after their successful result is reflected in UI state;
- resets when the session identity changes; and
- retains failed items until the user retries them or closes the Companion.

The Companion displays a concise status when at least one caption remains unsaved.
The status exposes the unsaved count and a `Retry saving` action. Manual retry
starts one new bounded attempt cycle for failed items; it does not create an
unbounded loop. Session stop and worker failure do not wait indefinitely for
pending transcript writes.

Tests cover immediate success, transient recovery, final failure, manual retry,
session reset, duplicate stable events, and the absence of transcript text from
error surfaces.

## Canonical transcript events and scoped invalidation

`RealtimeApplication.append_transcript` appends a
`realtime.transcript.appended` user-visible Ledger event in the same database
transaction as the transcript entry. Its payload contains only:

- `session_id`;
- `conversation_id`;
- `entry_id`; and
- transcript `sequence`.

The event does not contain caption text. Its envelope is scoped to the linked
Conversation and has no Project or Task scope unless those identities already
belong to the session.

Desktop invalidation maps `realtime.transcript.appended` to a dedicated transcript
domain. Only the matching
`["workspace", "realtime-transcript", conversation_id]` query is invalidated.
Other `realtime.*` and `voice.*` events continue to invalidate Voice settings
without refreshing messages, providers, previews, or unrelated Conversations.

Core tests prove atomic event creation and rollback. Desktop tests prove matching,
non-matching, and empty-scope behavior across two Conversations.

## Bounded Preview activation recovery

Preview activation retains automatic recovery for genuinely transient first-start
failures, but every activation identity has a finite retry budget.

The hook records attempts for the current Task, Workspace, Version, and Workspace
revision identity. It:

- performs the initial activation plus at most two automatic retries;
- uses bounded exponential delays;
- resets the budget only when the activation identity changes or the user invokes
  an explicit retry;
- stops immediately for version conflicts, validation errors, permission errors,
  and capability-unavailable errors;
- continues the existing bounded polling for `starting`, `waiting_for_slot`, and
  healthy `ready` results; and
- does not schedule error recovery when the component is disabled or unmounted.

The existing public hook result remains compatible. It gains an explicit retry
operation only if the current Preview error surface needs one; no automatic
Workspace-wide retry is introduced.

Tests use fake timers to prove recovery, terminal errors, retry exhaustion,
identity reset, explicit retry, unmount cleanup, and late-result isolation.

## Deterministic embedded Settings readiness

The E2E repair preserves the eight-worker functional project. It does not globally
increase Playwright timeouts or serialize all tests.

The application exposes an internal, semantic Settings-ready state after the lazy
module mounts and the selected category shell is available. The state is expressed
through existing accessible UI rather than a test-only production branch.

Playwright global setup prewarms the application entry and the Settings lazy chunk
within the same controlled Vite lifecycle. `openInternalSettings` waits for:

1. the Settings navigation request to be accepted;
2. the `Back to workspace` control and category navigation to mount; and
3. the loading status to leave the accessibility tree.

The helper uses an operation-specific readiness timeout instead of the default
five-second assertion budget. The full functional and performance projects remain
ordered through the existing project dependency.

Verification reproduces the previous concurrent cold start, runs the two affected
specs repeatedly with eight workers, and then runs the full Playwright suite.

## Event Stream recovery state

Event delivery distinguishes stream health from user action failures.

Workspace state stores a dedicated Event Stream error instead of writing transport
failures into the general action-error slot. `runResilientEventDelivery` reports a
successful recovery after it has re-established state/list delivery, even if no
new user-visible event is immediately available.

The recovery callback clears only the Event Stream error. It never clears a
permission conflict, failed user action, Preview error, or another domain error.
Repeated failures update the stream error without creating multiple notices.

Tests cover error, reconnect without a new event, reconnect with replay, repeated
failure, abort, and independence from action errors.

## Atomic Realtime scratch startup

Starting Realtime without a supplied Conversation becomes one coordinated Core
operation.

The coordinator checks the idempotency key first. If no existing session is found,
it creates the scratch Conversation, its initial Workspace/Version state, and the
Realtime Session through one Core unit of work. The session references the new
Conversation before the transaction commits.

Workspace filesystem initialization is the only non-database side effect. If it
fails, the database transaction rolls back. If a later database step or commit
fails, the coordinator removes only the newly created managed Workspace version
through the existing workspace provisioner cleanup boundary. It never purges an
existing Conversation or a replayed session.

Concurrent calls with the same idempotency key return the persisted session and
its Conversation. A conflicting request with the same key preserves the existing
idempotency-conflict behavior and leaves no second Conversation.

Tests cover success, replay, concurrent duplicate start, database failure,
workspace initialization failure, and conflicting reuse.

## Workspace Inspector accessibility

The resize handle keeps its current pointer behavior and keyboard increments. It
adds `aria-valuemin`, `aria-valuemax`, and `aria-valuenow` derived from the same
width bounds and current persisted width used by layout.

The Workspace view implements the ARIA Tabs pattern:

- every tab has a stable ID and `aria-controls`;
- the selected tab uses `tabIndex=0`, while unselected tabs use `tabIndex=-1`;
- Left/Right, Home, and End move selection and focus;
- the visible panel has `role="tabpanel"` and `aria-labelledby`; and
- hidden panels remain unmounted as they are today.

Pointer selection and narrow responsive layout remain unchanged. Tests cover
separator values, keyboard resizing, roving focus, tab-panel relationships, and
Conversation changes.

## Verification and completion

Each repair runs its focused TypeScript, Python, or Rust tests before commit.
Completion then requires:

- `npx tsc --noEmit`;
- complete Desktop Vitest;
- Core, Capabilities, and non-integration Cloud pytest;
- repository boundary and Ruff checks;
- Browser Worker smoke;
- Rust format, clippy, and workspace tests;
- complete Playwright with the functional project passing before performance; and
- one native Tauri WebView2 smoke for embedded Settings, chat restoration, Preview,
  and Inspector keyboard behavior.

Docker, release packaging, production image builds, live provider calls, and
claims of real multi-monitor DDA validation remain out of scope. All Node, Python,
Cargo, Vite, Core, Tauri, Voice, Realtime, and Browser Worker processes created by
verification are stopped before completion.
