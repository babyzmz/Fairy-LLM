# Workspace Loop And Extensions Acceptance

## User-observable invariants

- A generated output is visible only while its owning Conversation and Task are active.
- A late event or query response from Conversation A never changes Conversation B's Outputs, Preview, trace, or busy state.
- Completed, failed, cancelled, and interrupted Turns never retain a spinner or an active stream projection.
- One Turn renders at most one final Assistant message. Voice and media jobs do not create Assistant messages.
- Preview keeps the last ready frame visible while an atomic Workspace generation is applied, then reloads only after the generation advances.
- Skills and MCP servers can be installed, reviewed, enabled, disabled, and removed without exposing arbitrary execution to Settings.
- Every visible control either performs its named action, opens a governed confirmation, or is disabled with a user-visible reason.

## State ownership

| State | Owner | Scope key | Terminal lifecycle | Restart behavior |
|---|---|---|---|---|
| Assistant Turn | Core Assistant ledger | `tenant_id + conversation_id + task_id + turn_id` | completed, failed, cancelled, interrupted | recover leased work or mark interrupted |
| Turn trace | Core TurnTrace ledger | `turn_id` | settled when Turn is terminal | replay by `turn_id + sequence` |
| Media job | Core media store | `tenant_id + conversation_id + task_id + turn_id + job_id` | completed, failed, cancelled, interrupted | recover active job or mark interrupted |
| Workspace output projection | TanStack Query | `conversation_id + task_id` | no local terminal state | refetch from durable jobs |
| Preview | Core runtime ledger | `task_id + workspace_id + version_id` | stopped or failed | resolve exact binding; never infer from Conversation |
| Model selection | device preferences | device revision | persistent preference | reload last committed revision |
| Pet windows | Rust PresenceCoordinator | native window labels and physical anchor | hidden or closed independently | restore placement and window policy |

## Risk matrix

| Trigger | Required acceptance |
|---|---|
| Conversation/Task scope | two-way A/B isolation, rapid switching, delayed A completion after B becomes active |
| Async Turn/job/trace | success, failure, cancellation, interruption, approval resume, no duplicate message |
| Persistence | close/reopen or reconstruct Core from the same data directory |
| Input and popovers | pointer hit, keyboard navigation, IME, narrow window, Reduced Motion |
| Native windows | real Tauri coordinates, focus, hit testing, independent lifecycle, 100/125/150% DPI |

## Automated scenarios

| Scenario | Observable result | Forbidden result | Environment | Evidence |
|---|---|---|---|---|
| Generate A1, switch to B | B has no A1 job, progress, or Preview | stale A card or spinner | React + real query cache | component/integration assertion |
| A response arrives after B | durable A cache updates only | B UI changes | React + delayed Promise | integration assertion |
| Reopen A | A1 returns from `media.jobs.list` | process-only state loss | Core SQLite service | repository/service test |
| Terminal historical Turn | settled rail or explicit error | infinite spinner/poll | React query + trace fixture | component/E2E assertion |
| Tool approval | one decision resumes the same Turn once | second `turns.start` or duplicate reply | Core scheduler + E2E | invocation count and message count |
| Media generation | progress then verified Workspace stream | partial file or host path | Core store + desktop | job revision and stream URL |
| Preview update | old ready iframe remains, generation change remounts it | blank/half-written frame | desktop component | iframe identity assertion |
| Extension lifecycle | review precedes enable; removal revokes tool | unreviewed model-visible tool | Core + desktop E2E | catalog and registry assertions |

## Native-only acceptance

The following cannot be proven by Vite or Playwright Chromium and must run in a real Tauri development process:

- Pet and input windows move as one group only while the liquid bridge interaction is active.
- Closing, hiding, minimizing, or moving one independent window does not implicitly operate the other.
- The visible liquid input shell and the actual textarea differ by no more than 2 px on every edge.
- Center and four-corner clicks focus the textarea; English, Chinese IME, Shift+Enter, and Enter work.
- Repeat placement and hit tests at Windows 100%, 125%, and 150% scaling.

Browser evidence must not be reported as native acceptance. If the native smoke cannot create both WebViews, report it as unverified with the exact failure.

## Test truthfulness

- OpenRouter and media providers may be scripted for deterministic protocol tests; the Core ledger, Command Bus, query keys, routing, and projection code remain real.
- Browser tests mock the Tauri command boundary and therefore prove desktop UI behavior, not HWND behavior.
- SQLite tests use a real temporary database and managed Workspace.
- Docker/PostgreSQL and native Tauri results are reported separately; skipped checks are never called passed.
