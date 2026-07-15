# Chat State and Native Window Regressions

## Scope and completion rule

This acceptance specification covers three reported regressions:

1. Preview files or generated artifacts appear in another conversation.
2. Historical work chains remain active forever or inherit another conversation's
   active state.
3. Pet render and pet chat/input windows remain coupled when the liquid bridge is
   disabled, and the visible input shell does not match the real textarea hit target.

The work is complete only when all three user journeys pass in their required
environment. Unit-test counts and browser-only presence tests are supporting evidence,
not completion evidence.

## Current coverage gap

- `desktop/e2e/support/coreFixture.ts` defines one scratch conversation, one scratch
  task, and one completed scratch turn. It cannot prove conversation isolation, stale
  response handling, or concurrent jobs.
- Existing chat E2E tests exercise send, streaming, approval, and model selection in
  that single scratch conversation. They do not switch between two populated chats.
- Existing `ActivityRail` tests cover a normal completed trace and a running trace,
  but not terminal turns containing stale active steps, missing traces, failed trace
  requests, or an active turn in another conversation.
- Existing presence Playwright tests emulate host calls in WebView/Chromium. They can
  verify DOM layout but cannot prove HWND position, lifetime, native hit testing,
  focus, or DPI behavior.
- `scripts/test-presence-native.ps1` verifies transparent pass-through, hover reveal,
  topmost styles, and tray survival. It currently does not verify bridge-off movement
  independence, input-shell/textarea geometry, corner hit targets, IME, or multiple
  DPI settings.

## Regression 1: conversation-scoped preview and artifacts

### Observable invariants

- Every preview, file, asset set, generated artifact, loading state, and error shown by
  `WorkspaceInspector` belongs to the active conversation.
- A late result from conversation A is stored for A and never projected into active
  conversation B.
- Switching A -> B -> A never flashes B's content in A or A's content in B.
- Restart restores each conversation's own inspector state.

Conceptually:

```text
visible_items.every(item => item.conversation_id == active_conversation_id)
active_request.scope_key == [conversation_id, task_id, workspace_id, version_id]
```

### State ownership

| State | Owner | Scope key | Lifetime | Late completion rule |
| --- | --- | --- | --- | --- |
| Active chat task | Conversation projection | `conversation_id + task_id` | Conversation session, restored from durable tasks | Update the task's conversation only |
| Preview context | Core preview aggregate and query cache | `conversation_id + task_id + workspace_id + version_id` | Runtime/preview lifecycle | Cache under event/request scope, never active UI scope |
| Workspace files | Workspace version | `conversation_id + workspace_id + version_id` at UI boundary | Immutable version | Store by requested scope key |
| Asset sets/artifacts | Artifact aggregate | `conversation_id + turn_id + artifact_id` | Durable | Never infer conversation from current selection |
| Optimistic/stream state | Assistant turn controller | `conversation_id + turn_id` | One turn | Ignore or retain under origin scope after navigation |

### Likely roots to verify

- `useAssistantTurn` owns a single `turn`, `isBusy`, and pending draft but does not
  currently fence that state by `conversationId` when selection changes.
- `workspaceModel` owns one `chatTaskId`; inspector queries do not include
  `conversation_id` in their cache keys or returned UI context.
- Broad event-driven invalidation refetches every inspector query, increasing the
  chance that a stale request or unscoped projection is rendered.
- Project workspaces intentionally span conversations, so UI checks cannot assume
  `workspace_id` alone proves conversation ownership.

### Acceptance scenarios

| Scenario | User action | Required result | Forbidden result | Environment | Evidence | Timeout |
| --- | --- | --- | --- | --- | --- | --- |
| Basic isolation | Generate A1 in A, open B | B has no A1 | A1 or A loading in B | Browser E2E with production store/query layer | DOM assertions and RPC call log | 500 ms after B data settles |
| Bidirectional | Generate B1, switch A/B | A only A1; B only B1 | Mixed file tree, preview, asset count | Browser E2E | Assertions after each switch | 500 ms |
| In-flight switch | Delay A completion, switch B | B remains clean | A spinner/artifact in B | Browser E2E with delayed event bus | Event and DOM assertions | Entire delay window |
| Late event | Deliver A completion after B is active | A cache updates; B does not | Active inspector update | Integration/E2E | RPC scope log plus DOM | 500 ms |
| Rapid switching | A -> B -> A while requests resolve out of order | Final UI is A only | Stale B response overwrites A | Integration/E2E | Controlled promises | 500 ms |
| Restart | Persist A1/B1, close/reopen | Both restore independently | Last-selected global artifact leaks | Real Core plus Tauri or restartable integration | Before/after evidence | 5 s after ready |
| Delete isolation | Delete A1 | B1 remains | B mutation | Integration/E2E | File lists | 1 s |
| Parallel generation | Run A and B jobs | Each status/result stays scoped | Shared loading/result | Integration/E2E | Per-conversation projections | Job timeout |

### Mock boundary

Network/provider execution may be deterministic, but the test must use the real
React Query keys, workspace model, router/selection action, event subscription
projection, and at least two conversations/tasks/workspaces. A one-chat object stub is
not sufficient. Restart acceptance must use durable Core state rather than an in-memory
fixture.

## Regression 2: historical work chain never terminates

### Observable invariants

- A terminal turn (`completed`, `failed`, `cancelled`, or recovered `interrupted`)
  never shows an active spinner.
- Trace absence, 404, empty data, and request failure resolve to explicit empty,
  compatibility, interrupted, or retryable error states, never infinite loading.
- Active turn A cannot make historical conversation B appear active.
- Once terminal, the UI stops timers, polling, and turn-specific subscriptions.

### State ownership

| State | Owner | Scope key | Lifetime | Late completion rule |
| --- | --- | --- | --- | --- |
| Trace query lifecycle | Query layer | `conversation_id + turn_id` | Request | Old query populates its own key only |
| Turn execution status | Durable AssistantTurn | `conversation_id + turn_id` | Durable | Terminal status dominates stale active presentation |
| Work-chain projection | Pure projection | `turn_id` | Render | Reject events from any other turn/run |
| Loading/error/empty | Trace view state | `turn_id + request attempt` | Request | Settle deterministically after result/error |
| Duration clock | ActivityRail instance | `turn_id` | Nonterminal render only | Stop immediately on terminal state |

### Likely roots to verify

- `projectWorkChain` currently includes any event with a non-null `run_id`, even when
  its payload does not belong to the projected Turn.
- A terminal Turn is considered nonterminal when any stale trace step remains
  `pending`, `running`, or `waiting`; the spinner can therefore outlive durable Turn
  completion.
- Historical trace query errors are discarded by `useTurnTraces`, which exposes only
  a map of successful data and cannot distinguish loading, missing, and error.
- `useAssistantTurn` is not reset/fenced on conversation changes, so A's current Turn
  and busy state can remain mounted while B's messages render.

### Acceptance scenarios

| Scenario | Required result | Forbidden result | Environment | Evidence | Timeout |
| --- | --- | --- | --- | --- | --- |
| Completed/failed/cancelled/interrupted history | Correct terminal icon and label | Spinner | Component + browser E2E | No `.activity-spinner` | 500 ms after load |
| Missing trace / empty response | Explicit compatibility or empty state | Endless loading | Query integration | View-state assertion | 500 ms |
| Trace 404 or request failure | Retryable error/compatibility state | Endless loading/retry loop | Query integration | Error and request count | 5 s maximum |
| A active, B historical | B uses only B Turn/Trace | A spinner/status in B | Two-conversation E2E | DOM plus turn IDs | 500 ms |
| Delayed A response after B | B remains selected and terminal | A overwrites B | Query integration/E2E | Controlled response order | 500 ms |
| Terminal polling | Request count stops | Continued polling/timer | Query integration | Stable call count | 1 s |
| Crash/restart | Recover or mark interrupted | Permanent running | Real Core restart | Durable status and UI | 5 s after ready |

### Mock boundary

Provider output may be mocked. The tests must use real `useTurnTraces`, query cache,
work-chain projection, and MessageList binding with two conversation scopes. Core
restart verification must use a persisted repository. Timers may be fake only in the
component-level stop-clock test.

## Regression 3: native pet/input independence and hit geometry

### Observable invariants

- With the liquid bridge disabled, moving, hiding, minimizing, or closing pet-render
  does not move or change pet-input, and the reverse is also true unless an explicit
  product action requests both.
- With the bridge enabled, only the documented transition animation may temporarily
  coordinate geometry; the bridge is not a permanent layout dependency.
- The visible input shell is the real textarea container. Shell and textarea hit
  bounds differ by no more than 2 physical pixels on every edge.
- Clicking center and four inset corners focuses the textarea, after which English,
  Chinese IME, Shift+Enter, and Enter submit work.

### State ownership

| State | Owner | Scope key | Lifetime | Rule |
| --- | --- | --- | --- | --- |
| Render HWND placement/lifecycle | Native render-window controller | `pet-render HWND + monitor + DPI` | Application | Independent when bridge off |
| Input HWND placement/lifecycle | Native input-window controller | `pet-input HWND + monitor + DPI` | Visible interaction | Independent when bridge off |
| Bridge transition | Presence interaction state | transition/session id | Animation only | May coordinate only during specified transition |
| Visual/input geometry | `pet-input` DOM surface | input window + scale | Layout | One container owns optics and hit target |
| Drag session | Window being dragged | HWND + pointer id | Pointer gesture | Must not imply group movement when bridge off |

### Confirmed implementation mismatch to reproduce

- `pet_window_group_move` unconditionally calls `move_pet_window_group`; there is no
  bridge-enabled argument or preference in this path.
- `move_pet_window_group` repositions both visible windows.
- Liquid optics are drawn in `pet-render`, while the textarea lives in `pet-input`;
  existing native tests do not compare their geometry or hit targets.

### Native acceptance scenarios

| Scenario | Required result | Forbidden result | Environment | Evidence |
| --- | --- | --- | --- | --- |
| Bridge-off drag render | Input HWND coordinates unchanged | Group follow | Real Tauri/Win32 | Before/after HWND rectangles |
| Bridge-off drag input | Render HWND coordinates unchanged | Group follow | Real Tauri/Win32 | Before/after HWND rectangles |
| Hide/minimize/close each | Other window unchanged | Coupled lifecycle | Real Tauri/Win32 | Visibility/window-state log |
| Bridge-on transition | Only specified temporary coordination | Permanent bridge/coupling | Real Tauri | Timed coordinate samples |
| Geometry | Every edge within 2 px | Separate visual shell | Real WebView2 in Tauri | DOM rects converted with DPI |
| Hit target | Center and four inset corners focus textarea | Drag layer/intercept | Real Tauri/Win32 + CDP | Focus assertion per click |
| Input behavior | English, Chinese IME, newline, send | Lost composition/double send | Real Tauri | Submitted values/event log |
| DPI | Repeat at 100%, 125%, 150% | Drift/jump | Real Windows display scaling | Rect and hit reports |
| Multi-monitor | No unintended snap/jump | Cross-monitor coupling | Real Windows | HWND coordinates and monitor IDs |

Browser Playwright may exercise DOM semantics and layout calculations, but cannot be
reported as native acceptance. If automated DPI switching or Chinese IME injection is
not reliable, use `scripts/manual-presence-window-acceptance.ps1` to create a timestamped
manual checklist and preserve screenshots/logs.

## Planned regression tests

- `desktop/e2e/chat-scope-isolation.spec.ts`
  - `keeps delayed preview and artifacts in their originating conversation`
  - `restores independent inspectors after application reload`
  - `does not project an active turn into historical conversation`
- `desktop/src/chat/useTurnTraces.test.tsx`
  - `keeps stale trace responses under the requested turn key`
  - `settles missing and failed trace queries without indefinite loading`
- `desktop/src/chat/workChainProjection.test.ts`
  - `terminal turn overrides stale active trace steps`
  - `rejects command events from another turn`
  - `projects interrupted recovery as terminal`
- `desktop/src/chat/ActivityRail.component.test.tsx`
  - `never animates terminal historical turns`
- `desktop/src/app/workspaceModel.scope.test.tsx`
  - `fences turn, task, preview, file, asset, and pending state by conversation`
- `desktop/src/presence/input/PresencePanel.test.tsx`
  - `uses one visual input container and focuses textarea at all inset points`
- `desktop/src-tauri/tests/presence_window_independence.rs`
  - pure policy tests for bridge-off and bridge-transition movement/lifecycle decisions
- `scripts/test-presence-native.ps1`
  - extend with bridge-off HWND independence, DOM geometry, corner hit targets, and
    current-DPI reporting
- `scripts/manual-presence-window-acceptance.ps1`
  - manual IME and 100/125/150 percent matrix when host automation cannot change DPI

## Pre-fix evidence log

Recorded on 2026-07-15 before production changes.

### Scoped artifacts and active Turn

Command:

```powershell
cd desktop
npm test -- --run src/chat/useAssistantTurn.test.tsx `
  src/chat/workChainProjection.test.ts `
  src/app/WorkspaceFilesPanel.scope.test.tsx
```

Observed deterministic failures:

- `WorkspaceFilesPanel.scope.test.tsx:33`: after changing from version A to version
  B with the same `generated/result.txt` path, the viewer still contained
  `Conversation A artifact`.
- `useAssistantTurn.test.tsx:80`: after changing the selected conversation from A
  (`...0010`) to B (`...0011`), the hook still exposed A's running Turn (`...0030`)
  and busy state.
- `workChainProjection.test.ts:161`: a completed durable Turn with a stale running
  Trace step projected `terminal=false`, which keeps the ActivityRail spinner active.

Result: 3 expected failures, 16 supporting tests passed.

### Native pet/input coupling and geometry

Command, using an isolated Fairy data directory created by the script:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts/test-presence-native.ps1 `
  -Executable desktop/src-tauri/target/debug/fairy.exe `
  -ExpectedMode liquid `
  -VerifyRegressions
```

Environment: real Windows 11 Tauri debug runtime, WebView2 CDP, liquid renderer, and
the current monitor scale reported by the app. No browser fixture was used for HWND
coordinates.

Observed deterministic failure:

```text
BRIDGE_OFF_WINDOW_COUPLING
render_delta=[-512,0]
input_delta=[-542,0]
shell/textarea edge delta:
left=43.56, top=12.87, right=51.48, bottom=12.87 CSS px
```

The script also intermittently observed `pet-input` remaining visible in the passive
state, and the pre-existing native test previously failed with
`pet-input did not return to its hidden passive state`. These are lifecycle evidence,
not accepted behavior.

## Post-fix evidence log

Populate only after the corresponding pre-fix regression fails for the expected root
cause. Record targeted tests first, then browser E2E, real Tauri evidence, affected
desktop tests, and one final required regression.

## Final report template

- Acceptance invariants:
- Pre-fix reproduction evidence:
- Post-fix evidence:
- Automated tests:
- Native environment tests:
- Existing tests changed and why:
- Skipped or unverified items:
- Residual risks:
