# Realtime Omni Portable CUDA Build Design

Date: 2026-07-28

## Authority

This design completes the production CUDA build path required by the
user-approved Realtime Companion Beta Phase 2 and Phase 8 designs. It does not
change the frozen Phase 0 contracts, the Omni protocol, model identity,
readiness semantics, or release evidence thresholds.

The user approved Option A: use Ninja Multi-Config for `production-cuda`,
bootstrap a pinned non-admin CUDA build toolchain, and preserve the existing
Release output and fail-closed packaging interfaces.

## Problem

The existing `production-cuda` CMake preset uses the Visual Studio generator.
That generator requires NVIDIA's system-wide Visual Studio CUDA integration.
On an otherwise eligible Windows device, installing that integration requires
administrator approval and makes the build depend on mutable machine state.

Fairy's existing build policy correctly rejects a production runtime when no
CUDA toolset is available. The missing system integration therefore blocks
the MSI even when a real CUDA compiler and libraries are available from
NVIDIA's supported package channel.

The production executable also dynamically depends on `cublas64_13.dll`, which
in turn depends on `cublasLt64_13.dll`. Staging only the executable would
produce an installer that passes composition checks but cannot start on a
clean device.

## Verified feasibility

A diagnostic build was performed before changing repository configuration:

- NVIDIA RTX 5060 Ti with 16,311 MiB dedicated memory;
- NVIDIA display driver 610.74;
- MSVC 19.44 from Visual Studio 2022 Community;
- NVIDIA CUDA compiler 13.0.88 from the pinned `cuda-13.0.3` Conda label;
- CMake 3.31.8 and Ninja 1.13.2;
- `Ninja Multi-Config`, Release configuration;
- all 380 compile/link steps completed;
- the resulting 37.13 MiB executable returned schema 2,
  `build_profile=production-cuda`, `cuda_compiled=true`,
  `backend_ready=false`, and `model_probe=model_files_invalid`;
- the model-free self-test exited successfully without claiming backend
  readiness.

The executable directly requires `cublas64_13.dll`; that library requires
`cublasLt64_13.dll`. The verified files are approximately 49.17 MiB and
458.14 MiB respectively before cabinet compression.

This evidence proves build-path feasibility only. It is not model inference,
an installer test, a four-hour soak, or public-release approval.

## Decision

Only the `production-cuda` configure preset changes to
`Ninja Multi-Config`. The `contract` and `upstream-cpu` presets retain their
Visual Studio generator so their established test and compatibility behavior
does not change.

The production build script owns toolchain discovery and preparation:

1. locate the existing MSVC x64 command-line environment;
2. use an explicitly configured compatible CUDA toolkit when supplied;
3. otherwise bootstrap the pinned NVIDIA CUDA build packages into a
   user-writable cache;
4. use the repository-pinned CMake and Ninja executables;
5. configure and build the unchanged `production-cuda` CMake profile;
6. run the existing backend boundary tests and schema-2 self-test;
7. stage the executable and the exact required CUDA redistributable DLLs.

The output executable remains:

`out/production-cuda/Release/fairy-omni-runtime.exe`

The staged runtime remains:

`desktop/src-tauri/runtime/omni`

No Core, RPC, database, model-catalog, worker-protocol, or public UI contract
changes.

## Toolchain pinning and provenance

The build uses a checked-in manifest that records:

- CUDA package channel and immutable label;
- exact package names and versions;
- supported host architecture;
- micromamba version and archive SHA-256;
- required CMake and Ninja versions;
- redistributable DLL names;
- expected license identifier;
- optional known hashes for downloaded bootstrap artifacts where the upstream
  distribution provides stable bytes.

The initial authoritative pins are:

- existing repository CMake `4.4.0`;
- micromamba `2.4.0`;
- Ninja `1.13.2`;
- NVIDIA `cuda-compiler` from label `cuda-13.0.3`;
- NVIDIA `libcublas` and `libcublas-dev` `13.1.1.3`.

The diagnostic CMake `3.31.8` proved generator feasibility only. Production
continues to use the repository's digest-pinned CMake `4.4.0`.

The bootstrap process downloads only from the declared HTTPS origins and
refuses an unexpected package identity, version, architecture, missing file,
or license identifier. It never installs system drivers, writes the registry,
modifies machine-wide `PATH`, invokes UAC, or adds a global Git
`safe.directory`.

An explicitly supplied toolchain is accepted only after the same compiler,
library, architecture, and redistributable checks pass. Environment variables
identify paths only; they cannot weaken version or file validation.

The user-writable build-tool cache defaults below Local Application Data and is
not packaged. The Conda environment root must be ASCII-safe because package
extraction failed reproducibly under the repository's Unicode path. If the
default Local Application Data path is not ASCII-safe, the build fails with a
bounded instruction to supply an explicit ASCII-safe toolchain root. It does
not create a drive mapping or copy the source tree.

The cache is safe to delete and is separate from Fairy's managed model store
and user data.

## MSVC and generator environment

`production-cuda` requires the x64 MSVC compiler and Windows SDK. The build
script discovers Visual Studio through `vswhere` or the standard installed
location, imports `VsDevCmd.bat -arch=x64 -host_arch=x64` into the current
process, and validates `cl.exe` before CMake configuration.

The imported environment is process-local. It does not persist changes to the
calling shell or system configuration.

The Ninja executable is pinned and invoked through the prepared process
`PATH`. CMake still receives the existing
`FAIRY_OMNI_PATCHED_SOURCE` argument and uses the verified upstream revision
and patch-set digest.

## CUDA architecture behavior

The runtime keeps upstream llama.cpp/ggml CUDA architecture detection. The
reference RTX 5060 Ti probe resolved the native architecture to `120a-real`.
The build must not hard-code that architecture for every release machine.

Release automation may provide an explicit reviewed architecture list for
distribution coverage. If no explicit list is provided, native detection is
allowed for an internal machine-bound candidate, and the release evidence
records that the candidate is not a general public artifact. A public Beta
candidate must document and test its supported architecture set.

## Runtime staging

Production staging copies:

- `fairy-omni-runtime.exe`;
- `build-profile.txt` containing `production-cuda`;
- `cublas64_13.dll`;
- `cublasLt64_13.dll`;
- a bounded runtime-component manifest containing relative names, sizes,
  SHA-256 digests, package versions, and license identifiers.

The stage operation is atomic per file and fails before replacing a verified
stage when any required source is missing or invalid. Obsolete governed CUDA
DLLs are removed only after the new complete stage is ready. Unknown files in
the stage fail validation rather than being silently packaged.

The normal Windows NVIDIA display driver supplies `nvcuda.dll`; Fairy does not
redistribute it. `cudart64_13.dll` is not staged unless dependency inspection
or a clean-device test proves it is required by the final executable. The
current diagnostic executable does not import it.

The MSI continues to package the whole governed `runtime/omni` resource
directory. Model weights remain forbidden from this directory and from the
installer.

## Legal and release disclosures

NVIDIA runtime bytes are distributable only under the applicable NVIDIA
agreement. The build records the package license as
`LicenseRef-NVIDIA-End-User-License-Agreement`.

Before an MSI candidate is accepted:

- the NVIDIA agreement or approved redistribution notice is present in the
  packaged legal resources;
- `THIRD_PARTY_NOTICES.md` identifies the shipped CUDA/cuBLAS runtime;
- bundle validation requires both the notice and the exact staged DLL set;
- the media manifest includes the resulting MSI and external cabinets.

This design does not assert legal approval beyond the recorded upstream
license. A release operator remains responsible for confirming redistribution
terms for the selected CUDA release.

## Failure behavior

- No eligible MSVC environment: fail before CMake.
- Toolchain download unavailable: fail with a bounded public build error and
  preserve the previous verified stage.
- Package/version/hash/license mismatch: fail closed.
- Missing CUDA compiler, headers, import library, or redistributable DLL:
  fail closed.
- CMake reports CUDA disabled: fail closed.
- Self-test does not report `production-cuda` and `cuda_compiled=true`: fail
  closed.
- Model-free packaging self-test reports backend ready or a result other than
  `model_files_invalid`: fail closed.
- Explicit release model probe does not report `ready`: fail closed.
- Required runtime DLL missing from the stage or bundle: fail release
  composition validation.
- Contract and CPU profiles never bootstrap CUDA or stage CUDA DLLs.

Ordinary Fairy startup remains lazy. Packaging CUDA libraries must not start
Omni, Voice, Realtime, CosyVoice, or a model worker.

## Tests

Deterministic tests cover:

- production preset uses Ninja Multi-Config while other presets remain
  unchanged;
- toolchain manifest schema, exact versions, origins, architecture, and
  required redistributables;
- explicit toolchain acceptance and each fail-closed rejection;
- process-local MSVC environment import;
- model-free and explicit-model release policy;
- atomic staging, component manifest hashes, stale/unknown file rejection;
- release bundle requires CUDA DLLs and legal notices;
- contract build does not access or stage CUDA;
- scripts parse under strict PowerShell mode.

Real Windows gates cover:

- clean-cache bootstrap without administrator rights;
- complete Release CUDA compile;
- dependency inspection;
- model-free fail-closed self-test;
- verified MiniCPM model self-test and local inference;
- Tauri MSI build and external-cabinet manifest;
- install, cold start, upgrade, uninstall, signing, signature verification,
  and malware scan;
- four-hour eligible-hardware soak and native privacy/performance evidence.

## Alternatives rejected

### Keep the Visual Studio generator

This retains the current failure because the CUDA Visual Studio integration is
not installed. Installing it requires UAC and mutable system state and does
not improve the runtime contract.

### Maintain Visual Studio and Ninja production presets

Two production paths increase release ambiguity and testing cost. The same
source and CUDA profile does not need two authoritative generators. A single
portable production path is easier to certify.

### Ship only the executable

The executable has an external cuBLAS dependency. Omitting the required DLLs
creates a broken clean-device installer and is rejected.

## Completion

This design is implemented when a clean user-writable cache can build the
production CUDA runtime without UAC, stage an exact legally disclosed runtime
set, preserve every existing self-test and model fail-closed invariant, pass
the deterministic suite, and produce a production MSI candidate.

Public Beta release remains separately blocked until the existing Phase 8
real-environment release gate passes every required domain.
