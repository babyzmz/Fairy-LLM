# Realtime Companion Beta Phase 5 Acceptance

Date: 2026-07-28

## Decision

Phase 5 is accepted as the native activity-policy, bounded visual-context,
Context Epoch, standby, and active-session projection layer for Realtime
Companion Beta.

Tauri remains authoritative for requested Profile, effective activity,
interaction intensity, cooldown and duplicate policy, meaningful activity,
standby, duration extension, Backend Segment, Context Epoch, privacy state,
and user-visible Presence. React renders the bounded native projection and
sends explicit commands; it does not infer or run these policies.

## Observable behavior and ownership

- `Auto` is a requested Profile. Its effective `Game` or `Focus` activity is a
  separate native projection, not a second Persona.
- Explicit `Game` and `Focus` never enter the classifier. `Auto` changes only
  after three consistent, grounded observations and an acknowledged Context
  Epoch rotation.
- Direct replies remain immediate. Proactive Game cooldowns are 90, 45, and
  20 seconds for Quiet, Standard, and Active. Focus cooldowns are eight
  minutes for Quiet/Standard and three minutes for Active.
- Semantic duplicate suppression is bounded, deterministic, and reset with
  the active context. Rejected candidates do not consume cooldown.
- Visual context is latest-only, identity-bound, and capped at one frame per
  second normally or two frames per second for a five-second material-change
  boost. Static pixels, cursor-scale noise, malformed images, and stale Epochs
  are suppressed.
- Application audio has its own selected-application channel. It never enters
  the microphone transport.
- Local context rotation requires the exact `context_rotated`
  acknowledgement. Cloud rotation replaces the Provider Socket. Outputs from
  the prior Epoch or provider generation cannot be relabelled.
- After three quiet minutes, Local pauses media while retaining its runtime;
  Cloud drops its Provider Socket. Local may request unload after ten minutes,
  but Phase 7 remains responsible for resource-governor execution.
- Wake creates a new Local Epoch or Cloud Segment. Four hours is the default
  Presence duration; reaching it requires an explicit 30-minute extension.
- Privacy pause and resume are native, media-fenced transitions. Resume rotates
  context before accepting new content.
- The active Companion displays requested Profile, effective activity,
  intensity, Backend, exact cooldown help, standby reason, Wake, and duration
  extension from native status. Reload restores the active native projection.

## Mocked and native boundaries

Rust and C++ tests exercise native policy, strict control frames, Context
Epoch acknowledgement, provider replacement, output fences, standby, wake,
duration extension, and the bounded projection without microphone, GPU, or
provider traffic.

Vitest replaces Tauri events and native media surfaces. It proves UI
projection, stale-event rejection, remount restoration, policy commands,
privacy controls, and duration controls, but does not prove Windows window or
process ownership.

Playwright uses the governed desktop fixture and one controlled Vite
lifecycle. It proves 880x680 and 640x700 layouts, requested/effective identity,
native-command routing, exact cooldowns, standby/wake, explicit duration
extension, and Reduced Motion. It does not claim live Realtime inference.

A controlled Tauri dev run therefore inspected the actual WebView2 surfaces,
native command boundary, lazy worker state, and shutdown lifecycle.

## Certification evidence

The ordered gate completed on Windows:

- The pinned Omni source was restored at revision
  `74699a53df6ca0f4947ff37066f851532c20b12d`. The pinned CMake 4.4.0 archive
  passed its locked SHA-256 check.
- The contract C++ runtime built with MSVC. CTest passed 2/2 control and media
  tests, and the process-level control integration suite passed.
- The direct schema-2 runtime self-test matched the pinned upstream revision,
  patch-set digest, model manifest, and runtime compatibility. It correctly
  reported `build_profile: contract`, `cuda_compiled: false`,
  `backend_ready: false`, and `model_probe: not_run_contract`.
- `cargo fmt --all -- --check` passed.
- `cargo test -p fairy-realtime-worker` passed 56 tests. The credential-gated
  live GLM test remained explicitly ignored.
- `cargo test -p fairy-desktop-v3 realtime --lib` passed 56 focused tests.
- `cargo clippy --workspace --all-targets -- -D warnings` passed.
- `cargo test --workspace --all-targets` passed, including 254 Desktop unit
  tests, 56 Realtime Worker tests, and every workspace integration target.
  The real Core startup test completed inside its budget at 2.63 seconds.
- `npx tsc --noEmit` passed.
- Focused Realtime, Presence, Companion, and Settings Vitest passed 4 files /
  45 tests.
- Complete Vitest passed 95 files / 514 tests. jsdom emitted its known Canvas
  implementation warning; no test was skipped or failed because of it.
- Focused Realtime Playwright passed 16 tests.
- Complete Desktop Playwright passed 77 tests. The performance project
  completed in about 1.2 seconds.

## Native WebView2 evidence

The isolated Tauri dev lifecycle used loopback-only CDP and did not take
keyboard or mouse control:

- the main WebView rendered at 1440x900, reported `CORE READY`, and had no
  horizontal overflow;
- the Companion native target was created at 620x760; after a WebView reload it
  restored the `Realtime Companion Beta` dialog without horizontal overflow;
- the Companion showed the Local privacy boundary, selected-window capture,
  Fairy Voice, the Beta fail-closed message, and scoped source controls;
- its own authorized `hide_companion_window` command completed successfully;
- two expected `fairy.exe` processes represented the Desktop and Local Worker;
  `fairy-realtime-worker`, `fairy-omni-runtime`, and CosyVoice/Voice Python
  counts remained zero;
- the expected Core and capability Python processes were present without
  starting Voice or Omni;
- shutdown removed every controlled PID, launcher state file, path alias, and
  listener on ports 1430, 1431, and 9223.

## Explicitly deferred gates

This phase does not run or claim:

- Docker or PostgreSQL integration environments;
- model download, production CUDA compilation, or installed MiniCPM-o 4.5
  inference;
- live microphone, selected-application audio, game capture, or long-duration
  Presence sessions;
- paid Gemini or GLM traffic;
- real audio-device barge-in latency, echo cancellation, GPU pressure,
  thermals, game impact, multi-display capture, or soak results;
- Tauri release, installer, production image, or production bundle builds.

Those remain Phase 6-8 or hardware/provider certification gates. Contract and
CPU-compatible Omni artifacts remain fail-closed and cannot unlock Local Beta.
