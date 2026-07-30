# Voice Replies and Realtime Reliability Implementation Plan

> Authority:
> `docs/superpowers/specs/2026-07-30-voice-replies-realtime-reliability-design.md`
>
> Acceptance:
> `docs/acceptance/voice-replies-realtime-reliability.md`

## Execution rules

- Work only in `D:\桌面\~\deskllmchat\fairy-v3`.
- Preserve the user-owned untracked `D:\桌面\~\deskllmchat\CLAUDE.md`.
- Keep each reversible slice in an independent Conventional Commit.
- Reproduce each bug and add a failing root-cause regression before production
  changes.
- Keep Voice, Realtime, Omni, Vite, Core, and Tauri stopped during ordinary
  editing and deterministic unit tests.
- Normal Fairy startup must not prewarm Voice, Realtime, or Omni.
- Never log or persist credentials, raw audio, screen frames, private prompt
  data, or unbounded device paths.
- Run targeted tests with each slice. Run full Vitest, Playwright, Rust, Clippy,
  and native Windows acceptance once after all slices are structurally ready.

## Slice 1: unblock Realtime startup and preserve diagnostics

Commit: `fix(realtime): unblock governed startup`

### Regression-first work

- Add a bridge-facing Rust regression proving the internal
  `realtime.persona.snapshot` request uses an integer JSON-RPC ID.
- Add Companion tests proving:
  - `REALTIME_PERSONA_UNAVAILABLE` renders actionable copy;
  - unknown stable uppercase codes remain visible;
  - a failure after governed Session creation reports one terminal failure;
  - loading state terminates.

### Production work

- Replace the string Persona request ID with a reserved integer.
- Add a helper that extracts the Core error code instead of converting every
  missing `result` into Persona unavailable.
- Add the Persona code and a safe unknown-code fallback to Realtime error copy.
- Introduce a bounded startup-stage projection owned by the Companion attempt:
  resolving, creating Session, Persona, runtime, voice, microphone, screen,
  optional application audio, and active.
- Fence stage updates by attempt identity and clean up the created Session on
  every failure or cancellation.

### Target validation

- Realtime Tauri unit tests.
- `RealtimeCompanion` and support Vitest suites.
- TypeScript structural check.

## Slice 2: persist Local MiniCPM verification attestation

Commit: `fix(realtime): persist local verification evidence`

### Regression-first work

- Extend `LocalReadinessService` tests for close-and-reopen restoration.
- Prove unchanged evidence restores without invoking full verify/self-test.
- Add independent invalidation fixtures for model, runtime profile, upstream
  revision, patch digest, adapter, driver, schema, malformed/partial file, and
  quarantine.
- Add atomic-write interruption and remove/repair invalidation coverage.

### Production work

- Add a versioned attestation model and atomic local store under the managed
  MiniCPM model root.
- Include the immutable manifest/runtime facts already carried by
  `OmniRuntimeSelfTestRequest`.
- Extend native self-test/hardware evidence with the stable adapter and driver
  facts required for safe restore.
- Restore `SelfTestEvidence::Passed` during service construction only after
  every cheap compatibility check succeeds.
- Persist evidence only after full integrity, layout, and runtime self-test
  success.
- Invalidate evidence on install promotion, repair, remove, runtime quarantine,
  and compatibility mismatch.

### Target validation

- Local readiness, model control, self-test, hardware, and quarantine Rust
  suites.
- Readiness Settings Vitest.
- Restart-focused integration test with a temporary managed model root.

## Slice 3: govern Voice Worker readiness

Commit: `feat(voice): govern worker readiness lifecycle`

### Protocol and preflight

- Add a lightweight Voice Worker preflight result covering Torch CUDA,
  TensorRT, ONNX `CUDAExecutionProvider`, model files, and prompt assets.
- Add stable error `VOICE_ONNX_CUDA_PROVIDER_UNAVAILABLE`.
- Separate process handshake, preflight, model warmup, and Ready in the Worker
  protocol.
- Add explicit prepare and status endpoints. Session synthesis may wait on the
  one in-flight warmup but cannot start a second load.

### Tauri authority

- Replace the process-only manager state with a sequenced lifecycle snapshot:
  stopped, starting, checking runtime, warming, ready, playing, stopping, and
  failed.
- Add idempotent prepare/status/stop commands.
- Join concurrent prepare calls, support cancellation and timeout, and reject
  late completion after stop.
- Emit bounded sequenced lifecycle events.
- Track consumer leases for automatic replies, manual playback, Settings
  sample, and Realtime.
- Release the process only after the final lease and bounded keep-warm expire.

### Playback scheduling

- Add a bounded FIFO playback queue with message scope, expiry, cancellation,
  automatic duplicate collapse, and failure drain.
- Queue requests while warming instead of returning
  `VOICE_WORKER_NOT_READY`.
- Preserve manual playback priority without starving automatic replies.
- Fix the Settings sample and warmup text resources to valid UTF-8 content.

### Regression coverage

- Worker preflight success and each failure.
- Concurrent prepare, stop-during-start, stop-during-warmup, timeout,
  interruption, retry, and late-event fencing.
- Queue bounds, ordering, duplicate collapse, expiry, cancellation, and
  two-conversation isolation.
- Lease interaction between ordinary replies and Realtime.

### Target validation

- Voice Worker pytest.
- Voice Rust manager tests.
- Native voice TypeScript tests.

## Slice 4: unify voice preferences and desktop controls

Commit: `feat(desktop): unify fairy voice replies`

### Preferences

- Add `voice_replies_enabled`.
- Migrate legacy chat/pet auto-play with logical OR.
- Remove legacy public controls and update schema, defaults, fixtures, and
  round-trip tests together.

### Settings

- Rename the category to `Voice & Realtime`.
- Replace separate auto-play switches with one Fairy voice-replies control.
- Display the authoritative sequenced Worker lifecycle.
- Add prepare/stop and Ready-only sample actions.
- Keep volume and rate.
- Replace stale `starts on first playback` copy with the actual state and
  recovery action.
- Collapse advanced Realtime configuration behind its summary card.
- Remove the redundant public Realtime Beta toggle and align activation
  validation with final Companion consent.

### Pet and chat

- Add the stateful ordinary voice-replies menu command.
- Keep `Start Realtime Companion` separate.
- Ensure the ordinary command never requests microphone, screen, or
  application-audio capture.
- Route main-chat and pet automatic playback through the unified preference and
  readiness queue.
- Keep manual message playback independent.

### Regression coverage

- All four legacy migration combinations.
- Settings and pet state consistency, stale sequence rejection, keyboard/focus,
  constrained layout, and Reduced Motion.
- Main-chat/pet automatic playback and manual-play independence.
- Pet ordinary voice action does not invoke Realtime or capture.

### Target validation

- Preferences Rust tests.
- Settings, VoiceController, PresencePanel, chat, and host adapter Vitest.
- Relevant Playwright projects.

## Slice 5: complete Realtime startup lifecycle

Commit: `feat(realtime): expose reliable companion startup`

### Startup transaction

- Connect Companion stages to Tauri/Worker authoritative transitions.
- Prepare the shared Fairy Voice Worker only when Realtime voice output
  requires it.
- Acquire microphone, observed window, and selected-application audio in the
  governed order.
- Preserve explicit per-Session consent and never auto-start capture from pet.
- Make repeated Start idempotent while one attempt is active.

### Cleanup and recovery

- On failure or cancellation, release only resources acquired by the current
  attempt.
- Report exactly one terminal governed Session transition.
- Stop polling/subscriptions on every terminal state.
- Preserve stable error and expose stage-specific retry guidance.
- Retry creates a new attempt and Session and rejects late events from the old
  identities.

### Regression coverage

- Failure and cancellation at every startup stage.
- Two-Session bidirectional isolation and rapid retry/source switching.
- Shared Voice lease behavior.
- Window close and Worker interruption cleanup.
- Pet open/focus behavior and consent boundary.

### Target validation

- Realtime Rust and Core tests.
- Companion Vitest and Playwright.
- TypeScript structural check.

## Slice 6: repair and lock the development voice environment

Commit: `build(voice): verify cuda runtime providers`

### Repository work

- Extend development startup/build preflight to require the pinned Torch,
  TensorRT, and ONNX GPU provider set.
- Detect conflicting CPU/GPU ONNX distributions with actionable output.
- Add a deterministic environment-check script that performs no model load.
- Extend release-bundle checks to validate the packaged provider capability.

### Device-local repair

- After repository checks exist, remove the conflicting CPU ONNX Runtime
  distribution from the approved `cosyvoice_env`.
- Reinstall or repair the pinned GPU distribution only if its files were
  removed by shared-package uninstall.
- Verify `CUDAExecutionProvider` without starting the voice model.
- Do not expose or modify unrelated Python environments.

### Target validation

- Script/unit tests.
- Locked environment preflight on the target machine.
- Voice Worker cold prepare and one sample only during final native acceptance.

## Slice 7: reconciliation and full gates

Commit: `docs(acceptance): record voice and realtime evidence`

### Automated order

1. `npx tsc --noEmit`
2. targeted Vitest suites
3. complete desktop Vitest
4. targeted Voice Worker/Core Python tests
5. targeted Rust tests
6. complete Rust unit and integration tests
7. `cargo clippy --all-targets -- -D warnings`
8. targeted Playwright projects
9. complete Playwright

### Native Windows order

- Start one controlled Tauri dev lifecycle.
- Confirm normal startup has no Voice or Realtime Worker.
- Prepare ordinary voice replies from the pet menu.
- Confirm Ready, one audible Fairy reply, stop, and process release.
- Start Realtime from the pet menu, confirm no pre-consent capture, then start a
  real Local Session with microphone and selected-window capture.
- Stop and confirm capture/Voice/Realtime cleanup.
- Verify Local MiniCPM once, restart Fairy, and confirm readiness restores
  without full reverify.
- Exit and confirm no test-owned Node, Tauri, Core, Voice, Realtime, Cargo, or
  Python process remains.

### Evidence

- Update the acceptance checklist with commands, counts, native evidence,
  changed existing tests and specification reasons, skipped checks, and
  residual risks.
- Do not claim Docker, release, production image, or unrun hardware evidence.
