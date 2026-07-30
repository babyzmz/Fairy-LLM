# Voice Replies and Realtime Reliability Acceptance

**Design:** `docs/superpowers/specs/2026-07-30-voice-replies-realtime-reliability-design.md`
**Status:** Automated closure complete on 2026-07-30; native Windows
microphone, capture, audible-playback, and restart acceptance remains pending
with the user present.

## Observable behavior

1. Settings has one ordinary Fairy voice-replies preference for both main-chat
   and pet replies.
2. Enabling the preference explicitly starts and prepares the on-demand Voice
   Worker in the current session without playing a test phrase.
3. The first eligible reply waits for a cold Worker to become Ready instead of
   failing with `VOICE_WORKER_NOT_READY`.
4. Disabling ordinary voice replies cancels its pending playback and releases
   the Worker unless another consumer still owns it.
5. The pet menu separately exposes ordinary voice replies and
   `Start Realtime Companion`.
6. The Realtime action opens/focuses Companion but does not acquire microphone,
   screen, or application audio until the user confirms the session.
7. Realtime startup shows its current stage and preserves the exact stable
   failure code.
8. A successfully verified, unchanged Local MiniCPM installation remains Ready
   after Fairy restarts without repeating a full model hash.
9. Normal Fairy startup does not launch Voice or Realtime Workers.

## Invariants

- Ordinary voice reply enablement never grants microphone or screen access.
- Realtime capture always requires explicit per-session consent.
- Pet never owns Persona, project, conversation, approval, or Realtime state.
- Voice playback never crosses task, conversation, turn, or message scope.
- Realtime events never cross session, segment, context epoch, or startup
  attempt.
- Late async completion cannot overwrite a newer stop, retry, conversation, or
  session.
- A Worker is released only when no active consumer lease remains.
- Model/runtime verification evidence is restored only for the same verified
  compatibility facts and never overrides quarantine.
- No credentials, raw audio, screen frames, or private prompt contents enter
  public diagnostics.

## Deterministic pre-fix evidence

### Realtime Persona startup

- Latest local `core_realtime_sessions` row:
  - provider: `local_mini_cpm_o45`
  - status: `failed`
  - error: `REALTIME_PERSONA_UNAVAILABLE`
  - elapsed before failure: approximately 17 ms
- `desktop/src-tauri/src/lib.rs` sends `realtime.persona.snapshot` with a string
  JSON-RPC ID.
- `CoreBridge::call` requires `Value::as_i64()`.
- A separate integer-ID Persona RPC returns the canonical snapshot.

Expected regression: the internal Persona call reaches Core with an integer ID,
and a successful response advances startup to the next stage.

### Voice startup

- Cold health reports `idle` without starting a Worker.
- Voice test creates a Worker and immediately dispatches synthesis.
- Worker warmup is asynchronous and session creation rejects non-Ready health.
- Settings does not refetch health during or after the test.

Expected regression: explicit prepare observes startup through Ready, and a
playback request submitted during warmup completes once without an initial
not-ready failure.

### Voice CUDA environment

Before the runtime repair, the target development environment reported:

- Torch `2.7.0+cu128`;
- CUDA available on NVIDIA GeForce RTX 5060 Ti;
- TensorRT `10.13.3.9`;
- ONNX Runtime providers: `AzureExecutionProvider`,
  `CPUExecutionProvider`;
- both `onnxruntime` and `onnxruntime-gpu` distributions installed.

Expected regression: preflight rejects this masking state with
`VOICE_ONNX_CUDA_PROVIDER_UNAVAILABLE`; the repaired locked environment exposes
`CUDAExecutionProvider`.

The repaired locked environment now passes the repository preflight with
Python `3.10.11`, Torch/Torchaudio `2.7.0+cu128`, CUDA on the NVIDIA GeForce RTX
5060 Ti, TensorRT `10.13.3.9`, ONNX Runtime GPU `1.22.0`, and both
`TensorrtExecutionProvider` and `CUDAExecutionProvider`. The conflicting CPU
distribution is absent.

### Local verify persistence

- model install state persists as Ready;
- `LocalReadinessService::self_test_evidence` is initialized to `None`;
- successful self-test evidence exists only in process memory.

Expected regression: successful verify writes an attestation, and reopening the
service restores passed evidence only when the compatibility key matches.

## Automated scenarios

### Preferences and UI

- [x] All four legacy chat/pet preference combinations migrate to the unified
      Fairy voice-replies preference.
- [x] Settings renders the bounded Worker lifecycle and explicit prepare,
      test, stop, install, and retry actions.
- [x] Settings, main chat, and pet use the same preference; manual message
      playback remains independent.
- [x] Pet voice replies and Realtime launch remain separate commands, and
      opening Companion does not start capture.
- [x] Keyboard, constrained-window, and Reduced Motion behavior is covered by
      component and browser tests.

### Voice asynchronous lifecycle

- [x] Concurrent prepare joins one startup; stop during launch/warmup,
      timeout, interruption, retry, and late-Ready fencing are covered.
- [x] The bounded playback queue covers ordering, priority, duplicate collapse,
      expiry, cancellation, failure drain, and conversation isolation.
- [x] Consumer leases keep the shared Worker alive while Voice or Realtime
      still owns it and release it only after the final owner.
- [x] Automatic playback is bounded to stable durable message ranges and is
      cancelled when the unified preference is disabled.

### Scope isolation

- [x] Queued playback is fenced by task, conversation, turn, message, offsets,
      and idempotency key.
- [x] Realtime state and late events are fenced by Session, segment, context
      epoch, startup attempt, and monotonic startup stage.
- [x] Rapid retry, source replacement, window close, and late completion cannot
      overwrite the newer attempt.

### Realtime startup and cleanup

- [x] Persona RPC uses an integer ID and preserves exact stable Core errors.
- [x] Backend resolution, Session creation, Persona, runtime, optional Fairy
      voice, microphone, observed window, application audio, and active stages
      are projected in order.
- [x] Failure/cancellation performs one governed terminal transition and
      bounded worker cleanup for the current attempt only.
- [x] Unknown uppercase stable codes remain visible in diagnostics.
- [x] Repeated Start is idempotent while an attempt is active.

### Verification attestation

- [x] Successful full verification atomically writes versioned evidence.
- [x] Identical cheap compatibility facts restore Ready after reopen without a
      full model hash or native self-test.
- [x] Missing, malformed, partial, stale-schema, model, runtime, upstream,
      patch, adapter, and driver mismatches fail closed.
- [x] Quarantine, install promotion, remove, repair, and failed explicit verify
      preserve the required invalidation semantics.

## Automated gate evidence

- `npx tsc --noEmit`: passed.
- Complete desktop Vitest: 99 files, 548 tests passed.
- Voice Worker pytest with `voice-worker/src` on `PYTHONPATH`: 15 passed.
- Locked Voice runtime preflight: passed; CUDA and TensorRT providers are
  available and the CPU ONNX Runtime distribution is absent.
- `cargo test -p fairy-realtime-worker`: 69 passed; one live-provider test
  intentionally ignored.
- Desktop Rust library tests: 297 passed; one live/native test intentionally
  ignored.
- `window_scope` integration: 11 passed.
- `cargo test --workspace --all-targets`: passed on the complete rerun. The
  first cold concurrent run exceeded the Core readiness budget once
  (3.67 seconds against 3 seconds); its isolated rerun passed in 1.96 seconds,
  and the subsequent complete run passed.
- `cargo fmt --check` and
  `cargo clippy --all-targets -- -D warnings`: passed.
- Targeted Voice/Presence/Realtime Playwright after contract reconciliation:
  28 passed.
- Complete Playwright: 81 passed, including the dependent performance project
  at approximately 1.2 seconds.

The first complete Playwright run identified three stale test contracts rather
than product failures. The fixture now uses desktop-preferences schema 11 and
`voice_replies_enabled`; the pet-menu assertion uses the current
`Fairy voice replies` label; and backend projection tests explicitly open the
collapsed Advanced Realtime settings before querying the selector.

The first Voice Worker pytest invocation used the package root instead of
`voice-worker/src` as `PYTHONPATH` and failed during collection. The corrected
locked-source invocation passed all tests.

## Native Windows acceptance

Run only when the user is not interacting with the target windows.

This section is intentionally not claimed by automated mocks. It remains the
joint development-build acceptance session.

1. Start Tauri dev with one controlled Vite/Core lifecycle.
2. Confirm no Voice or Realtime Worker exists after ordinary startup.
3. Open pet menu and activate ordinary voice replies.
4. Confirm lifecycle reaches Ready and exactly one Voice Worker exists.
5. Trigger one Fairy reply and confirm audible playback.
6. Disable voice replies and confirm the Worker exits after leases expire.
7. Start Realtime Companion from the pet menu.
8. Confirm no capture begins before final consent.
9. Select a real observed window, grant microphone and screen consent, and
   start Local Realtime.
10. Confirm startup advances beyond Persona, capture is scoped to the selected
    source, microphone conversation works, and Fairy output is audible.
11. Stop Realtime and confirm capture, Voice, and Realtime processes release.
12. Verify Local MiniCPM once, restart Fairy, and confirm readiness restores
    without a multi-minute full verification.
13. Change or simulate one compatibility fact and confirm readiness requires
    explicit verification again.
14. Exit Fairy and confirm no Node, Tauri, Core, Voice, Realtime, Cargo, or
    test-owned processes remain.

## Required commands

Run targeted checks before broad suites:

1. TypeScript structural check.
2. Affected Voice, Settings, Presence, Realtime, and readiness Vitest suites.
3. Affected Rust unit and integration tests.
4. Voice Worker Python tests and locked-environment preflight.
5. Chat/Settings/Presence/Realtime Playwright projects.
6. Desktop complete Vitest.
7. Complete Playwright.
8. Rust complete tests and Clippy.

Exact commands belong in the implementation plan after affected package targets
are confirmed.

## Mock boundaries

Automated tests may mock:

- process launch and exit;
- audio sink and playback timing;
- capture sources and Windows permissions;
- GPU/runtime fact providers;
- model self-test execution.

Mocks are acceptable for deterministic state, fencing, cleanup, and error
mapping. They do not establish:

- actual CUDA provider loading;
- TensorRT/CosyVoice model readiness;
- audible PCM playback;
- Windows microphone permission;
- selected-window DDA capture;
- real VRAM and driver compatibility;
- native multi-window focus and menu hit targets.

Those claims require the Native Windows acceptance above.

## Completion evidence

The final report must list:

- changed existing tests and the product-spec reason for each change;
- commands and pass/fail counts;
- native evidence;
- any skipped or blocked environment checks;
- process cleanup evidence;
- residual risks.

No Docker, release, or production-image result may be claimed unless those
commands were separately authorized and run.

## Cleanup and residual risk

- The final elevated process audit found no Node, npm, Cargo, Rust, Fairy,
  Core, Voice Worker, Realtime Worker, Python, or Vite process whose command
  line belonged to this Fairy V3 checkout.
- Docker, Tauri release, production image, and production bundle builds were
  not run.
- Audible PCM output, Windows microphone permission, real selected-window DDA
  capture, a real Local MiniCPM session, and readiness restoration across an
  actual Tauri restart remain native acceptance items.
