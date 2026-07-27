# Realtime Companion Beta Phase 1 Acceptance

Date: 2026-07-28

## Decision

Phase 1 is accepted as the hardware-readiness and governed model-management
foundation for Realtime Companion Beta.

The desktop now detects the supported Windows, CPU, DXGI, CUDA, storage, model,
runtime, and session-budget dimensions without starting an inference service.
It can install, resume, verify, quarantine, and remove the pinned MiniCPM-o 4.5
artifact set through bounded native commands. Local Beta remains fail-closed
until every hardware, model, runtime, and budget gate passes.

This acceptance does not claim a working Omni runtime, a completed model
download, local CUDA inference, live Voice or Realtime operation, or paid cloud
operation. The Omni runtime is a Phase 2 deliverable.

## Boundary review

- The selected hardware adapter must be a physical NVIDIA adapter with at least
  16 GiB dedicated VRAM, AVX2, a working CUDA driver, and matching DXGI/CUDA
  LUIDs.
- Ordinary startup and Settings navigation perform bounded readiness queries
  only. They do not download a model, hash the complete model, run the runtime
  self-test, initialize CUDA inference, or start Voice, Realtime, or Omni.
- The pinned manifest records the exact upstream revision, model files, sizes,
  SHA-256 digests, HTTPS origins, license, runtime revision, patch-set digest,
  and predicted peak VRAM.
- Installation is resumable and cancellable. Redirect count, content length,
  byte ranges, total size, path safety, and final digests are verified before
  atomic promotion.
- A partial, corrupt, or unexpected install cannot be reported as ready.
- The future runtime self-test is an offline, timeout-bounded,
  output-bounded, strict JSON subprocess with an exact identity check and no
  inherited credentials.
- Readiness and model mutation commands preserve the main/companion/pet
  authorization boundary. The companion may read readiness; only the main
  window may install, cancel, verify, or remove; the pet is denied.
- The Settings card distinguishes unsupported, installable, downloading,
  partial, corrupt, runtime-missing, self-test-failed, temporarily unavailable,
  and ready projections. Cloud Live remains selectable when Local Beta is
  unavailable.

## Automated evidence

All commands ran from their owning project directories.

- Desktop TypeScript: `npx tsc --noEmit` passed.
- Desktop Vitest: 94 files, 502 tests passed.
- Rust formatting: `cargo fmt --all -- --check` passed.
- Rust linting: `cargo clippy --workspace --all-targets -- -D warnings`
  passed.
- Rust workspace tests passed, including desktop, local worker, Realtime
  worker, Windows DDA, integration, and documentation tests. The credentialed
  live GLM test remained ignored as designed.
- Focused Realtime readiness Playwright: 9/9 passed.
- Complete desktop Playwright: 70/70 passed, including functional and
  performance projects. The performance project completed in about 1.7
  seconds in the shared controlled application lifecycle.

A pre-existing Core startup timing assertion exceeded its three-second budget
once while Rust compilation was under load. Its focused rerun completed in
2.22 seconds, and the subsequent complete Rust workspace run passed. No product
code was relaxed for that transient result.

## Native WebView2 evidence

A controlled Windows `npm run tauri -- dev` lifecycle was started, inspected,
and stopped:

- one user-visible native main window titled `Fairy` was created;
- the WebView2 rendered the existing chat, Conversation outline, Composer,
  Preview, and persistent workspace state;
- Settings opened inside the same main window and returned to the original
  workspace without horizontal overflow;
- the Voice page reported `Fairy Voice Worker` as
  `Stopped · starts on first playback`;
- opening Voice started no Voice, Realtime, Omni, CosyVoice, or model runtime
  process;
- the expected Core capability and `--local-worker` process tree remained
  active during the check.

The native hardware report on this machine was:

- adapter: NVIDIA GeForce RTX 5060 Ti;
- reported physical dedicated VRAM: 15.7 GiB;
- CUDA driver API version: 13030;
- DXGI/CUDA adapter identity: matched;
- model: 6.3 GiB artifact set not installed;
- runtime: bundled runtime missing;
- session budget: unknown until an explicit refresh.

Because the reported dedicated VRAM is below the strict 16 GiB product gate,
and because neither the model nor Phase 2 runtime is installed, the native card
correctly displayed `Unavailable`. This is evidence of fail-closed behavior,
not a GPU qualification or a claim that local inference works on this device.

The second native run exposed a loopback-only WebView2 debugging port so the
Settings page could be inspected through CDP without taking keyboard or mouse
focus from the user. The exact controlled process tree was then terminated.
Final inspection found no remaining controlled Fairy process and no listener
on ports 1430, 1431, or 9223.

## Explicitly deferred gates

The following were not run in Phase 1:

- Docker or PostgreSQL integration environments;
- Tauri release, installer, production image, or production bundle builds;
- real MiniCPM-o 4.5 download;
- real Omni runtime or CUDA model inference;
- live microphone, screen, application-audio, Voice, or Realtime sessions;
- paid Gemini or GLM cloud sessions;
- production multi-display, game-impact, and long-duration soak tests.

These omissions are intentional. Phase 2 must supply and independently certify
the local Omni sidecar before any Local Beta readiness claim can become true.
