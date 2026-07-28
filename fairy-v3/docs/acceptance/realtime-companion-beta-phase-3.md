# Realtime Companion Beta Phase 3 Acceptance

Date: 2026-07-28

## Decision

Phase 3 is accepted as the governed Realtime backend-abstraction layer for
Fairy V3.

Cloud Live and Local MiniCPM now implement one internal Worker backend
contract. Tauri is the sole authority for resolving `Auto`, validating current
readiness and consent, creating Backend Segments, and fencing Worker events by
Session, Segment, and Context Epoch. React renders the resulting projection
and cannot select or forge an execution backend.

This acceptance does not claim production CUDA compilation, installed
MiniCPM-o 4.5 artifacts, local model inference, or a live paid provider
session. Those gates were unavailable and remain fail-closed.

## Boundary review

- Cloud provider sockets are owned only by `CloudLiveBackend`; a Local launch
  cannot construct a provider transport or accept a cloud credential.
- Local Omni is launched only on demand with a cleared environment, hidden
  window, inherited job, verified managed paths, parent-owned named pipe,
  process-ID verification, bounded frames, and bounded deadlines.
- Contract and CPU compatibility runtimes cannot report production readiness.
- Resolver preview is read-only. Start reruns the resolver and rejects stale or
  renderer-forged resolution tokens.
- `Auto` selects Local only when all local hardware, model, runtime, voice, and
  budget gates pass. Cloud fallback additionally requires the explicit
  preference, configured provider, and this session's upload consent.
- A backend failure disables media and requires an explicit continuation. It
  never silently moves media or credentials to another backend.
- Each approved continuation creates a new Tauri-owned Backend Segment.
  Results from stale Segment or Context Epoch identities are dropped before
  renderer projection.
- Core records the historical backend and pinned model identity but does not
  resolve or execute a backend.
- The Companion shows the exact resolved/active backend and distinct Local
  processing versus Cloud upload privacy scope. It does not receive
  credentials, Persona bodies, model paths, or raw media.

## Automated evidence

All commands ran from their owning project directories.

- Omni source-lock tests passed for the pinned upstream revision
  `74699a53df6ca0f4947ff37066f851532c20b12d`.
- All five digest-locked patches applied cleanly. The combined patch-set
  digest was
  `b2a095f49fb5d48c587505673b0549a50ee6bd4717066c7a9e562eb5c031d29a`.
- Omni backend boundary checks passed.
- The contract runtime built successfully. CTest passed 2/2 and the strict
  control/media process integration suite passed.
- The full `upstream-cpu` compatibility profile compiled successfully and
  passed the backend boundary checks.
- Realtime Worker tests passed 38/38. The credentialed GLM live test remained
  ignored as designed.
- Rust formatting and
  `cargo clippy --workspace --all-targets -- -D warnings` passed.
- The complete Rust workspace passed, including 219 Desktop library tests and
  all integration and documentation tests.
- Core passed 852/852 tests.
- Desktop TypeScript passed with `npx tsc --noEmit`.
- Desktop Vitest passed 94 files and 503 tests.
- Focused Realtime readiness and backend-projection Playwright passed 11/11 at
  880x680 and 640x700.
- Complete Desktop Playwright passed 72/72. Functional and performance
  projects shared one controlled lifecycle; the performance case completed in
  about 1.2 seconds.

The fixed upstream CPU build emitted upstream narrowing-conversion warnings and
the existing `token2wav` recursion warning. The Fairy adapter and executable
linked successfully. These warnings are not treated as production CUDA or
model-readiness evidence.

## Native WebView2 evidence

A controlled Windows Tauri dev lifecycle was started, inspected through a
loopback-only CDP endpoint without taking keyboard or mouse control, and then
stopped:

- one main Fairy WebView and the expected pet Render/Input WebViews existed;
  there was no independent Settings WebView;
- the main WebView rendered at 1440x900 with no horizontal overflow;
- Settings opened inside the main WebView while Workspace stayed connected and
  hidden, then returned to the same Composer and Workspace;
- the Voice page reported `Stopped · starts on first playback`;
- the Local readiness projection remained fail-closed on this machine:
  15.7 GiB physical VRAM, model not installed, runtime not explicitly tested,
  and session budget not refreshed;
- a read-only backend preview returned `REALTIME_BETA_DISABLED` with no backend
  because the user's Beta preference was off;
- the active process tree contained the main Desktop, Core, and one expected
  `--local-worker`; Omni, Realtime Worker, Voice Worker, and CosyVoice counts
  remained zero.

Shutdown removed the complete controlled process tree and listeners on ports
1430, 1431, and 9223.

## Explicitly deferred gates

The following were not run:

- production CUDA compilation, because no qualifying production toolchain gate
  was established for this phase;
- MiniCPM-o 4.5 model loading or inference, because the verified model set was
  not installed;
- live microphone, selected-window, application-audio, or long-duration local
  session tests;
- live Gemini or GLM provider sessions and paid traffic;
- GPU pressure, latency, thermal, game-impact, multi-display, and soak tests;
- Docker, PostgreSQL integration environments, Tauri release, installer,
  production image, or production bundle builds.

These omissions remain explicit and fail closed. Phase 4 may consume only the
certified Backend Segment and public projection contracts.
