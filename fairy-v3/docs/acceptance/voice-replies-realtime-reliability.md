# Voice Replies and Realtime Reliability Acceptance

**Design:** `docs/superpowers/specs/2026-07-30-voice-replies-realtime-reliability-design.md`
**Status:** Planned

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

Target development environment currently reports:

- Torch `2.7.0+cu128`;
- CUDA available on NVIDIA GeForce RTX 5060 Ti;
- TensorRT `10.13.3.9`;
- ONNX Runtime providers: `AzureExecutionProvider`,
  `CPUExecutionProvider`;
- both `onnxruntime` and `onnxruntime-gpu` distributions installed.

Expected regression: preflight rejects this masking state with
`VOICE_ONNX_CUDA_PROVIDER_UNAVAILABLE`; the repaired locked environment exposes
`CUDAExecutionProvider`.

### Local verify persistence

- model install state persists as Ready;
- `LocalReadinessService::self_test_evidence` is initialized to `None`;
- successful self-test evidence exists only in process memory.

Expected regression: successful verify writes an attestation, and reopening the
service restores passed evidence only when the compatibility key matches.

## Automated scenarios

### Preferences and UI

- [ ] Migrate false/false to unified disabled.
- [ ] Migrate true/false, false/true, and true/true to unified enabled.
- [ ] Settings renders every Worker lifecycle state and terminal action.
- [ ] Settings and pet menu reject stale lower-sequence state.
- [ ] Main chat and pet both honor the unified preference.
- [ ] Manual message playback remains available while automatic replies are
      disabled.
- [ ] Keyboard focus, Enter, Space, Escape, and constrained layout behavior
      remain correct.

### Voice asynchronous lifecycle

- [ ] Two concurrent starts share one startup.
- [ ] Stop during process launch terminates with Stopped.
- [ ] Stop during warmup cancels waiters and terminates with Stopped.
- [ ] Startup success drains the bounded queue once and in order.
- [ ] Startup failure drains the queue with the original stable code.
- [ ] Timeout terminates busy state and process ownership.
- [ ] Interrupted child process is detected and recoverable.
- [ ] Duplicate automatic playback for one message collapses.
- [ ] Expired and cancelled queue entries never play.
- [ ] A late Ready event after Stop is ignored.
- [ ] Realtime lease prevents ordinary-reply Stop from killing the Worker.

### Scope isolation

- [ ] Two conversations with queued replies remain bidirectionally isolated.
- [ ] Rapid conversation switching rejects late audio and lifecycle events.
- [ ] Two Realtime Sessions remain bidirectionally isolated.
- [ ] Source replacement and Session retry reject events from the prior
      session, segment, context epoch, and attempt.

### Realtime startup and cleanup

- [ ] Persona RPC uses an integer ID and succeeds.
- [ ] Persona error preserves `REALTIME_PERSONA_UNAVAILABLE`.
- [ ] Backend resolution, Session creation, Persona, runtime, voice,
      microphone, screen, and application-audio stages each render.
- [ ] Failure at every stage reports exactly one terminal Session and releases
      resources already acquired.
- [ ] Cancellation and window close during every startup stage terminate work.
- [ ] Unknown uppercase stable code remains visible in diagnostics.
- [ ] Pet action opens/focuses Companion without calling capture start.
- [ ] Repeated Start cannot create duplicate active Sessions.

### Verification attestation

- [ ] Successful full verify atomically writes attestation.
- [ ] Close-and-reopen with identical facts restores Ready.
- [ ] Restore path does not hash managed model contents or run native self-test.
- [ ] Missing, malformed, partial, or old-schema attestation fails closed.
- [ ] Changed model manifest/version invalidates.
- [ ] Changed runtime/profile/upstream/patch digest invalidates.
- [ ] Changed adapter or driver invalidates.
- [ ] Runtime quarantine overrides otherwise matching evidence.
- [ ] Failed explicit verify does not replace prior evidence as current.
- [ ] Remove/repair invalidates evidence.

## Native Windows acceptance

Run only when the user is not interacting with the target windows.

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
