# Realtime Companion Beta Completion Acceptance

Date: 2026-07-30

## Status

Implementation slices 1–4 and their written regression coverage are complete
in commits:

- `e9ef1786 feat(realtime): add bounded audio processing`
- `42d918e3 feat(realtime): govern capture scope and recovery`
- `751e8e0e feat(realtime): enforce standby and cloud budgets`
- `8a0ed020 feat(desktop): complete realtime companion controls`

The deterministic automated gates and a guarded native WebView2 development
session are complete. Physical audio, eligible-GPU Local inference, live
provider, real four-hour wall-clock soak, installer, signing, release
composition, Docker/PostgreSQL/S3, and production-build validation remain
**not run**. Therefore this record does not claim release readiness.

This record separates code-level completion from the real Windows audio, GPU,
provider, WebView2, installer, and long-duration evidence that must be run with
the user present.

## Observable behavior

- Audible Realtime modes process microphone audio through bounded AEC, noise
  suppression, gain control, and local VAD using only a transient Fairy
  process-tree render reference.
- Local VAD interrupts Fairy playback without waiting for provider VAD.
- Microphone, selected window, Fairy render reference, and selected-application
  audio fail and recover independently.
- A closed target window preserves microphone conversation and requests a new
  source.
- Local Follow Foreground changes sources only through a Tauri-owned,
  acknowledged Window Epoch transition.
- Sensitive, excluded, locked, DND, or secure-desktop state stops visual and
  application-audio observation without persisting the matched metadata.
- Local standby observes the configured keep-warm duration and unloads its
  Backend Segment once.
- Cloud Segment start and wake fail closed when authoritative daily wall time
  is unavailable or reaches the configured limit.
- Cloud never offers selected-application audio when its provider transport
  cannot preserve a distinct channel.
- The UI uses general Realtime terminology and persists the actual
  application-audio consent.

## State ownership and scope keys

| State | Owner | Scope |
| --- | --- | --- |
| Presence lifecycle, effective capture source, privacy, standby | Tauri Coordinator | Session, Segment, Epoch |
| Audio processing and media-channel health | Realtime Worker | Session, Segment, Epoch, channel sequence |
| Local inference lifecycle and loaded model | Omni Sidecar under Tauri/Worker supervision | Backend Segment |
| Cloud daily usage | Core repository, queried through Tauri local-day bounds | device-local day, Cloud Backend |
| Capture preferences and exclusions | device-local Desktop Preferences | device |
| Full Assistance answer and approval | Fairy Core | Conversation, Task, Turn |
| UI rendering and user requests | React Companion | current authoritative projection only |

## Invariants

- No raw microphone, render reference, selected-application audio, or frame is
  written to disk, logs, Core, Ledger, diagnostics, or telemetry.
- The AEC reference contains Fairy process-tree output only and never becomes a
  model input.
- Application audio never enters microphone bytes.
- Local and Cloud cannot switch without explicit user approval.
- Cloud cannot follow the foreground.
- A sensitive-window match exposes no raw title or executable path.
- Old source, media, channel, usage, and recovery events are fenced by active
  identity and sequence.
- All queues and history are bounded and zeroized on teardown.
- A channel failure cannot terminate healthy independent channels.
- Core or usage-query failure cannot silently bypass Cloud cost policy.
- Companion reload cannot take session ownership from Tauri.

## Deterministic automation

Implementation adds focused tests for:

- synthetic delayed echo attenuation and near-end speech preservation;
- NS/AGC limits, VAD onset/offset, duplicate Barge-in suppression, reset, queue
  caps, and zeroization;
- Fairy-only reference scope and invalid host PID rejection;
- microphone, window, application-audio, and render-reference failure
  isolation, retry, cancellation, timeout, and late-event fencing;
- sensitive classifier categories, excluded-app validation, title/path
  non-disclosure, DND/lock projection, and fast foreground switching;
- two Sessions with distinct sources, exclusions, failures, and late events;
- standby at zero and nonzero keep-warm, one unload, wake readiness failure,
  and no Cloud fallback;
- Cloud usage across more than 50 sessions, midnight overlap, active sessions,
  Local exclusion, query failure, start/wake fencing, and exact limit;
- preference migration and invalid exclusion lists;
- truthful consent, general terminology, Cloud channel capability, keyboard
  actions, constrained layout, Reduced Motion, reload, and source replacement.

These tests were executed in the 2026-07-30 joint verification session. The
focused and complete deterministic results are recorded below.

## Implementation evidence

| Area | Primary implementation |
| --- | --- |
| Bounded AEC/NS/AGC/VAD and Barge-in | `desktop/src-tauri/crates/realtime-worker/src/audio_processing.rs`, `runtime_session.rs`, `media.rs`, `protocol.rs` |
| Fairy process-tree render reference and independent channel recovery | `desktop/src-tauri/crates/realtime-worker/src/runtime.rs`, `runtime_session.rs`, `desktop/src-tauri/src/realtime_worker.rs` |
| Capture privacy, Follow Foreground, source epochs, DND/lock handling | `desktop/src-tauri/src/realtime_privacy.rs`, `desktop/src-tauri/src/realtime_worker.rs`, Realtime Worker runtime |
| Standby and one-shot Local unload | `desktop/src-tauri/src/realtime_coordinator.rs`, `desktop/src-tauri/src/realtime_worker.rs`, Worker protocol/runtime |
| Authoritative Cloud daily wall-time gate | `core/src/fairy_core/realtime/repository.py`, `application.py`, `desktop/src-tauri/src/realtime_daily_budget.rs`, `desktop/src-tauri/src/lib.rs` |
| Preference schema 10 and exclusion normalization | `desktop/src-tauri/src/desktop_preferences.rs`, `desktop/src/settings/client.ts` |
| Companion projections, consent, retry/replace, and constrained UI | `desktop/src/realtime/RealtimeCompanion.tsx`, `RealtimeMediaStatus.tsx`, `realtimeCompanionModel.ts`, `realtime-companion.css`, Core/Tauri transport types |
| Settings controls and current readiness copy | `desktop/src/settings/SettingsApp.tsx`, `settingsControls.tsx`, `RealtimeReadinessCard.tsx` |

The generated RPC registry and TypeScript method contract were refreshed for
`realtime.sessions.cloud-usage` and are committed with Slice 3.

## Written regression inventory

Existing standalone test files changed by this completion cycle:

- `core/tests/realtime/test_realtime_service.py`: Cloud wall-time aggregation,
  more-than-50 pagination, Local exclusion, midnight clipping, and active
  Session coverage.
- `core/tests/test_core_service.py`: generated/public Core method exposure.
- `desktop/src/realtime/RealtimeCompanion.test.tsx`: truthful Local/Cloud
  application-audio consent, capture preferences, native channel/source
  recovery, Segment/Epoch late-event fencing, current terminology, and safe
  error messages.
- `desktop/src/settings/SettingsApp.test.tsx`: schema-10 controls and
  composition-safe normalized exclusions.
- `desktop/e2e/realtime-readiness.spec.ts`: 880×680 and 640×700 containment,
  fixed Cloud scope, Local scope, keyboard retry/replacement, Reduced Motion,
  screenshots, and no horizontal overflow.
- `desktop/src/app/App.test.tsx`,
  `desktop/src/realtime/RealtimeCompanionWindowApp.test.tsx`,
  `desktop/src/presence/DualSurface.test.tsx`,
  `desktop/src/presence/PresenceApp.test.tsx`, and
  `desktop/src/presence/transport/renderSettings.test.ts`: current
  DesktopPreferences schema and new field compatibility across main,
  companion, dual-surface, and render fixtures.

Deterministic Rust tests are colocated with their implementation:

- `audio_processing.rs`: delayed echo, near-end speech, gain/noise limits,
  VAD timing, duplicate suppression, bounded queues, reset, and zeroization.
- `runtime.rs`: scoped reference capture, channel failure isolation,
  retry/cancellation, teardown, and protocol projection.
- `realtime_privacy.rs`: sensitive categories, executable-basename
  normalization, exclusions, and non-disclosure.
- `realtime_worker.rs`: capture epochs, source replacement, late events,
  recovery, reload projections, unload, and wake boundaries.
- `realtime_coordinator.rs`: zero/nonzero keep-warm, one unload, late ticks,
  and new Segment wake.
- `realtime_daily_budget.rs`: local-day bounds, exact limit, unavailable
  usage, and concurrent reservation behavior.
- `desktop_preferences.rs`: schema-9 preservation, privacy-safe defaults,
  invalid lists, and normalized persistence.

## Required real-environment acceptance

The following cannot be replaced by mocks or deterministic signal fixtures:

- physical speaker/microphone AEC quality and Barge-in p95 at or below 120 ms;
- headset, speaker, device change, silence, double-talk, and loud playback;
- selected-window close/reselect and Local Follow Foreground across real
  Windows applications, multiple displays, lock, UAC/secure desktop, private
  browsing, password manager, payment, DRM, and user exclusions;
- real Fairy Voice and provider-native playback reference scope;
- eligible NVIDIA 16 GiB+ MiniCPM-o Local Session and GPU pressure;
- live Gemini/GLM behavior and Cloud billing-duration comparison;
- native WebView2 reload, focus, keyboard, window sizing, and no input
  competition;
- four-hour soak, context rotations, pauses, window changes, channel recovery,
  Sidecar crash/restart/quarantine, and process cleanup;
- signed MSI install, upgrade, uninstall, and exact release candidate gate.

Each unavailable environment remains `blocked` or `not run`, never `passed`.

## Planned verification order

When the user requests the joint gate run:

1. structural preflight and formatting;
2. focused Core, Rust, C++, Vitest, and Playwright tests;
3. affected-package regressions;
4. complete repository gate without Docker unless separately requested;
5. guarded Tauri dev WebView2 and physical media tests;
6. eligible-GPU Local and live-provider tests;
7. four-hour soak and reference performance/privacy capture;
8. administrator installer/signing/release validation; and
9. process, listener, generated-output, and worktree cleanup.

## Commands reserved for the joint verification session

Run from the Fairy V3 project root unless a working directory is stated:

1. Core focus:
   `uv run --project core pytest core/tests/realtime/test_realtime_service.py core/tests/test_core_service.py`
2. Realtime Worker focus, from `desktop/src-tauri`:
   `cargo test -p fairy-realtime-worker`
3. Desktop Rust focus, from `desktop/src-tauri`:
   `cargo test -p fairy-desktop-v3 realtime --lib`
4. Desktop TypeScript, from `desktop`:
   `npx tsc --noEmit`
5. Focused Desktop components, from `desktop`:
   `npx vitest run src/realtime/RealtimeCompanion.test.tsx src/settings/SettingsApp.test.tsx src/realtime/RealtimeCompanionWindowApp.test.tsx`
6. Realtime Playwright, from `desktop`:
   `npx playwright test e2e/realtime-readiness.spec.ts`
7. Complete Core:
   `uv run --project core pytest`
8. Complete Rust, from `desktop/src-tauri`:
   `cargo test --workspace --all-targets`
9. Rust lint, from `desktop/src-tauri`:
   `cargo clippy --workspace --all-targets -- -D warnings`
10. Complete Desktop, from `desktop`:
    `npx vitest run` followed by `npx playwright test`
11. Complete non-Docker repository gate:
    `powershell -ExecutionPolicy Bypass -File scripts/test-all.ps1 -SkipDocker`
12. Guarded native session, from `desktop`:
    `npm run tauri -- dev`

Docker, production image, Tauri release, signing, and installer commands remain
out of scope until the user explicitly starts the final release gate.

## Evidence log

- Focused Core Realtime/service: 35 passed before the complete run; after the
  final contract export-order cleanup, the Realtime/contract/service group
  passed 64/64.
- Complete Core: 869/870 passed on the first run. The only failure was the
  repository module-size boundary; after splitting the renderer state/media UI
  and Worker control/session runtime, its focused boundary suite passed 6/6.
  No Core behavior test failed.
- Realtime Worker: 69/69 passed. The live GLM integration remained ignored
  because `FAIRY_ZHIPU_API_KEY_FILE` and live provider access were unavailable.
- Complete Rust workspace with all targets passed, including 284 Desktop unit
  tests (283 passed, one CUDA/model download test ignored), the 69 Worker
  tests, all integration suites, and the three deterministic soak tests.
- Strict Rust lint passed:
  `cargo clippy --workspace --all-targets -- -D warnings`.
- TypeScript passed: `npx tsc --noEmit`.
- Focused Desktop components passed 48/48; complete Vitest passed 98 files /
  531 tests.
- Realtime Playwright passed 20/20; complete Playwright passed 81/81,
  including functional and performance projects in the controlled Vite
  lifecycle.
- Capabilities passed 118/118. Cloud non-integration passed 123 tests; one
  PostgreSQL integration test was skipped for missing test DSNs and 29
  integration tests were deliberately deselected.
- Python formatting passed for 488 files and ruff passed after sorting the
  public Realtime export list. `scripts/check_boundaries.py` and
  `git diff --check` passed.
- The guarded native Tauri development session showed exactly one visible
  `Fairy` WebView2 window with `CORE READY`. Settings opened in that same
  window; the Voice page reported `Stopped · starts on first playback`.
  Returning restored the conversation, Composer, Line Sidebar, and active
  `Preview Static · 1/3 active`.
- Opening Voice settings started no Voice, Realtime, or Omni Worker. The
  machine reported an RTX 5060 Ti with 15.7 GiB VRAM, below the frozen 16 GiB
  Local Beta gate; the model was not installed and the runtime was not
  self-tested, so Local inference was not attempted.
- `scripts/test-all.ps1 -SkipDocker` was not invoked because it also performs
  release Core sidecar, MinGit composition, Rust release-composition, and
  production frontend build steps. Those remain reserved for the explicitly
  authorized final release gate; the available non-release sub-gates were run
  individually instead.
- No API key or raw media was printed or persisted.
- The native session and its Core/Vite children were stopped after inspection.
  Final process/listener and generated-output cleanup is recorded with the
  closure commit. The unrelated parent-level `CLAUDE.md` was not read,
  modified, staged, or removed.
