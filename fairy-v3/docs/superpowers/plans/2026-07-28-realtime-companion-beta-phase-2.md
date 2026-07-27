# Realtime Companion Beta Phase 2 Implementation Plan

> Execute from `D:\桌面\~\deskllmchat\fairy-v3`. Preserve the untracked
> repository-level `CLAUDE.md`. Do not run Docker, a Tauri release build, or a
> production installer in this phase.

## Goal

Deliver a reproducible, offline, crash-isolated `fairy-omni-runtime.exe`
boundary with strict control/media protocols and a pinned patched
llama.cpp-omni production adapter. Keep Local Beta unavailable unless a genuine
production CUDA build and model probe pass.

## Task 1: Lock the upstream source and development toolchain

Files:

- add `desktop/native/omni-runtime/upstream.lock.json`
- add `desktop/native/omni-runtime/scripts/sync-upstream.ps1`
- add `desktop/native/omni-runtime/scripts/bootstrap-cmake.ps1`
- add `desktop/native/omni-runtime/scripts/verify-patches.ps1`
- modify `.gitignore`
- modify `docs/third-party/minicpm-o-4.5-model-manifest.md`

Steps:

1. Pin the official repository and exact revision already recorded by Phase 1.
2. Pin the official portable CMake archive and SHA-256 used by the local build
   bootstrap; do not add it to Git.
3. Fetch into `_deps`, detach HEAD, reject a dirty or wrong revision, and
   prevent branch-following.
4. Define an ordered patch manifest and combined digest algorithm.
5. Test wrong revisions, dirty dependency trees, digest mismatches, and path
   escapes with a local fake Git origin.

Gate:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File desktop/native/omni-runtime/scripts/verify-patches.ps1 -ContractOnly
```

Commit:

```text
build(omni): pin runtime source and toolchain
```

## Task 2: Implement the C++ control protocol and contract executable

Files:

- add `desktop/native/omni-runtime/CMakeLists.txt`
- add `desktop/native/omni-runtime/CMakePresets.json`
- add `desktop/native/omni-runtime/include/fairy_omni/control.hpp`
- add `desktop/native/omni-runtime/include/fairy_omni/identity.hpp`
- add `desktop/native/omni-runtime/src/control.cpp`
- add `desktop/native/omni-runtime/src/self_test.cpp`
- add `desktop/native/omni-runtime/src/main.cpp`
- add `desktop/native/omni-runtime/tests/control_tests.cpp`

Steps:

1. Implement four-byte little-endian length-prefixed strict JSON with a 256
   KiB ceiling.
2. Freeze session, segment, context epoch, and sequence validation.
3. Add hello, load, context lifecycle, media commit, cancellation, stop, and
   ping commands.
4. Add ready, progress, model/context state, decision, pressure, diagnostic,
   stopped, and pong events.
5. Add the contract build profile and self-test identity. It must report
   `backend_ready: false`.
6. Reserve stdout for frames and stderr for bounded diagnostics.

Gate:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File desktop/native/omni-runtime/scripts/build.ps1 -Profile contract -Test
```

Commit:

```text
feat(omni): add bounded control runtime
```

## Task 3: Implement the binary named-pipe media channel

Files:

- add `desktop/native/omni-runtime/include/fairy_omni/media.hpp`
- add `desktop/native/omni-runtime/src/media.cpp`
- add `desktop/native/omni-runtime/src/media_pipe_windows.cpp`
- add `desktop/native/omni-runtime/tests/media_tests.cpp`
- add `desktop/src-tauri/src/omni_media_protocol.rs`

Steps:

1. Implement the fixed 36-byte FOMI header in C++ and Rust.
2. Validate magic, version, kind, epoch, monotonic sequence, timestamp, format,
   and length before allocation.
3. Enforce exact 20 ms microphone packets, bounded application-audio packets,
   bounded JPEG/BGRA frames, a latest-video slot, bounded audio rings, and a 48
   MiB aggregate ceiling.
4. Create the host-owned Windows named pipe with first-instance and
   reject-remote-client flags; verify peer PIDs.
5. Prove that no media path or temporary file is created.

Gates:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File desktop/native/omni-runtime/scripts/build.ps1 -Profile contract -Test
cargo test -p fairy-desktop-v3 omni_media
```

Commit:

```text
feat(omni): stream bounded binary media
```

## Task 4: Patch and bind the pinned llama.cpp-omni backend

Files:

- add `desktop/native/omni-runtime/patches/0001-memory-backed-duplex-media.patch`
- add `desktop/native/omni-runtime/patches/0002-memory-only-output-policy.patch`
- add `desktop/native/omni-runtime/patches/0003-bounded-decision-result.patch`
- add `desktop/native/omni-runtime/include/fairy_omni/backend.hpp`
- add `desktop/native/omni-runtime/src/backend_contract.cpp`
- add `desktop/native/omni-runtime/src/backend_upstream.cpp`
- modify `desktop/native/omni-runtime/CMakeLists.txt`
- modify `desktop/src-tauri/resources/omni/minicpm-o-4.5.json`
- modify `desktop/src-tauri/src/omni_model_manifest.rs`
- modify `docs/third-party/minicpm-o-4.5-model-manifest.md`

Steps:

1. Add memory-backed PCM/JPEG input to the reviewed upstream duplex pipeline.
2. Disable TTS, Python Token2Wav, diagnostic directories, WAV/token/hidden
   dumps, and other raw-media writes in Fairy's production path.
3. Expose a bounded LISTEN/SPEAK text result.
4. Load only the pinned LLM, VPM, and APM files with CUDA layers enabled.
5. Generate the ordered patch stack from the exact upstream revision and
   update both patch-set and canonical manifest digests.
6. Run clean-apply and static no-network/no-media-file gates.
7. Configure a CPU source-compatibility build if CUDA is unavailable; never
   label it production-ready.

Gates:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File desktop/native/omni-runtime/scripts/verify-patches.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File desktop/native/omni-runtime/scripts/build.ps1 -Profile upstream-cpu -Test
```

Commit:

```text
feat(omni): bind pinned minicpm runtime
```

## Task 5: Add the host supervisor and schema-2 self-test

Files:

- add `desktop/src-tauri/src/omni_runtime_protocol.rs`
- add `desktop/src-tauri/src/omni_runtime_manager.rs`
- modify `desktop/src-tauri/src/omni_runtime_self_test.rs`
- modify `desktop/src-tauri/src/local_readiness.rs`
- modify `desktop/src-tauri/src/lib.rs`

Steps:

1. Spawn with a cleared environment, null stdin for self-test, bounded
   stdout/stderr, no console window, and inherited kill-on-close Job Object.
2. Validate self-test schema 2, upstream revision, patch digest, build profile,
   CUDA feature, backend readiness, and model probe.
3. Keep contract and CPU builds fail-closed.
4. Add load/stop deadlines, one pre-candidate restart, repeated-crash
   quarantine, and explicit verify recovery.
5. Do not connect the runtime to user Realtime start yet; Phase 3 owns backend
   selection.

Gate:

```powershell
cargo fmt --all -- --check
cargo test -p fairy-desktop-v3 omni_runtime
cargo clippy -p fairy-desktop-v3 --all-targets -- -D warnings
```

Commit:

```text
feat(desktop): supervise omni runtime
```

## Task 6: Wire development and package inputs without release-building

Files:

- add `scripts/build-omni-sidecar.ps1`
- modify `desktop/package.json`
- modify `desktop/src-tauri/tauri.conf.json`
- modify `desktop/src-tauri/build.rs`
- modify `scripts/start-desktop.ps1`

Steps:

1. Add explicit contract and production build entry points.
2. Stage the runtime under `runtime/omni/fairy-omni-runtime.exe` only after its
   declared profile passes self-test.
3. Keep ordinary `npm run dev` lazy: it must not build, load, or start Omni.
4. Add the production runtime as a Tauri resource input while keeping release
   builds out of this phase.
5. Ensure a missing production artifact yields `runtime_missing`, never a
   placeholder-ready state.

Gates:

```powershell
npm run build:omni-sidecar -- --profile contract
npm run dev
```

Commit:

```text
build(desktop): stage omni runtime
```

## Task 7: Certify Phase 2

Files:

- add `docs/acceptance/realtime-companion-beta-phase-2.md`
- add or modify focused Rust/C++/Playwright boundary tests as required

Steps:

1. Run the contract executable control and media tests.
2. Run source-lock, patch clean-apply, patch digest, no-network, and
   no-temporary-media checks.
3. Run TypeScript, complete Vitest, Rust format/lint/workspace tests, focused
   Playwright, and complete Playwright.
4. Run native Tauri dev without starting Omni on ordinary startup or Settings
   navigation.
5. If CUDA Toolkit and verified model files are absent, record production
   compile/model inference as explicitly deferred and confirm Local Beta stays
   unavailable.
6. Remove `_deps`, build output, screenshots, traces, and controlled
   processes.

Commit:

```text
test(realtime): certify phase two sidecar
```

## Exit criteria

- The Phase 2 design is implemented and documented.
- The contract runtime builds and passes on Windows.
- The production adapter applies cleanly to the exact pinned upstream.
- No runtime path provides HTTP, networking, tools, temporary raw media, TTS,
  or direct Core/database access.
- Desktop readiness accepts only a schema-2 production CUDA report.
- Ordinary Fairy startup starts no Omni process.
- The working tree is clean except for the preserved repository-level
  `CLAUDE.md`.
