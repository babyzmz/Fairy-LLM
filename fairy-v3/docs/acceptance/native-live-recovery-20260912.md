# Native/live recovery acceptance — 2026-09-12

User explicitly resumed the previously interrupted native/model/voice/GPU gates.
No model installation, credential export, remote upload, Docker or release build.
Use existing secure provider configuration and a separate acceptance conversation.
Stop UI input on user competition or Escape. Permission/security dialogs remain
user-controlled. Native interactions use Computer Use, not injected UI automation.

## Baseline

- Git HEAD: `45415f272`; only pre-existing CLAUDE.md and SVG backup untracked.
- No running project process found before launch.
- NVIDIA RTX 5060 Ti: 16311 MiB total, 2518 MiB used, 13533 MiB free.
- Consistent source DB backup and independent migration/reopen passed at
  `.tmp/native-acceptance-20260912-live-backup`; source unchanged by probe.
  SHA-256 `081ddf899555c12d888b06f1271c931b1044f95f1f5d385f32b1c7869fe2370c`.

## Ordered journeys

1. Actual WebView2/Core startup; ordinary startup must not preload voice/realtime.
2. Two new acceptance chats: real provider reply/tool evidence, chat switching,
   inspector hide/restore and settings state retention; no existing user chat edits.
3. Pet binding/input/menu and quiet centered SVG; native glass remains a distinct
   visual/input gate. Do not replace a native test with browser CSS evidence.
4. Existing voice readiness, explicit test playback, realtime eligibility and
   start/stop when required devices/permissions are available.
5. If a real model loads, sample actual VRAM/child processes over five wall-clock
   idle minutes; active session must remain protected. No simulated-clock claim.
6. Close test-owned processes, record results and missing prerequisites separately.

All journeys initially not verified. Failures record exact public codes and scope,
never raw credentials, private conversation contents or model response internals.

## Live evidence (continued at user request)

- Actual debug Tauri/WebView2 launched with the existing secure data directory after
  the backup above. Core connected; ordinary startup displayed Voice Worker
  `Stopped · ready to start`, without a voice/realtime model prewarm.
- Two separate new acceptance chats were created. Only benign test instructions
  were sent; existing user messages and files were not edited. The temporary
  manual-model preference and collapsed Inspector were restored afterward.
- Chat A (Auto) requested only `Fairy 在线`, explicitly forbidding tools/files.
  Turn `01a095ad-287c-7438-b1e5-ae1c50c573d7`, workflow
  `01a095ad-287e-741e-9042-7c45f8cc5a9a`: started 12:52:28 UTC,
  persisted failed at 12:53:25 UTC with `PROVIDER_ERROR`. Three coordinator
  attempts were classified `protocol` (two DeepSeek, one GLM). No successful
  model reply and no tool execution were observed. Provider compatibility fails
  this journey; the category alone does not establish the underlying wire error.
- **Important: failed-turn UI does not settle.** While Settings was open the
  failure occurred. Returning to the chat showed `Response failed` together with
  `Running`, `Working`, and the busy Composer. Read-only SQLite inspection proved
  both Turn and Workflow were already terminal, with no active lease. The stale
  busy state remained several minutes. Switching A → B → A restored ordinary
  input; Settings return alone did not. Line Sidebar still said `Fairy is
  responding…` for the failed exchange after the busy state recovered.
- Inspector collapse and embedded Settings navigation responded in native
  WebView2. Collapse kept the background-task/restore controls visible. Full
  Preview restoration and screenshot-cessation instrumentation remain unverified.
- The native SVG pet was visibly round/centered while idle and reflected working
  and ready states. This proves the observed pose, not a complete hover/drag,
  keyboard, multi-DPI or native-glass acceptance.
- Explicit `Start Fairy voice` returned `VOICE_WORKER_IO_ERROR` without bringing
  the worker to ready. The adjacent text said `A CUDA GPU runtime is required`.
  However `scripts/check-voice-runtime.ps1` passed: Python 3.10.11,
  torch/torchaudio 2.7.0+cu128, CUDA available, TensorRT 10.13.3.9,
  onnxruntime-gpu 1.22.0 with CUDAExecutionProvider and no CPU distribution
  conflict. The existing worker log was not updated (last write 2026-08-09).
  The UI text is a fallback for missing device_name, not proof of missing CUDA;
  the actual host I/O failure still needs diagnostic detail.
- MiniCPM passive readiness recognized RTX 5060 Ti, a verified model layout and
  sufficient memory, but reported `Self-test failed / Installed · not tested`.
  Explicit Verify was started to obtain fresh runtime evidence (no microphone or
  screen capture consent was changed).

No production code or existing test assertions changed during this acceptance.
Real audio output, active-session protection and five-minute model unload cannot
be marked passed until a real model reaches ready and is exercised.

### Follow-up results and diagnosis

- Chat B used the existing manual/free Nemotron model. Turn
  `01a095b5-618c-7041-89a8-359ee543b6a9` failed in approximately one second with
  `PROVIDER_UNAVAILABLE` (two attempts classified `unavailable`). It reproduced
  the same permanently busy UI; neither A nor B produced a Fairy reply.
- For both exact test tasks, read-only `domain_events` inspection found only
  `assistant.turn.started`, with **no assistant.turn.failed event**. Code explains
  this path: `turn_lifecycle._fail_turn_in_unit` emits failure only when its
  command run remains RUNNING; the workflow settlement returns immediately for
  an already-terminal Turn. `useAssistantTurn` depends on the missing status
  event. Fix should establish one durable, idempotent terminal event for this
  preparation-failure path and add UI recovery coverage, not globally poll fast.
- MiniCPM explicit Verify completed by 13:04:28 UTC: native card showed `Ready`,
  `Self-test passed`, verified model layout, 14.9 GiB available / 9.8 GiB required.
  This passes the explicit self-test journey only; it is not a real session test.
- **Important: Settings → Start Realtime Companion fails before consent.** Native
  UI returned `Window is not authorized`, with no Companion window opened.
  `settings/client.ts` invokes `open_companion_window`; the Rust command uses
  `authorize_pet_input_window`, which permits only PET_INPUT_LABEL, not main.
  The fix must permit this specific navigation from main without giving pet
  surfaces general Core or privacy-control access. No guard was bypassed here.
- Voice runtime diagnosis excluded a missing CUDA stack and an unwritable log:
  a write-access handle to the existing log could be opened/closed with zero
  bytes written under the same Windows user as Fairy. The host's public I/O code
  still hides the exact failing operation. Do not reinstall CUDA on this evidence.
- Two fairy.exe processes were confirmed to belong to the same owned lifecycle:
  the second is a Core child, not a second user-started GUI. No unrelated process
  was stopped.

### Priorities for the next repair pass

1. P1: durable Turn terminal event and front-end recovery, including failure in
   interpretation before routing, both Auto and Manual, Settings hidden/return.
2. P1: typed/limited main-window authorization for opening Companion.
3. P1: identify Voice host I/O operation/OS error; remove the misleading CUDA
   fallback for a host launch failure; retest actual warm/play/stop.
4. P1 availability gate: inspect sanitized Provider protocol failure details and
   verify current model availability. Menu `Ready` is not proof a request works.
5. Repeat real sessions/audio and five-minute unload after these blockers clear;
   finish native interaction, DPI and glass coverage separately.

### Restart and cleanup gate

- The first owned Tauri dev lifecycle was stopped. A scoped OS process inspection
  confirmed no Fairy/Core/voice/realtime processes remained before the second
  launch. The second launch reused the existing debug build (0.46 s Cargo gate),
  not release/Docker, with the same formal data directory.
- After restart the main WebView2 showed `CORE READY`, ordinary Composer input,
  Auto model and expanded Inspector. Test A/B remained failed with respectively
  3/2 provider attempts: restart did not retry them or create a duplicate Turn.
- Opening Voice settings after restart showed MiniCPM `Ready / Self-test passed`
  without pressing Verify again. The explicit self-test evidence survived restart.
  Voice still showed `Stopped · ready to start`: no ordinary-startup prewarm.
- Full native voice/Realtime sessions remain blocked by the failures above.
  No microphone permission, capture consent, credentials or security policies were
  changed. No actual five-minute model-residency claim is made.
- The second test-owned Tauri dev lifecycle was stopped after these observations.
  Final scoped process inspection found no Fairy/Core/voice/realtime processes;
  no Vite listener remained on 1430. Final GPU reading was 2300 MiB used /
  13751 MiB free. These are cleanup observations, not an idle-unload timing test.

## Validation scope

- `scripts/check-voice-runtime.ps1`: passed twice against the existing real GPU
  Python runtime; no dependency installation or runtime replacement performed.
- Read-only SQLite probes: both test Turn/Workflow terminal states, provider
  attempt categories/counts, missing terminal events and restart non-reexecution.
- Native Computer Use: real main WebView2, Settings, Inspector, Auto/Manual
  requests, explicit Voice start, MiniCPM Verify, Companion entry, restart.
- No code changed, so the already-recorded full unit/browser gates were not
  rerun or represented as new native evidence. No existing assertions modified.
- Remaining: successful provider/tool journey, actual audio/microphone/screen
  session, model idle-unload timing, complete pet hit-testing/drag/DPI/glass, and
  any real WSL/Cloud services. This acceptance is **not a release pass**.
