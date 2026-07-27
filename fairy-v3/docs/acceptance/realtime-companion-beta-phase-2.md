# Realtime Companion Beta Phase 2 Acceptance

Date: 2026-07-28

## Decision

Phase 2 is accepted as the bounded Omni sidecar and desktop-supervision
foundation for Realtime Companion Beta.

Fairy now pins the official `tc-mb/llama.cpp-omni` source revision and build
toolchain, applies a digest-locked patch set, builds a strict local control
runtime, transports media through bounded Windows named pipes, and supervises
the sidecar from the desktop. The runtime has no HTTP service, network client,
tool access, direct Core or database access, temporary raw-media output, or TTS
path. Ordinary Fairy startup does not start Omni.

This acceptance does not claim that a production CUDA runtime was compiled or
that MiniCPM-o 4.5 inference ran on this machine. The CUDA Toolkit and verified
model files were absent, so the production compile and model-inference gates
are explicitly deferred. Desktop readiness consequently remains fail-closed
and Local Beta remains unavailable.

## Boundary review

- The official upstream source is locked to revision
  `74699a53df6ca0f4947ff37066f851532c20b12d`.
- The CMake 4.4.0 toolchain archive and the three-file upstream patch set are
  digest-pinned. The patches apply cleanly to the exact upstream revision.
- The control protocol is strict 4-byte little-endian length-prefixed JSON with
  a 256 KiB frame limit and exact identity checks.
- Binary audio, image, and video data travels through bounded named-pipe media
  frames rather than JSON, HTTP, or temporary files.
- The production adapter disables upstream HTTP serving, network access, tool
  calls, TTS, and disk media output. It exposes only the bounded decision
  surface required by the desktop supervisor.
- The desktop launches with a cleared environment, hidden window, inherited
  Windows job object, a 90-second model-load deadline, and a five-second stop
  deadline. One pre-candidate restart is allowed; repeated crashes quarantine
  the runtime until explicit verification.
- Desktop readiness accepts only a schema-2 `production-cuda` report with CUDA
  and backend readiness true and a ready model probe. Contract and CPU
  compatibility artifacts cannot unlock Local Beta.
- The production staging command fails closed without a verified model root.
  Ordinary `npm run dev` remains lazy and does not build or start Omni.
- Vite dependency discovery is restricted to the desktop `index.html`; native
  source and build trees are excluded from frontend scanning and watching.

## Automated evidence

All commands ran from their owning project directories.

- Source lock, patch digest, and clean-apply verification passed.
- The contract C++ runtime built successfully. CTest passed 2/2 and the
  control-process integration suite passed.
- The `upstream-cpu` compatibility profile compiled through MSVC and passed the
  backend boundary tests. Its self-test correctly reported
  `cuda: false`, `backend_ready: false`, and
  `model_probe: cpu_compatibility_only`.
- Desktop TypeScript: `npx tsc --noEmit` passed.
- Desktop Vitest: 94 files, 502 tests passed.
- Rust formatting: `cargo fmt --all -- --check` passed.
- Rust linting: `cargo clippy --workspace --all-targets -- -D warnings`
  passed.
- Rust workspace tests passed, including 213 desktop-library tests and all
  integration and documentation tests. The credentialed live GLM test remained
  ignored as designed.
- Focused Realtime readiness Playwright: 9/9 passed.
- Complete desktop Playwright: 70/70 passed. The functional and performance
  projects shared one controlled application lifecycle; the performance test
  completed in about 1.3 seconds.
- Contract staging succeeded and recorded a non-production contract profile.
  Production staging without a model root failed as required and did not
  replace the contract marker.

The first complete Rust run exceeded a three-second Core startup assertion once
under concurrent compilation load, completing in 3.22 seconds. The isolated
rerun passed in 1.84 seconds and the subsequent complete workspace run passed.
No timeout or product behavior was relaxed.

The upstream CPU build emitted narrowing-conversion warnings and a pre-existing
recursive `token2wav` warning. The Fairy adapter compiled successfully, and no
warning was reclassified as evidence of production readiness.

## Native WebView2 evidence

A controlled Windows Tauri dev lifecycle was started, inspected, and stopped:

- exactly one main `fairy.exe` process and one expected `--local-worker`
  process were active;
- the capability bridge was active for Core operation;
- Omni process count remained zero;
- Voice Worker and CosyVoice process count remained zero;
- the staged contract artifact was reported as `on-demand only`;
- after restricting Vite discovery to the shell entry, a second run produced
  no native-source dependency scan, hot reload, or transient IPC callback
  warnings;
- shutdown removed the project process tree and the listener on port 1430.

The first native run revealed that Vite's default dependency discovery entered
the temporary upstream source tree. That caused one development-only hot reload
and transient IPC callback warnings. The configuration was corrected and the
controlled native run was repeated successfully.

## Explicitly deferred gates

The following were not run in Phase 2:

- production CUDA compilation, because no CUDA Toolkit was installed;
- MiniCPM-o 4.5 model loading or inference, because no verified model artifact
  set was installed;
- GPU qualification, VRAM pressure, token-latency, or long-duration Omni soak
  tests;
- live microphone, screen, application-audio, Voice, or paid cloud sessions;
- Docker or PostgreSQL integration environments;
- Tauri release, installer, production image, or production bundle builds.

These omissions are intentional and fail closed. Phase 3 may consume only the
certified supervisor boundary; it must not infer production local-inference
availability from the contract or CPU compatibility profiles.
