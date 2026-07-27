# Realtime Companion Beta Phase 4 Acceptance

Date: 2026-07-28

## Decision

Phase 4 is accepted when the certification sequence below passes. This phase
makes the canonical Fairy Persona, dialogue policy, Presence projection, and
Fairy Voice speech generation authoritative across Local MiniCPM and Cloud
Live without enabling either backend implicitly.

The user-visible surface is named **Realtime Companion Beta**. `Game` remains
an activity profile, not a second product identity.

## Observable behavior and ownership

- Core creates one canonical, digest-bound Persona Snapshot. The same bounded
  system instruction and digest enter Local and Cloud backend setup.
- Worker backends return structured dialogue candidates. They cannot emit an
  assistant caption, invoke a tool, or decide a user-visible Presence state.
- Tauri's `RealtimeDialogueDirector` accepts only the current Session,
  Backend Segment, Context Epoch, sequence, Persona digest, and grounded
  candidate. Suppressed candidate bodies are not re-emitted or logged.
- Tauri's Coordinator owns the bounded Presence vocabulary and emits
  `presence_projection` with Session, Segment, Epoch, sequence, Persona
  digest, and optional level. React does not infer Presence from lifecycle
  events.
- Fairy Voice consumes only approved `fairy_voice` captions whose identity,
  Persona digest, and speech generation match the current projection.
  Sentence punctuation, useful comma boundaries, a bounded length, or stable
  completion flush short phrases.
- The next phrase may synthesize after `readyForNext`; ordered PCM playback
  remains active until each Worklet `finished` boundary.
- Backend VAD barge-in increments the Tauri speech generation, clears active
  and pending Fairy Voice playback, and projects `listening`. Late captions,
  starts, finishes, or reports from the old generation cannot overwrite the
  active state.
- Provider-native voice remains Cloud-only and explicit. Text-only never
  starts the Voice Worker.

## Scope, isolation, and asynchronous acceptance

- Two Segment identities and stale Epoch/sequence cases prove that old
  candidates and Presence projections cannot enter the current scope.
- Persona drift is rejected independently in Core snapshot, Worker candidate,
  Director, Presence channel, and Fairy Voice enqueue boundaries.
- Dialogue tests cover accepted speech, listen, assistance request,
  ungrounded content, duplicates, high-risk humour, false action claims,
  privacy gates, user speech ownership, and Fairy speech ownership.
- Speech tests cover cumulative unstable text, stable remainder, long
  unpunctuated text, synthesis pipelining, failure, active cancellation, late
  start cancellation, stale generation, and playback completion.
- Coordinator tests cover startup, backend continuation, privacy pause/resume,
  failure, terminal state, unknown Worker states, resource pressure, speech
  overlay restoration, and barge-in.
- Companion event subscriptions are removed on unmount. Controlled E2E and
  native lifecycles must stop their Vite, Node, Tauri, Core, Realtime, Voice,
  and Omni processes before acceptance.

## Mocked and real boundaries

Vitest replaces Tauri events, native Voice playback, AudioContext, and
AudioWorklet with deterministic fixtures. These tests prove ordering,
fencing, cancellation, and UI projection, but not audio-device latency or
Windows process behavior.

Playwright uses the governed desktop fixture and one controlled Vite
lifecycle. It proves constrained layout, consent controls, labels, Local/Cloud
privacy projection, and that navigation does not start workers. It does not
prove WebView2 process ownership or native window lifecycle.

A controlled Tauri dev run is therefore required to inspect the real WebView2
surface, secondary-window title/lifecycle, process tree, loopback listeners,
and absence of eager Realtime, Omni, Voice, or CosyVoice Python workers.

## Certification evidence

The ordered gate completed on Windows:

- `cargo fmt --all -- --check` passed.
- `cargo test -p fairy-realtime-worker` passed 48 tests. The credential-gated
  live GLM test remained explicitly ignored.
- `cargo test -p fairy-desktop-v3 realtime --lib` passed 38 tests.
- `cargo clippy --workspace --all-targets -- -D warnings` passed.
- `cargo test --workspace --all-targets` passed across the complete workspace.
  The first cold parallel run exceeded the `real_core` three-second startup
  budget once at 3.21 seconds. Its focused rerun passed in 1.82 seconds and a
  complete workspace rerun passed the same test in 2.16 seconds. This timing
  fluctuation is recorded rather than reported as a product failure.
- Focused Core Persona/Realtime tests passed 23 tests; the complete Core suite
  passed 852 tests.
- `npx tsc --noEmit` passed.
- Focused Realtime, Presence, transport, and settings Vitest passed 9 files /
  96 tests; complete Vitest passed 95 files / 511 tests. The jsdom Canvas
  implementation warning is a mocked-environment limitation, not a skipped
  test.
- Focused Realtime Playwright passed 11 tests. Complete Desktop Playwright
  passed 72 tests, including the performance project in about 1.2 seconds.

The controlled Tauri dev lifecycle was inspected through loopback-only CDP
without taking keyboard or mouse control:

- the main WebView rendered at 1440x900, reported `CORE READY`, and had no
  horizontal overflow;
- the secondary native window was 620x760 and its operating-system title was
  `Fairy Realtime Companion Beta`;
- the Companion rendered its Local privacy statement, scoped window picker,
  consent controls, Fairy Voice selection, and fail-closed Beta preference
  message;
- the expected local worker and capability broker were present, while
  Realtime Worker, Voice Worker, CosyVoice, and Omni counts remained zero;
- the Companion was hidden through its scoped native command; shutdown then
  removed the controlled project process tree and all listeners on ports
  1430, 1431, and 9223.

## Explicitly deferred gates

This phase does not run or claim:

- Docker, PostgreSQL integration environments, release, installer, production
  image, or production bundle builds;
- production CUDA compilation or installed MiniCPM-o 4.5 model inference;
- live microphone, application-audio, or long-duration game/focus sessions;
- paid Gemini or GLM traffic;
- real audio-device barge-in latency, acoustic echo cancellation quality, GPU
  pressure, thermals, game impact, multi-display capture, or soak results.

These remain Phase 5–8 or hardware/provider acceptance gates and stay
fail-closed.
