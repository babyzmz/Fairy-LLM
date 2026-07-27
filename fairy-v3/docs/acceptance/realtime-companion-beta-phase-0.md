# Realtime Companion Beta Phase 0 Acceptance

Date: 2026-07-27

## Decision

Phase 0 is accepted as the contract and governance baseline for Realtime Companion Beta. The
backend-neutral protocol, Core-owned Persona Snapshot, local-readiness declarations, desktop
preference migration, session ownership reducer, and worker protocol are implemented and covered
by the repository gates below.

This acceptance does not claim that the local MiniCPM-o 4.5 runtime, CUDA inference, production
voice output, or a paid live cloud session is implemented or verified. Those are later-phase
gates.

## Boundary review

- Worker protocol is `fairy-realtime-worker-v2` and carries `session_id`, `segment_id`, and
  `context_epoch` identities.
- The desktop fetches `realtime.persona.snapshot` from Core before a governed cloud start. The
  worker has no fallback `companion_instruction`.
- Secret-bearing command debug output redacts both the credential and Persona Snapshot.
- Local backend selection fails closed with `LOCAL_BACKEND_NOT_IMPLEMENTED`; the UI does not
  present local Realtime as ready.
- Realtime Beta defaults to disabled and must be explicitly enabled before start.
- Schema 9 owns current Realtime preferences. `realtime_provider` and `realtime_voice_mode` remain
  only in the bounded v8-to-v9 migration projection and its tests.
- The secondary native window title is `Fairy Realtime Companion`; historical acceptance evidence
  was updated to the same title.
- App audio is an explicit per-session choice. Startup does not prewarm a Voice, Realtime, Omni,
  CUDA, or model worker.

## Automated evidence

All commands ran from their owning project directories.

- Desktop TypeScript: `npx tsc --noEmit` passed.
- Desktop Vitest: 93 files, 490 tests passed.
- Core: 850 tests passed.
- Core boundary and style checks: 25 focused boundary tests passed; Ruff check and format passed.
- Rust formatting: `cargo fmt --all -- --check` passed.
- Rust linting: `cargo clippy --workspace --all-targets -- -D warnings` passed.
- Rust workspace tests passed:
  - desktop: 149 unit tests;
  - Realtime worker: 35 tests;
  - all integration and documentation tests;
  - the credentialed live GLM test remained ignored as designed.
- Playwright focused startup stability: 9/9 repeated Presence scale cases passed.
- Playwright complete suite: 61/61 passed, including functional and performance projects.

Two load-sensitive readiness assertions were corrected during acceptance:

- durable recovery trace visibility now allows the bounded asynchronous recovery path to settle;
- the first Presence canvas allows 10 seconds under the eight-worker full-suite load. The final
  full run observed an 8.1-second first frame at 100% scale and completed successfully.

## Native WebView2 evidence

A controlled `npm run tauri -- dev` lifecycle was started and stopped on Windows:

- exactly one user-visible native window titled `Fairy` was returned;
- the main WebView2 rendered the durable workspace with `CORE READY`;
- History, the left Conversation outline, persisted messages, Composer, and Preview were present;
- Settings was exposed as a main-workspace navigation action; no independent Settings window was
  present;
- the persisted desktop preferences were Schema 9 with Realtime Beta disabled, backend `auto`,
  Presence maximum 240 minutes, cloud daily limit 30 minutes, and local keep-warm 10 minutes;
- the runtime tree contained the expected desktop, Core capability, and local-worker processes;
- no Realtime worker, Voice/CosyVoice worker, Omni worker, CUDA runtime, or local model service was
  started.

User input was detected before an automated Settings click, so native automation stopped competing
for the window. The current page was inspected read-only instead. The in-main-window Settings
transition remains covered by the complete Vitest and Playwright suites; this run does not claim a
separate automated native click-through.

The exact controlled process tree was terminated. Final inspection found no Fairy V3 process and no
listener on ports 1430 or 1431. Playwright results and temporary native-smoke traces were removed
after evidence was recorded.

## Explicitly deferred gates

The following were not run in Phase 0:

- Docker or PostgreSQL integration environments;
- Tauri release, installer, production image, or production bundle builds;
- real local MiniCPM-o 4.5 model download or inference;
- real CUDA/GPU, DXGI capture-to-model, Voice/CosyVoice, Omni, or paid cloud session tests;
- production multi-display and long-duration soak tests.

These omissions are intentional and must remain visible in later Phase 1–8 acceptance records.
