# Realtime Omni Portable CUDA Build Implementation Plan

> **Design:** `docs/superpowers/specs/2026-07-28-realtime-omni-portable-cuda-build-design.md`
>
> **Authority:** the user-approved Realtime Companion Beta final design,
> frozen Phase 0 contracts, and the approved portable CUDA Option A.

## Goal

Make Fairy's `production-cuda` Omni runtime reproducibly buildable on an
eligible Windows x64 device without administrator installation of the NVIDIA
Visual Studio integration, stage every required CUDA runtime component, and
produce a truthful MSI candidate without bundling model weights.

## Non-goals

This work does not change the Omni protocol, add CPU fallback, start workers
eagerly, bundle MiniCPM model weights, weaken model verification, install a
display driver, change machine-wide environment state, create signing
credentials, or convert an incomplete Phase 8 gate into a public-release pass.

## Task 1: Lock the portable CUDA toolchain contract

**Files**

- Modify `desktop/native/omni-runtime/upstream.lock.json`
- Add `desktop/native/omni-runtime/scripts/cuda-toolchain.ps1`
- Add `desktop/native/omni-runtime/scripts/cuda-toolchain.tests.ps1`

**Implementation**

1. Extend the existing toolchain lock with exact micromamba, Ninja, CUDA
   channel/label, compiler, cuBLAS, architecture, license, and runtime DLL
   identities.
2. Keep CMake 4.4.0 and minimum MSVC 19.44 authoritative.
3. Implement strict lock parsing and ASCII-safe user cache resolution.
4. Validate an explicit toolchain root without allowing it to bypass pinned
   versions, files, architecture, or license identity.
5. Implement clean-cache bootstrap through HTTPS into a temporary location,
   verify the micromamba archive digest, create the environment, then move it
   into the governed cache.
6. Keep all environment changes process-local and reject UAC/system-install
   behavior.

**Tests**

- good explicit fixture is accepted;
- non-ASCII cache, bad schema, wrong versions, missing `nvcc`, Ninja, headers,
  import library, or DLLs are rejected;
- production bootstrap inputs are exact and contract/CPU paths never call it;
- temporary/incomplete cache cannot be mistaken for ready.

## Task 2: Switch only production CUDA to Ninja Multi-Config

**Files**

- Modify `desktop/native/omni-runtime/CMakePresets.json`
- Modify `desktop/native/omni-runtime/scripts/build.ps1`
- Add or extend focused script tests

**Implementation**

1. Change only `production-cuda` to `Ninja Multi-Config`.
2. Preserve `out/production-cuda/Release/fairy-omni-runtime.exe`.
3. Discover Visual Studio with `vswhere` or the standard installation and
   import `VsDevCmd.bat -arch=x64 -host_arch=x64` into the current process.
4. Resolve the locked CUDA toolchain, add only its required executable and
   DLL directories to the current process, and set `CUDAToolkit_ROOT`.
5. Continue using the digest-pinned CMake 4.4.0 and patched upstream tree.
6. Run existing backend-boundary checks and return structured build metadata
   in addition to the executable path without breaking the staging caller.

**Tests**

- contract/upstream-cpu presets remain Visual Studio x64;
- production uses Ninja Multi-Config and Release;
- missing MSVC, CUDA, or Ninja fails before configuration;
- contract build remains offline and CUDA-independent;
- existing contract CMake/control tests still pass.

## Task 3: Stage the complete governed Omni runtime

**Files**

- Modify `scripts/build-omni-sidecar.ps1`
- Add `scripts/test-omni-runtime-staging.ps1`
- Modify `.gitignore`
- Modify `desktop/src-tauri/build.rs`

**Implementation**

1. Validate schema-2 self-test identity and existing model-free/explicit-model
   policies before staging.
2. Inspect the locked toolchain output and stage:
   `fairy-omni-runtime.exe`, `cublas64_13.dll`,
   `cublasLt64_13.dll`, `build-profile.txt`, and
   `runtime-components.json`.
3. Write exact relative names, sizes, SHA-256 digests, package versions, and
   license identifiers to the component manifest.
4. Prepare all files under one temporary staging directory, validate it, then
   replace governed stage files without exposing a partial new stage.
5. Reject missing, duplicate, stale governed, or unknown runtime files.
6. Make release `build.rs` verify the complete component manifest and hashes;
   debug startup behavior stays unchanged.
7. Ignore generated staging bytes while retaining `.gitkeep`.

**Tests**

- complete controlled stage succeeds;
- missing DLL, hash mismatch, wrong profile, unknown file, and interrupted
  temporary stage fail closed;
- no GGUF/model/credential path can enter the stage;
- old verified stage is preserved when preparation fails.

## Task 4: Package and disclose NVIDIA runtime bytes

**Files**

- Modify `THIRD_PARTY_NOTICES.md`
- Add `desktop/src-tauri/resources/legal/NVIDIA-CUDA-EULA.txt`
- Modify `scripts/check-release-documents.py`
- Modify `scripts/check_release_bundle.py`
- Modify `scripts/test_release_bundle.py`

**Implementation**

1. Add the applicable official NVIDIA CUDA/cuBLAS redistribution agreement
   text or approved upstream notice to packaged legal resources.
2. Record exact shipped CUDA/cuBLAS versions, license identifier, and
   distribution status in the third-party notice.
3. Require the legal resource, two runtime DLLs, component manifest, and Omni
   executable whenever an Omni runtime is present.
4. Validate the component manifest against bundle bytes and reject extra
   governed CUDA runtime files.
5. Preserve the model-weight and sensitive-content exclusions.

**Tests**

- controlled complete bundle passes;
- each missing runtime/legal item fails;
- altered DLL/hash or unknown governed DLL fails;
- bundles without an Omni runtime retain existing policy behavior.

## Task 5: Integrate deterministic gates

**Files**

- Modify `scripts/test-all.ps1`
- Modify focused scripts only where their public outputs must remain bounded

**Implementation**

1. Add CUDA lock/toolchain fixture tests without downloading packages.
2. Add Omni stage and release-bundle fixtures to the normal non-Docker gate.
3. Keep the real CUDA build opt-in so ordinary test runs do not download a
   toolchain or spend minutes compiling upstream.
4. Preserve strict-mode PowerShell, Ruff formatting, boundary checks, Rust,
   Vitest, and Playwright behavior.

**Verification**

- focused PowerShell/Python fixtures;
- `git diff --check`;
- contract Omni CMake build and tests;
- `scripts/test-all.ps1 -SkipDocker`.

**Commit**

`build(desktop): add portable omni cuda toolchain`

## Task 6: Certify the real production build and MSI input

**Generated inputs only**

- user-writable ASCII-safe CUDA cache;
- `desktop/native/omni-runtime/out/production-cuda`
- `desktop/src-tauri/runtime/omni`
- Tauri MSI/cabinet output and bounded evidence directory.

**Execution**

1. Remove or isolate the prior diagnostic build output without touching user
   files.
2. Bootstrap from a clean governed cache without administrator rights.
3. Build `production-cuda` with the repository command.
4. Run model-free fail-closed self-test and dependency inspection.
5. Stage the verified runtime, build the production MSI, and collect the
   external-cabinet media manifest.
6. Run bundle composition, Defender malware scan, and all feasible unsigned
   installer lifecycle checks.
7. Record exact hashes and update the Phase 8 acceptance truthfully.

**Real commands**

- `npm run build:omni-sidecar:production`
- `npm run tauri build`
- `powershell -File scripts/collect-wix-media.ps1 ...`
- `powershell -File scripts/test-presence-install-cycle.ps1 ...`
- `powershell -File scripts/test-realtime-companion-beta-release.ps1 ...`

Signing remains blocked without a release certificate. Model inference,
four-hour soak, reference game performance, and native privacy gates proceed
only after the exact verified MiniCPM model is installed.

## Completion

The implementation commit is accepted only after deterministic regressions
and a clean-cache real CUDA build pass. Phase 8 remains truthful: a generated
unsigned MSI is an internal candidate, not public-release approval, until
every existing real-environment gate passes.
