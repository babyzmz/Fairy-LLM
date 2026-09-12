# Composer recovery

## Scope and observed faults

User requests repair of the complete Composer surface, with OpenRouter text
credentials supplied by a local file. Audio must remain local (explicit choice).
Preserve the current chat UI, secure credential store, Turn/Scope/Approval boundary
and unrelated unfinished icon/provider changes.

- Catalog loading is reported as missing credentials; unknown manual models can
  bypass the missing-entry check. Validate readiness without erasing drafts.
- Submit rejection is swallowed by the form. Show a public error and retain the
  rejected draft without overwriting newer typing.
- Schedule editor is nested in the send form: Enter can send immediately instead
  of scheduling. Serialise submission, enforce current readiness and attachment
  constraints, close menus predictably and preserve new typing.
- Document count over ten is silently truncated. Reject invalid batches explicitly.
- Capture picker does not fence late capture/list responses after close or
  disable; attach can outlive the selected model's vision capability.
- Recording availability follows the chat model, and an always-present floating
  warning obscures the draft. Local-only dictation requires an independent backend;
  never use the existing cloud transcribe route as an automatic fallback.

## Verification and ownership

For each confirmed fault add a failing behavioural regression, then minimal fix.
Use two conversations for recording/capture late-callback isolation; keep drafts
owned by the existing keyed Composer. UI tests mock only RPC/device boundaries.
No user audio, files, screenshots or history sent for provider testing: only a
synthetic short text request. Key material stays out of logs, arguments, Git and
test reports; configure via existing DPAPI credential replacement/validation.

Required: TypeScript, Composer/model/capture/voice tests, affected Core/Rust tests,
browser layout at 880x680 and 640x700, native dev and synthetic live text acceptance.
Native microphone and physical device permission checks need explicit human input;
report them separately. Any new local model/runtime installation requires approval
and must use D: storage. No startup prewarm or cloud audio fallback.

## Evidence

### Implemented

- Fixed the actual OpenRouter configuration regression: the host validation helper
  sent a string request ID although CoreBridge requires integer caller IDs (and
  assigns its own generation/sequence ID on the wire). Added a failing regression
  before correcting the helper. The DPAPI replacement/rollback path remains intact.
- Composer retains drafts on rejection and reports errors; model selection errors
  are public and actionable. Loading is distinct from missing credentials. Oversized
  document batches are rejected, not silently truncated. IME confirmation and Enter
  in a schedule editor cannot send accidentally. Screenshots remain removable but
  cannot be sent after choosing a text-only model.
- Capture close/disable/unmount fences late callbacks; Escape restores trigger
  focus. Voice permission, transcript, profile/task/conversation and hidden workspace
  changes use the recording generation. Cancel discards late results. Recorder
  constructor/start/stop failure releases microphone tracks; stop has a 5s deadline.
- Dictation always selects the reserved `local-whisper-small` profile, independently
  of DeepSeek or other chat selection. The old cloud adapter remains for explicit
  existing API clients, but is never a Composer fallback.

### Local runtime and recovery

User approved dependencies and the multilingual Small model on D:. Installed at
`fairy-v3/.runtime/stt`, excluded from Git; model plus environment currently about
0.72 GiB. Recreate it from the Fairy project with:

```powershell
./scripts/install-local-dictation.ps1
```

The installer uses Python 3.13, an isolated venv, no pip download cache, and a D:
Hugging Face cache. It pins faster-whisper 1.2.1 and huggingface-hub 1.31.0, and
`Systran/faster-whisper-small` revision
`536b0662742c02347bc0e980a01041f333bce120`. Six downloaded files passed repository
hash verification. The model's downloaded README contains its upstream MIT license
metadata. No model binaries or credentials enter commits.

Runtime operation is offline, CPU/int8, four CPU threads, one active transcription,
120 seconds / 20 MiB input maximum and a 90s process deadline. The worker receives
only bounded audio/language JSON through stdin, decodes in RAM, uses no HTTP client,
inherits no provider credentials and exits after each request (earlier than an idle
unload timer). Health checks only inspect required files and do not warm the model.
UI cancellation discards a running RPC's eventual transcript; it does not claim to
interrupt inference immediately. The bounded worker finishes/exits or is reaped on
timeout/Core shutdown. No microphone is opened automatically.

This installation targets the Windows **development build**. The production
resource path is reserved, but STT runtime packaging/installer UI is not included in
this change; other computers need the documented install. Existing packaged builds
without that runtime correctly report local transcription unavailable. The legacy
provider metadata requires TEXT alongside STT; the reserved profile is absent from
the chat catalog and explicitly rejects chat generation.

### Executed gates

- TypeScript: `npx tsc --noEmit` passed.
- Direct Vitest: Composer, ModelSelector, modelSelection, CaptureControl and
  VoiceController: 49 tests passed, including RED/GREEN cases for ID contract,
  wrong-scope transcript, unavailable vision, selector rejection and device loss.
- Core voice/providers: 36 passed. Final Capabilities package: 130/130 passed,
  including cancellation/close/timeout reaping and no-prewarm composition checks.
  Affected Capabilities Ruff checks passed.
- Rust: provider validation 2 passed; `cargo clippy --all-targets -- -D warnings`
  passed. Developer `npm run tauri -- dev --no-watch` compiled and launched.
- Playwright chat/perception/voice: 19 passed; full suite 87/87 passed. After the
  final recorder cleanup change, voice E2E was rerun: 4/4 passed. Layout gates cover
  880x680 and 640x700; RPC/device boundaries are fixtures, not real microphones.
- Real OpenRouter: the authorized key was validated via production DPAPI replacement
  and a fresh catalog returned `configured`, `stale=false`, no error. Two earlier
  attempts safely rolled back before the request-ID fix; no credential was printed.
- Real local CPU worker: synthetic WAV recognized in about 3.03s, with no microphone
  or audio upload. Full Rust Bridge/Core acceptance subsequently transcribed in two
  different temporary conversations with correct identity, then completed one
  synthetic text-only Assistant Turn through OpenRouter. Combined test: 18.30s.
  An initial test harness used the unit-test executable as Local Worker and failed;
  corrected to the actual dev desktop worker, without changing production behavior.

### Unverified and residual issues

- Full desktop Vitest: 689 passed / 2 failed before the final two recorder tests.
  `MessageList.outline.test.tsx` has the previously known responding-vs-no-reply
  assertion mismatch. `App.test.tsx` task-page test observed two calls instead of
  one in the full run, then passed in isolation. Neither test was weakened or its
  unrelated production module changed to make the suite green.
- Real native window interaction/physical microphone/Chinese dictation quality
  remain manual acceptance. The desktop was actively used by another application;
  stopped at read-only window inspection without activating, typing or recording.
  The returned occluded screenshot cannot establish native Composer visual quality.
- No Docker, release, production packaging or GPU/Realtime acceptance was run.
- Changed existing assertions: idle unavailable voice warning is now a button
  description instead of persistent overlay; E2E dictation profile is local Whisper,
  not the formerly mislabeled DeepSeek STT fixture. Both match the user's local-only
  requirement. No other existing assertion was relaxed.

No blanket claim that every project/Composer issue is closed; remaining manual
acceptance and unrelated failures above are retained for the next review.

Final cleanup: the dev Tauri lifecycle and both Playwright lifecycles exited.
Process inspection found no project Fairy/Core/Python/Cargo/Vite/STT worker left;
Codex's own Computer Use helpers were intentionally left untouched. Unrelated icon
and prior provider/voice changes remain outside the Composer commits.
