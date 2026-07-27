# Stability Audit Repairs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the seven confirmed Fairy V3 stability and usability gaps without changing the public product architecture or prewarming optional workers.

**Architecture:** Keep Core as the authority for durable conversation, transcript, event, and session state. Desktop owns bounded UI recovery, query invalidation, and accessibility. Every repair begins with a focused regression test, uses existing Unit of Work/Ledger/Query mechanisms, and lands as an independently reversible Conventional Commit.

**Tech Stack:** Python 3.13, SQLAlchemy, pytest, React 19, TypeScript, TanStack Query, Vitest, Playwright, Tauri 2, Rust.

## Global Constraints

- Work only in `D:\桌面\~\deskllmchat\fairy-v3`.
- Preserve the untracked repository-root `CLAUDE.md`; never stage or commit it.
- Do not run Docker, release builds, production image builds, or optional Voice/Game Companion warmups.
- Use existing Command Bus, Scope, Ledger, and Core Unit of Work boundaries.
- Never log or include transcript text in Ledger event payloads.
- Keep each task in a separate commit. If a focused test exposes an independent Critical or Important defect, repair and commit it separately.
- After each task, run the focused tests named below before continuing.
- At the end, stop every Node, Python, Cargo, Tauri, Vite, Core, browser, and Voice process started during verification.

---

## Task 1: Recover Failed Realtime Transcript Persistence

**Files:**

- Create: `desktop/src/realtime/useTranscriptPersistence.ts`
- Create: `desktop/src/realtime/useTranscriptPersistence.test.tsx`
- Modify: `desktop/src/realtime/RealtimeCompanion.tsx`
- Modify: `desktop/src/realtime/RealtimeCompanion.test.tsx`
- Modify: `desktop/src/styles/realtime.css`

**Interface:**

```ts
type TranscriptAppendRequest = {
  session_id: string;
  speaker: "user" | "assistant";
  text: string;
};

type TranscriptPersistenceState = {
  unsavedCount: number;
  retryUnsaved: () => void;
  enqueue: (request: TranscriptAppendRequest) => void;
  reset: (sessionId?: string) => void;
};
```

- [ ] Add hook tests proving a transient append failure retries with bounded backoff, succeeds without duplicate enqueue, and never exceeds three automatic attempts.
- [ ] Add hook tests proving exhausted entries remain counted, manual retry restarts the attempt budget, and a session reset cancels old timers and drops old-session work.
- [ ] Run `npx vitest run src/realtime/useTranscriptPersistence.test.tsx`; confirm the new tests fail because the hook does not exist.
- [ ] Implement a session-scoped FIFO queue in `useTranscriptPersistence.ts`. Keep transcript text only in memory; do not log rejected requests. Use a maximum of three automatic attempts and clear timers during reset/unmount.
- [ ] Replace the fire-and-forget `.catch(() => undefined)` in `RealtimeCompanion` with `enqueue`.
- [ ] Render a restrained `N captions unsaved` status and a `Retry saving` button only when exhausted work exists. Keep the realtime session running.
- [ ] Extend `RealtimeCompanion.test.tsx` to prove a failed append becomes visible, manual retry succeeds, and restarting/switching sessions cannot replay stale captions.
- [ ] Run `npx vitest run src/realtime/useTranscriptPersistence.test.tsx src/realtime/RealtimeCompanion.test.tsx`.
- [ ] Run `npx tsc --noEmit`.
- [ ] Commit:

```text
fix(desktop): recover realtime transcript persistence
```

---

## Task 2: Publish Transcript Appends and Invalidate Exact Queries

**Files:**

- Modify: `core/src/fairy_core/realtime/application.py`
- Modify: `core/tests/realtime/test_realtime_service.py`
- Modify: `desktop/src/app/workspaceQueryInvalidation.ts`
- Modify: `desktop/src/app/workspaceQueryInvalidation.test.ts`

**Core event contract:**

```json
{
  "event_type": "realtime.transcript.appended",
  "conversation_id": "<conversation id>",
  "payload": {
    "session_id": "<session id>",
    "conversation_id": "<conversation id>",
    "entry_id": "<entry id>",
    "sequence": 3
  }
}
```

- [ ] Add a Core test that appends a transcript and asserts the new user-visible Ledger event is committed with the transcript entry in the same operation.
- [ ] Assert the event payload contains identifiers and sequence only, never transcript text.
- [ ] Run `python -m pytest core/tests/realtime/test_realtime_service.py -q`; confirm the event assertion fails.
- [ ] Append the event through `unit_of_work.commands.append_domain_event` before the existing commit in `RealtimeApplication.append_transcript`.
- [ ] Add Desktop invalidation tests proving `realtime.transcript.appended` maps to a dedicated transcript domain and invalidates only `["workspace", "realtime-transcript", conversationId]` for the matching conversation.
- [ ] Assert unrelated messages, voice settings, other conversations, and other projects are not invalidated.
- [ ] Run `npx vitest run src/app/workspaceQueryInvalidation.test.ts`; confirm the focused test fails before implementation.
- [ ] Add the dedicated `realtimeTranscript` domain and exact query matcher before the broad `realtime.*` voice-domain routing.
- [ ] Run `python -m pytest core/tests/realtime/test_realtime_service.py -q`.
- [ ] Run `npx vitest run src/app/workspaceQueryInvalidation.test.ts`.
- [ ] Run `npx tsc --noEmit`.
- [ ] Commit:

```text
fix(realtime): publish transcript updates
```

---

## Task 3: Bound Preview Activation Recovery

**Files:**

- Modify: `desktop/src/app/usePreviewActivation.ts`
- Modify: `desktop/src/app/usePreviewActivation.test.tsx`

**Policy:**

- Initial request plus at most two automatic retries.
- Exponential bounded delays based on the existing retry interval.
- Terminal capability, permission, validation, not-found, and version-conflict errors do not retry.
- A preview identity change or explicit retry resets the budget.

- [ ] Add fake-timer tests proving a permanently transient failure results in exactly three activation attempts and no fourth attempt after arbitrarily advancing time.
- [ ] Add parameterized tests proving terminal Core error codes stop after the first attempt.
- [ ] Add tests proving identity changes and explicit retry reset the budget, while late results from the previous identity remain ignored.
- [ ] Run `npx vitest run src/app/usePreviewActivation.test.tsx`; confirm the new attempt-count assertions fail.
- [ ] Preserve structured Core error codes in the hook’s internal failure state instead of reducing the value to display text immediately.
- [ ] Add a retry counter and a pure `shouldRetryPreviewActivation` policy. Reset both error and retry budget on identity change and explicit retry.
- [ ] Stop scheduling when the budget is exhausted or the error is terminal; expose the existing human-readable error for the UI.
- [ ] Run `npx vitest run src/app/usePreviewActivation.test.tsx`.
- [ ] Run `npx tsc --noEmit`.
- [ ] Commit:

```text
fix(desktop): bound preview activation recovery
```

---

## Task 4: Stabilize Embedded Settings Readiness Under Parallel E2E Load

**Files:**

- Modify: `desktop/src/App.tsx`
- Modify: `desktop/src/settings/SettingsScreen.tsx`
- Modify: `desktop/e2e/support/settings.ts`
- Modify: `desktop/e2e/global-setup.ts`
- Modify: `desktop/e2e/execution-controls.spec.ts`
- Modify: `desktop/e2e/extensions.spec.ts`

**Readiness contract:**

```html
<main aria-label="Fairy settings" data-settings-state="ready">
```

- [ ] Update the shared settings helper tests/spec usage to wait for semantic Settings readiness instead of treating the Back button alone as loaded state.
- [ ] Reproduce with the two affected specs under parallel/cold conditions and record the failure before implementation:

```powershell
npx playwright test e2e/execution-controls.spec.ts e2e/extensions.spec.ts --workers=8
```

- [ ] Add a normal browser-idle preload for the lazy Settings module after the Workspace becomes usable. The preload must not fetch settings data or mount Settings.
- [ ] Add an explicit ready marker to the loaded Settings screen and make `openInternalSettings` wait for it with an operation-specific timeout.
- [ ] Have global setup issue a harmless module request only when necessary to ensure the Vite transform cache is warm; keep one controlled Vite lifecycle and do not create a second server.
- [ ] Remove duplicated ad hoc “Loading settings” waits from the two affected specs in favor of the shared helper.
- [ ] Run the two focused Playwright specs with eight workers until they pass from a cold Vite start.
- [ ] Run `npx tsc --noEmit`.
- [ ] Commit:

```text
test(desktop): stabilize embedded settings readiness
```

---

## Task 5: Clear Recovered Event Stream Errors Without Hiding Action Failures

**Files:**

- Modify: `desktop/src/core/eventStream.ts`
- Modify: `desktop/src/core/eventStream.test.ts`
- Modify: `desktop/src/app/workspaceModel.ts`
- Modify: `desktop/src/app/workspaceModel.test.tsx`

**Interface:**

```ts
type EventDeliveryOptions = {
  onEvent: (event: EventEnvelope) => void;
  onError?: (error: unknown) => void;
  onRecovered?: () => void;
};
```

- [ ] Add an event-stream test that forces a reconnect error followed by a successful state/list/subscribe recovery with no new event, then asserts `onRecovered` fires once.
- [ ] Assert normal startup does not emit a false recovery callback and repeated successful polls do not repeatedly call it.
- [ ] Run `npx vitest run src/core/eventStream.test.ts`; confirm the callback test fails.
- [ ] Track whether resilient delivery has reported an error. Call `onRecovered` after the first successful catch-up/subscription establishment, even if no event arrived.
- [ ] Add Workspace model tests proving stream failures appear, successful recovery clears only the stream error, and an unrelated action error remains visible.
- [ ] Split `eventStreamError` from `actionError` in `workspaceModel.ts`; give action errors display priority and clear each only through its own lifecycle.
- [ ] Run `npx vitest run src/core/eventStream.test.ts src/app/workspaceModel.test.tsx`.
- [ ] Run `npx tsc --noEmit`.
- [ ] Commit:

```text
fix(desktop): clear recovered event stream errors
```

---

## Task 6: Create Realtime Scratch Conversation and Session Atomically

**Files:**

- Modify: `core/src/fairy_core/application/core.py`
- Modify: `core/src/fairy_core/application/realtime_service.py`
- Modify: `core/src/fairy_core/application/service.py`
- Modify: `core/src/fairy_core/workspaces/application.py`
- Modify: `core/tests/realtime/test_realtime_service.py`

**Coordinator contract:**

```py
def start_with_scratch_conversation(
    self,
    request: RealtimeStartRequest,
) -> RealtimeSession:
    """Create scratch conversation/workspace/version and session in one UoW."""
```

- [ ] Add a fault-injection test that raises while saving the realtime session and asserts no scratch Conversation, Workspace, Version, or session remains durable.
- [ ] Assert any initial workspace directory created before the injected failure is removed.
- [ ] Add the symmetric failure test for initial workspace filesystem creation and assert no database rows are committed.
- [ ] Run `python -m pytest core/tests/realtime/test_realtime_service.py -q`; confirm the orphan assertion fails against the current two-transaction flow.
- [ ] Extract a Core helper that populates a caller-owned Unit of Work with a scratch Conversation, initial Version, and Workspace without committing.
- [ ] Add a narrow workspace cleanup/compensation function for a newly created initial workspace path. Resolve and validate that the path is inside the managed workspace root before removal.
- [ ] Replace the `conversation_factory` callback with a coordinator callback that creates scratch state and starts the realtime session within one Unit of Work and one commit.
- [ ] On failure, roll back the Unit of Work and compensate only the newly created filesystem workspace. Never remove an existing workspace.
- [ ] Keep explicitly supplied conversation IDs on the existing start path.
- [ ] Run `python -m pytest core/tests/realtime/test_realtime_service.py -q`.
- [ ] Run the focused Core application/workspace tests touching scratch creation.
- [ ] Run `python -m ruff check core/src core/tests`.
- [ ] Commit:

```text
fix(core): create realtime scratch sessions atomically
```

---

## Task 7: Complete Workspace Inspector ARIA and Keyboard Semantics

**Files:**

- Modify: `desktop/src/app/WorkspaceInspector.tsx`
- Modify: `desktop/src/app/WorkspaceInspector.test.tsx`
- Modify: `desktop/src/styles/workspace-inspector.css`
- Modify: `desktop/e2e/workspace-inspector.spec.ts`

**Semantics:**

- The resize separator exposes `aria-valuemin`, `aria-valuemax`, and current `aria-valuenow`.
- Arrow keys adjust by the normal step; Home/End select the valid min/max.
- Tabs and tabpanels are linked with stable IDs, roving `tabIndex`, and Left/Right/Home/End navigation.

- [ ] Add component tests for separator values, clamping, Arrow/Home/End resize behavior, and live `aria-valuenow`.
- [ ] Add component tests for one active tab stop, `aria-controls`/`aria-labelledby`, wraparound arrow navigation, and Home/End.
- [ ] Run `npx vitest run src/app/WorkspaceInspector.test.tsx`; confirm the new ARIA and keyboard assertions fail.
- [ ] Define shared minimum/maximum/step values from the actual inspector width constraints and apply them consistently to pointer, keyboard, CSS, and ARIA state.
- [ ] Implement roving focus and linked tabpanel semantics without changing the existing active-preview authority or automatic/manual tab selection rules.
- [ ] Add or update the focused Playwright test to cover keyboard-only tab and separator behavior at a constrained window size.
- [ ] Run `npx vitest run src/app/WorkspaceInspector.test.tsx`.
- [ ] Run `npx playwright test e2e/workspace-inspector.spec.ts --workers=1`.
- [ ] Run `npx tsc --noEmit`.
- [ ] Commit:

```text
fix(desktop): complete inspector accessibility
```

---

## Task 8: Full Regression and Native WebView2 Smoke

**Files:**

- Modify only if a gate finds a confirmed regression; commit any independent repair separately.

- [ ] Verify the worktree contains only intended committed changes and the preserved untracked `../CLAUDE.md`.
- [ ] Run Desktop type checking:

```powershell
npx tsc --noEmit
```

- [ ] Run the full Desktop component suite:

```powershell
npx vitest run
```

- [ ] Run the full Core suite:

```powershell
python -m pytest core/tests -q
```

- [ ] Run Rust formatting, lint, and workspace tests:

```powershell
cargo fmt --all -- --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
```

- [ ] Run the complete Playwright suite through its single controlled Vite lifecycle:

```powershell
npx playwright test
```

- [ ] When the user is not interacting with the native window, run one Tauri dev WebView2 smoke. Verify embedded Settings cold-open/return, transcript failure status, bounded Preview retry, Inspector keyboard behavior, and event-stream recovery display.
- [ ] Confirm only one user-visible Fairy main window exists and no independent Settings WebView is created.
- [ ] Stop all processes created by the smoke and verify no Fairy Node, Vite, Core, Tauri, Cargo, Python Voice, Realtime, browser-worker, or Game Companion process remains.
- [ ] Run `git status --short --branch` and record the final commit list.
