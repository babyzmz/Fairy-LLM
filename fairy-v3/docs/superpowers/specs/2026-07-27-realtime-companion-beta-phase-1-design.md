# Realtime Companion Beta Phase 1 Design

## Status and authority

This document defines Phase 1 of the accepted Fairy Realtime Companion Beta
design for Fairy V3 Windows Desktop. Phase 0 contracts, ADR 0022, and the
authoritative product design remain normative.

Phase 1 delivers:

- real Windows hardware capability detection;
- the NVIDIA 16 GiB local Beta product gate;
- governed MiniCPM-o model installation and verification;
- an offline runtime self-test boundary; and
- a Settings readiness card that exposes evidence without making a false
  readiness claim.

Phase 1 does not implement or bundle `fairy-omni-runtime`, load MiniCPM-o,
capture media for the model, open a network listener, run inference, or enable
Local Beta. Until the Phase 2 runtime exists and passes its self-test, overall
Local readiness remains false.

## Product decisions

1. Local Beta is Windows 10/11 x64 only.
2. The selected adapter must be a hardware NVIDIA adapter with at least 16 GiB
   of physical dedicated video memory.
3. AVX2, CUDA initialization, and exact DXGI/CUDA LUID identity are hard gates.
4. CPU fallback is not Local Beta.
5. Current free GPU budget is evaluated per activity profile:
   `Focus = model peak + renderer reserve + 3 GiB`,
   `Auto = model peak + renderer reserve + 4 GiB`, and
   `Game = model peak + renderer reserve + 5 GiB`.
6. System memory below 24 GiB is a warning, not a hard failure.
7. Model installation is explicit, cancellable, resumable, hash-verified, and
   atomic. Models are not bundled in the desktop installer.
8. Hardware, Model, Runtime, and Session Budget are separate readiness
   dimensions. A green hardware row cannot imply that the local backend is
   ready.
9. Phase 1 never starts Voice, Realtime, Omni, CUDA model inference, or a model
   download during ordinary Fairy startup or Settings navigation.

## State ownership

Tauri owns all local readiness evidence.

```text
Settings
  -> Tauri LocalReadinessService
       -> WindowsHardwareProbe
       -> OmniModelManager
       -> OmniRuntimeSelfTest
       -> Phase 0 readiness policy evaluator
```

React renders a typed projection and sends user intent. It does not inspect
drivers, enumerate adapters, hash files, choose install paths, download model
artifacts, or infer readiness.

`LocalReadinessService` maintains a short-lived read-only hardware cache and a
single model-operation state. Settings queries do not poll continuously.
Explicit refresh invalidates the hardware cache.

## Hardware probe

### Reported facts

The Windows probe returns bounded facts and diagnostics:

- OS support and process architecture;
- AVX2 availability;
- total physical system memory;
- selected DXGI adapter description, vendor ID, hardware/software status,
  physical dedicated VRAM, LUID, local-memory budget, and current usage;
- CUDA driver initialization, driver API version, enumerated CUDA devices, and
  the matching CUDA device LUID;
- model disk capacity and required install capacity.

User-facing reports may include the adapter name and numeric capacities. They
must not include arbitrary driver paths, environment variables, command lines,
or raw device handles.

### DXGI selection

The probe enumerates adapters by high-performance preference where supported
and otherwise uses ordinary DXGI enumeration. It excludes software adapters.
The first NVIDIA hardware adapter is selected deterministically. Multi-GPU
systems remain fail-closed unless one CUDA device reports the same eight-byte
LUID as that selected DXGI adapter.

Physical eligibility uses `DedicatedVideoMemory`; dynamic session eligibility
uses `IDXGIAdapter3::QueryVideoMemoryInfo` for local video memory budget and
current usage.

### CUDA boundary

Tauri loads the system CUDA driver library dynamically. It resolves only the
driver API functions required for:

- `cuInit`;
- driver version;
- device count; and
- device LUID.

The probe does not create a CUDA context, allocate GPU memory, or load a model.
Missing symbols, initialization errors, zero devices, or LUID mismatch are
specific fail-closed reasons. The library handle and function pointers never
cross the probe boundary.

### Portability

Non-Windows builds return a supported typed report with
`unsupported_os`. Pure policy evaluation remains platform-neutral and fully
unit-testable.

## Model manifest and trusted catalog

Fairy ships a reviewed manifest as an application resource, not as mutable
remote metadata. It pins:

- model ID and version;
- Hugging Face repository revision;
- runtime protocol compatibility;
- upstream llama.cpp-omni revision;
- Fairy patch-set digest;
- license;
- predicted peak VRAM;
- every relative file path, byte size, SHA-256 digest, and HTTPS download URL.

Phase 1 uses the official OpenBMB MiniCPM-o 4.5 GGUF repository. The reviewed
revision and LFS object metadata are recorded in the manifest. Runtime and model
revisions do not follow an upstream branch automatically.

Manifest validation additionally proves that `manifest_digest` equals the
SHA-256 of a deterministic canonical payload that excludes the digest field.
Manifest paths are normalized relative paths with no parent traversal,
absolute prefix, alternate data stream, duplicate case-folded name, or
reserved Windows component.

The Beta payload contains only the reviewed LLM, vision, and audio artifacts.
TTS, Projector TTS, Token2Wav, and reference voice assets are excluded.

## Model installation lifecycle

### Paths

Model data is device-local:

```text
%LOCALAPPDATA%\Fairy\models\minicpm-o-4.5\
  <version>\
    manifest.json
    MiniCPM-o-4_5-Q4_K_M.gguf
    vision\...
    audio\...
  install-state.json
```

Downloads use a sibling staging directory and `.partial` files. No model bytes
enter the repository, `%TEMP%`, Fairy Core storage, a Workspace, or a chat.

### State machine

```text
not_installed
  -> checking_space
  -> downloading
  -> verifying
  -> layout_check
  -> runtime_self_test
  -> ready

Any active state -> cancelling -> not_installed | partial
Any validation failure -> corrupt
Runtime unavailable -> runtime_missing
Runtime failure -> self_test_failed
```

Only the model manager writes install state. One operation may be active.
Repeated starts return the current operation rather than creating duplicate
downloads.

### Download policy

- Start requires an explicit Settings action.
- Only HTTPS URLs contained in the reviewed bundled manifest are allowed.
- Redirects remain HTTPS and are bounded.
- Each request has connect, read, and total-operation timeouts.
- Resume uses a byte Range from the current `.partial` length.
- If the server does not honor the Range, the partial file is safely restarted.
- Received bytes may never exceed the manifest size.
- Progress events contain only artifact path, received bytes, total bytes, and
  operation state.
- Cancellation closes the response and keeps a bounded `.partial` for a later
  resume.
- No credentials or cookies are attached.

### Verification and promotion

Each staged artifact must match both exact size and SHA-256. The full layout is
validated before promotion. A failed artifact is never moved into the version
directory. Diagnostic partial data may remain, but corrupt final files are
removed.

The verified manifest is copied into staging. The completed staging directory
is atomically renamed to its version directory on the same volume. The active
install state is updated only after promotion and directory sync.

### Removal

Removing a model is an explicit destructive Settings action with confirmation.
It is unavailable during an install or active Realtime session. Phase 1 tests
the manager operation, but does not automate the final confirmation click in a
production profile.

## Runtime self-test boundary

Phase 1 defines a runner for the future bundled path:

```text
runtime\omni\fairy-omni-runtime.exe
  --self-test
  --manifest <verified manifest>
  --model-root <verified version directory>
```

The child is non-elevated, attached to the existing process-lifetime controls,
has bounded stdout/stderr, and must return strict versioned JSON within a
timeout. It receives no Core credential or network configuration.

Because the executable is delivered by Phase 2, ordinary Phase 1 installations
report `runtime_missing`. Unit and integration tests use a fake self-test
runner; they do not manufacture a production readiness claim.

## Tauri command surface

The main and Settings views may use:

- `realtime_local_readiness_get`
- `omni_model_status`
- `omni_model_install_start`
- `omni_model_install_cancel`
- `omni_model_verify`
- `omni_model_remove`

Commands return typed projections and never expose local absolute model paths.
Model progress is published as a typed Tauri event with a monotonically
increasing sequence.

The Companion may read the final readiness projection before a start, but only
Settings may mutate model installation state. Pet surfaces cannot call these
commands.

## Settings readiness card

The Voice category keeps Realtime Companion Beta controls and adds a
`Local MiniCPM-o 4.5 Beta` card above the backend selector.

The card shows:

- Hardware: supported OS/architecture, adapter, physical VRAM, AVX2;
- CUDA: driver availability and DXGI/CUDA adapter identity;
- Model: version, exact download size, install/verification progress;
- Runtime: missing, self-testing, ready, failed, or quarantined;
- Current budget: Ready or temporarily unavailable for the selected profile;
- an overall label: `Unavailable`, `Install model`, `Runtime required`,
  `Temporarily unavailable`, or `Ready`.

No card row says Ready for unknown evidence. The install action is available
only when static hardware and disk preflight permit it. Refresh is explicit.
Reduced Motion disables indeterminate repeated animation while preserving
numeric progress.

Backend `Local MiniCPM-o 4.5 Beta` remains non-startable unless the complete
projection is ready. Cloud controls remain usable on unsupported hardware.

## Privacy and failure handling

- Hardware probing is read-only and does not capture a screen or microphone.
- Model downloads contain no user content.
- Raw model bytes, partial contents, device handles, and absolute paths are not
  emitted to UI logs or the Core ledger.
- A checksum mismatch, disk-full condition, interrupted download, stale
  manifest, or self-test failure cannot create a Ready state.
- A Settings failure affects only the local readiness card; Voice and Cloud
  Realtime configuration remain available.
- Startup reads install metadata and shallow file presence only. Full hashing
  occurs on explicit verify, after download, or before a Local session.

## Testing and acceptance

Phase 1 automated tests cover:

- DXGI adapter selection, NVIDIA vendor and 16 GiB threshold;
- AVX2, CUDA availability, driver version, LUID matching, memory warning, and
  profile budget evaluation;
- manifest canonical digest, Windows path safety, exact file set, URL policy,
  and revision pinning;
- disk preflight, resume/restart semantics, cancellation, size ceiling,
  checksum mismatch, layout rejection, atomic promotion, and single operation;
- runtime missing, strict self-test response, timeout, and failed self-test;
- Tauri command authorization and progress sequence;
- Settings card unknown/failure/installing/partial/runtime-missing/temporary/
  ready states;
- Local start remains blocked unless the full report is ready;
- Cloud controls remain available when Local is unsupported.

Network tests use a local bounded fixture server and small synthetic files.
They do not download MiniCPM-o. GPU tests use injected probe facts unless a
separate manual hardware gate is explicitly run.

Acceptance runs TypeScript, focused and complete Vitest, Rust formatting,
Clippy with warnings denied, Rust workspace tests, chat/settings Playwright,
and the complete Playwright suite. A native Windows check confirms truthful
hardware presentation and that opening Settings starts no model, Voice,
Realtime, or Omni worker.

No Docker, release bundle, production installer, real model download, CUDA
inference, Voice session, paid cloud session, or GPU qualification is claimed
in Phase 1.
