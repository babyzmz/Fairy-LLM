# Realtime Companion Beta Phase 5 Implementation Plan

> **Design:** `docs/superpowers/specs/2026-07-28-realtime-companion-beta-phase-5-design.md`
>
> **Authority:** the frozen Phase 0–4 contracts and the user-approved
> Realtime Companion Beta final design.

## Goal

Make Auto/Game/Focus and Quiet/Standard/Active real native policy, add bounded
event cooldown and semantic deduplication, rotate Window/Context Epochs
authoritatively, replace byte-hash video sampling with a latest-only perceptual
Frame Gate, and move standby/lifetime ownership out of React.

## Non-goals

This phase does not execute Core Assistance, persist a durable session digest,
promote memory, perform a real four-hour soak, govern GPU pressure, quarantine
the Sidecar, build an installer, download a model, run paid providers, or claim
production latency.

## Task 1: Add native activity and interaction policy

**Files**

- Create:
  `desktop/src-tauri/src/realtime_activity.rs`
- Modify:
  `desktop/src-tauri/src/lib.rs`
- Modify:
  `desktop/src-tauri/src/realtime_coordinator.rs`
- Modify:
  `desktop/src-tauri/src/realtime_dialogue.rs`

**Implementation**

1. Add an Auto classifier with confidence, grounding, sequence, five-sample
   window, and three-consistent-observation hysteresis.
2. Resolve explicit Game/Focus without classifier mutation; Auto defaults to
   conservative Focus.
3. Add monotonic proactive budgets for profile/intensity combinations.
4. Let current-user replies bypass only proactive cooldown.
5. Restrict Quiet and Focus proactive categories to the approved high-value
   intents.
6. Add a 32-entry, ten-minute canonical event cache with exact and bounded
   near-duplicate detection.
7. Clear classifier/event state on epoch, segment, privacy, and terminal
   transitions.

**Tests**

- explicit profiles never switch;
- Auto ignores low-confidence, stale, ungrounded, or conflicting evidence;
- three consistent observations switch exactly once;
- cooldown values match Game/Focus and Quiet/Standard/Active;
- user replies remain immediate;
- rejected output does not consume cooldown;
- cache remains bounded and expires;
- near-identical repeated events suppress without storing bodies in diagnostics.

**Commit**

`feat(desktop): govern realtime activity policy`

## Task 2: Add the perceptual latest-only Frame Gate

**Files**

- Create:
  `desktop/src-tauri/crates/realtime-worker/src/frame_gate.rs`
- Modify:
  `desktop/src-tauri/crates/realtime-worker/src/lib.rs`
- Modify:
  `desktop/src-tauri/crates/realtime-worker/src/runtime.rs`
- Modify:
  `desktop/src-tauri/crates/realtime-worker/src/media.rs`
- Modify:
  `desktop/src-tauri/crates/realtime-worker/src/backend/cloud.rs`

**Implementation**

1. Decode a bounded grayscale thumbnail and calculate perceptual and
   four-region difference without retaining pixels.
2. Enforce 1 FPS baseline, 2 FPS five-second event boost, static suppression,
   and five-minute heartbeat using monotonic time.
3. Keep one latest captured frame and clear signatures/media on pause,
   rotation, source change, and stop.
4. Feed only bounded audio/user/backend-state triggers into the gate.
5. Remove JPEG byte hashes as the visual-equivalence decision.
6. Remove Cloud application-audio mixing from microphone speech. A provider
   without a distinct application-audio transport fails that channel closed.

**Tests**

- identical pixels with different JPEG bytes suppress;
- material regional change sends and starts a bounded boost;
- cursor-scale/noise changes suppress;
- timing never exceeds the 1/2 FPS ceilings;
- heartbeat and reset behavior are deterministic;
- decode failure drops safely;
- application audio never enters microphone bytes;
- queues remain latest-only and bounded.

**Commit**

`feat(realtime): gate bounded visual context`

## Task 3: Implement acknowledged Context Epoch rotation

**Files**

- Modify:
  `desktop/src-tauri/crates/realtime-worker/src/backend/mod.rs`
- Modify:
  `desktop/src-tauri/crates/realtime-worker/src/backend/local_omni.rs`
- Modify:
  `desktop/src-tauri/crates/realtime-worker/src/backend/cloud.rs`
- Modify:
  `desktop/src-tauri/crates/realtime-worker/src/protocol.rs`
- Modify:
  `desktop/src-tauri/crates/realtime-worker/src/runtime.rs`
- Modify:
  `desktop/src-tauri/crates/realtime-worker/src/main.rs`
- Modify:
  `desktop/src-tauri/src/realtime_coordinator.rs`
- Modify:
  `desktop/src-tauri/src/realtime_worker.rs`
- Modify native Omni control tests only where required by the already-frozen
  `context_rotate` protocol.

**Implementation**

1. Add a bounded `RotateContext` host/runtime/backend command carrying next
   epoch, safe reason, and bounded public summary.
2. Make Local Omni use its existing strict `context_rotate` command and wait
   for the matching `context_rotated` acknowledgement.
3. Make Cloud invalidate provider generation and create a fresh provider
   context; old provider outputs must never be relabelled.
4. Publish the next identity only after backend acknowledgement.
5. Recreate the Director and clear speech/event/frame state on success.
6. Reject all prior-epoch events, media, Presence, captions, assistance, and
   speech state.
7. Fail the active Segment safely if either side cannot acknowledge.

**Tests**

- next epoch must advance by exactly one;
- stale and skipped epochs reject;
- Local acknowledgement is identity-bound;
- Cloud output racing rotation cannot cross the fence;
- old speech generation and media are cleared;
- bounded carry-forward summary accepts public text and rejects raw/oversized
  content;
- failed rotation never activates the next epoch.

**Commit**

`feat(realtime): rotate governed context epochs`

## Task 4: Move standby and duration ownership to the Coordinator

**Files**

- Modify:
  `desktop/src-tauri/src/realtime_coordinator.rs`
- Modify:
  `desktop/src-tauri/src/realtime_worker.rs`
- Modify:
  `desktop/src-tauri/crates/realtime-worker/src/protocol.rs`
- Modify:
  `desktop/src-tauri/crates/realtime-worker/src/main.rs`
- Modify:
  `desktop/src-tauri/crates/realtime-worker/src/runtime.rs`
- Modify:
  `desktop/src/realtime/RealtimeCompanion.tsx`
- Modify:
  `desktop/src/core/client.ts`
- Modify:
  `desktop/src/core/tauriTransport.ts`

**Implementation**

1. Track meaningful activity with a native monotonic clock.
2. Add deterministic Coordinator ticks and actions for three-minute standby,
   ten-minute Local unload eligibility, and the Presence duration ceiling.
3. Local standby pauses model input/visual decode while retaining the loaded
   runtime.
4. Cloud standby closes the provider context and exposes a governed wake action
   that creates a new Segment without standby-media replay.
5. Wake Local into a new epoch and Cloud into a new Segment.
6. Make privacy pause clear media immediately and remain stronger than standby.
7. Remove React idle-stop and maximum-duration timers.
8. Keep panel close/reload independent of session lifecycle.

**Tests**

- no transition before the threshold;
- Local and Cloud actions differ at three minutes;
- activity resets the deadline;
- privacy pause cannot be woken by stale media;
- Local ten-minute unload is only a request for Phase 7 governance;
- duration expiry requires explicit extension or end;
- React contains no session lifetime interval/timeout;
- companion remount restores authoritative status.

**Commit**

`feat(desktop): own realtime standby natively`

## Task 5: Project requested/effective Profile and intensity

**Files**

- Modify:
  `desktop/src-tauri/src/realtime_coordinator.rs`
- Modify:
  `desktop/src-tauri/src/realtime_worker.rs`
- Modify:
  `desktop/src/core/client.ts`
- Modify:
  `desktop/src/realtime/realtimePresence.ts`
- Modify:
  `desktop/src/realtime/RealtimeCompanion.tsx`
- Modify:
  `desktop/src/realtime/realtime-companion.css`
- Modify:
  `desktop/src/settings/SettingsApp.tsx`
- Modify related component and E2E fixtures/tests.

**Implementation**

1. Add requested profile, effective activity, intensity, standby reason, and
   wake availability to the bounded native status/projection.
2. Show Auto’s effective activity without representing it as a second Persona.
3. Show exact cooldown behavior in accessible help text.
4. Keep active Backend identity visible.
5. Route explicit controls through native commands and restore state after
   window reload.
6. Keep narrow-window and reduced-motion behavior bounded.

**Tests**

- requested/effective values remain distinct;
- stale projections cannot change labels;
- Game/Focus/intensity labels survive companion remount;
- standby/wake controls use native status;
- narrow UI has no overflow or hidden critical control;
- opening the panel starts no Worker.

**Commit**

`feat(desktop): expose realtime activity state`

## Task 6: Certify Phase 5

**Files**

- Create:
  `docs/acceptance/realtime-companion-beta-phase-5.md`
- Modify tests only for independent Critical/Important defects found by the
  ordered gate.

**Validation order**

1. native Omni control tests and debug runtime self-test
2. `cargo fmt --all -- --check`
3. focused realtime-worker and Desktop Rust tests
4. `cargo clippy --workspace --all-targets -- -D warnings`
5. full Rust workspace tests
6. `npx tsc --noEmit`
7. focused Realtime/Presence Vitest
8. complete Vitest
9. focused Realtime Playwright
10. complete Playwright
11. controlled Tauri dev WebView2 panel reload/close, status, process, and
    cleanup inspection
12. repository status and process/listener cleanup

Record mocked, native, and deferred evidence separately. Do not run Docker,
PostgreSQL, model download, live microphone/application audio, paid provider
traffic, release, installer, production bundle, or production CUDA inference.

**Commit**

`test(realtime): certify activity policy`
