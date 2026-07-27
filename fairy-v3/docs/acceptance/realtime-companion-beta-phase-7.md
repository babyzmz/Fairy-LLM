# Realtime Companion Beta Phase 7 Acceptance

Date: 2026-07-28

## Decision

Phase 7 implementation and every environment-independent certification gate
are complete. The local Beta release gate remains blocked because this machine
does not have a verified managed MiniCPM-o 4.5 installation for the mandatory
four-hour wall-clock soak. The controlled native process lifecycle ran, but
this managed desktop session exposed neither its WebView2 debugging endpoint
nor a targetable Fairy window, so proposal, quarantine, and reload interaction
are not claimed as native UI evidence.

This record therefore certifies the memory, context-rotation, resource
governance, quarantine, UI, and deterministic long-session implementation. It
does not certify Fairy Realtime Companion Beta as release-ready.

## Observable behavior and ownership

- Core owns terminal Companion Session digests, stable-caption source ranges,
  memory proposal classification, revision-fenced decisions, and Hermes Claim
  promotion.
- Only stable public user captions from the same Session can support a digest
  or proposal. Interim captions, hidden provider items, credentials, raw media,
  and inferred sensitive facts are excluded.
- Low-risk explicit progress, next-goal, and remember-this facts may follow the
  frozen automatic policy. Inferred or sensitive facts remain pending for an
  explicit main-window decision.
- Tauri owns the active Session/Segment/Epoch identity, bounded context
  carryover, GPU pressure policy, one local Sidecar recovery, and persistent
  quarantine after the next failure.
- A rotation carries only bounded public state and unfinished Assistance
  identity. A stale acknowledgement cannot advance the current Epoch.
- Resource pressure reduces visual cadence before Critical pauses media.
  Device removal terminates the Segment. Neither path selects CPU or Cloud
  automatically.
- Companion displays only bounded saved/pending counts and an Open-main-chat
  action. Accept and Reject remain in the main-window Knowledge surface.
- Stable captions are flushed before terminal digest creation. A terminal
  Sidecar state keeps its quarantine projection instead of being overwritten
  by generic shutdown state.

## Automated certification evidence

The ordered Windows gate completed:

- Complete Core pytest passed 869 tests in 323.83 seconds.
- Complete Core Ruff checks passed.
- `cargo test --workspace` passed every Rust unit, integration, and doc-test
  target. The credential-gated live GLM test remained explicitly ignored.
- The deterministic four-hour-equivalent Realtime soak passed repeatedly. It
  covers 20 acknowledged rotations, 10 privacy pause/resume cycles, five
  window changes, all resource levels, one recovery, second-failure
  quarantine, Companion reconstruction, terminal cleanup, and digest
  generation.
- `cargo fmt --all -- --check` passed.
- `cargo clippy --workspace --all-targets -- -D warnings` passed.
- `npx tsc --noEmit` passed.
- Complete Vitest passed 98 files / 530 tests. jsdom printed its known Canvas
  implementation warning; it did not skip or fail a test.
- Focused Realtime, Companion, and Presence Playwright passed 38 tests.
- Complete Desktop Playwright passed 79 tests. The dependent performance
  project completed in about 1.2 seconds.
- Repository boundary checks passed after extracting Realtime Companion
  helpers and Worker event emitters from oversized modules.

## Mocked and real boundaries

Core tests use deterministic summarization and persistence fixtures. They
exercise terminal-state requirements, stable-only evidence, idempotency,
proposal policy, Hermes promotion, restart recovery, and tenant,
Conversation, and Session isolation without provider traffic.

Rust tests use deterministic clocks and controlled Worker/Sidecar adapters.
They exercise the real state machines and strict wire schemas without claiming
production CUDA inference, physical device removal, thermal behavior, or game
frame-time impact.

Vitest replaces Tauri transport and native windows. Playwright uses the
governed desktop fixture and a controlled Vite lifecycle. Together they prove
main-window proposal authority, Companion data minimization, Session
isolation, reload reconstruction, narrow layouts, keyboard behavior, and
Reduced Motion. They do not replace a real WebView2 interaction check.

## Native process evidence

Two controlled native debug lifecycles used a disposable Desktop data
directory:

- the real Tauri debug binary, Vite, Core, capability broker, local Worker, and
  WebView2 process family started;
- ordinary cold startup did not start Realtime Worker, Omni runtime, Voice
  Worker, or CosyVoice;
- the dedicated data directory was removed after shutdown;
- every Fairy, Vite, Core, capability, and project-owned helper process from
  the lifecycle was terminated.

The managed session refused the configured loopback WebView2 debugging port
and Windows UI Automation returned no Fairy window. Consequently no native
claim is made for proposal interaction, resource-pressure rendering, live
Sidecar crash recovery, quarantine controls, or Companion reload.

## Existing test changes

Existing Core Realtime service and Core service tests were extended for the
new digest/proposal methods and strict persistence boundaries. Existing
Realtime Companion, Companion-window, transcript-persistence, Core-client,
Worker runtime, protocol, media, frame-gate, Coordinator, hardware-probe, and
Omni-manager tests were extended for the accepted Phase 7 contracts. Existing
assertions were not weakened to accommodate regressions.

New focused tests cover Realtime memory review data and UI, bounded memory
projection, GPU governance, context carryover, Sidecar supervision, and both
deterministic and wall-clock soak harness behavior.

## Explicitly blocked or deferred gates

The following are not reported as passed:

- the mandatory four-hour wall-clock soak on a reference eligible NVIDIA
  16 GiB+ device with verified MiniCPM-o model and production Omni runtime;
- physical microphone, selected-application audio, selected-window capture,
  Fairy Voice output, AEC, barge-in, multi-display, and sensitive-window
  behavior over that four-hour run;
- production CUDA latency percentiles, GPU frame-time impact, thermals, real
  OOM/device removal, and game 1% Low impact;
- native WebView2 proposal, pressure, crash, quarantine, and reload
  interaction in the current managed session;
- live paid Gemini/GLM traffic or provider answer quality;
- installer, signing, production bundle, or distribution. Those are Phase 8.

The wall-clock harness fails closed unless duration is at least four hours, the
data directory is dedicated and confirmed, managed model state is Ready, the
backend remains local, the Session completes, a digest exists, and controlled
process cleanup succeeds.
