# Realtime Companion Beta Phase 8 Acceptance

Date: 2026-07-28

## Decision

Phase 8 release engineering is complete: Fairy now has an MSI-only packaging
contract, third-party notices, support/privacy/troubleshooting disclosures,
strict bundle inspection, bounded release evidence, and a fail-closed Beta
release gate.

Fairy Realtime Companion Beta is **not approved for public release** from this
machine. A pinned, portable CUDA toolchain now builds the production Omni
runtime, and the current production Tauri composition produced one MSI plus
seven external cabinets and an exact media manifest. The exact MSI passed WiX
ICE validation without suppression, strict bundle inspection, Windows Defender
scanning, and bounded native launch inspection. It remains unsigned. A real
per-machine installation attempt reached `InstallFinalize` but Windows
Installer rejected the non-administrator token with Error 1925 and rolled the
transaction back. Install, upgrade, uninstall, signature verification, the
mandatory four-hour production-model soak, broader native WebView2 recovery
evidence, and reference-device performance/privacy evidence therefore remain
absent.

The pinned production model was downloaded through the governed model manager,
and the exact Omni runtime packaged in the current MSI passed its real
three-model CUDA initialization probe. The available RTX 5060 Ti remains
correctly ineligible for Local Beta because DXGI reports about 15.67 GiB of
physical dedicated memory, below the frozen 16 GiB product floor. These
successful candidate checks do not change the public-release decision.

This record certifies the Phase 8 implementation and its rejection behavior. It
does not convert missing physical or release-candidate evidence into a pass.

## Release contract and disclosures

- The supported Windows artifact is MSI with external cabinet files. The
  generated media manifest must enumerate the exact MSI/cabinet set, sizes,
  and SHA-256 digests.
- MiniCPM model weights are downloaded into the managed local model store and
  are forbidden from the installer. Production Omni runtime staging no longer
  requires a model source merely to build the runtime, while an explicit model
  probe remains strict.
- The bundle contains the Realtime Companion support matrix, privacy
  boundaries, troubleshooting guidance, top-level third-party notices, and
  relevant dependency license texts.
- Voice packaging preserves the CosyVoice and Matcha-TTS root licenses in
  addition to installed Python dependency licenses.
- Bundle inspection rejects model weights, databases, logs, recordings,
  environment files, legacy Qt `main.py`, test results, duplicate cabinets,
  missing disclosures, and incomplete license sets.
- Release evidence accepts only exact, bounded schemas with relative artifact
  paths, fresh timestamps, content-free diagnostics, verified file sizes and
  hashes, and quantitative production thresholds.
- The gate refuses to overwrite an existing report and emits a bounded
  `passed` or `blocked` JSON result. A blocked gate exits nonzero.

## Automated certification evidence

The final repository gate
`scripts/test-all.ps1 -SkipDocker` completed successfully:

- Release disclosures, bundle policy, release-gate fixtures, Omni runtime
  policy fixtures, and Voice license fixtures passed.
- Sandbox tests passed 49 tests with one existing platform skip.
- Core passed 869 tests in 555.92 seconds; locked Ruff check and format passed.
- Capabilities passed 118 tests.
- Cloud unit tests passed 123 tests with one existing skip and 29
  PostgreSQL-backed integration tests deselected.
- Alembic upgrade and downgrade SQL generation completed.
- Rust format, Clippy with warnings denied, and the complete workspace test
  suite passed. The credential-gated live GLM test remained explicitly
  ignored.
- The deterministic four-hour-equivalent Realtime soak passed. It remains a
  state-machine regression gate and is not the required four-hour wall-clock
  production-model soak.
- The repository gate rebuilt and probed a fresh bundled Core executable,
  hashing it as
  `d85cf55aa78018aa335d5730daa161e152a5d700aec12e3096318ee52c8380fe`.
  This gate output is distinct from the executable in the retained MSI,
  whose exact candidate hash is recorded below.
- Pinned MinGit `2.55.0.windows.2` was composed and probed with the Core and
  Rust worker.
- Complete Vitest passed 98 files / 530 tests. The known jsdom Canvas warning
  did not skip or fail a test.
- Complete Playwright passed 79 tests in one controlled Vite lifecycle. The
  performance project completed in about 1.2 seconds.
- TypeScript and the production Vite build passed. A final isolated rerun of
  the terminal performance gate measured Core readiness at 1,846.0 ms against
  a 3,000 ms budget; initial renderer gzip was 733.7 KiB
  against an 800 KiB budget.
- Generated contracts were unchanged, and `git diff --check` passed.

The Rust workspace portion used `CARGO_BUILD_JOBS=1` after the first parallel
attempt encountered Windows error 1455 while unrelated user applications were
using substantial memory. The serial rerun passed the same tests and warnings
policy without reducing coverage.

Release-gate fixtures cover a passing candidate plus blocked, missing,
malformed, unknown-field, path-traversal, digest, stale-evidence, threshold,
and output-overwrite cases. A separate final audit used the published blocked
example as input. It exited with code 1 and reported every absent mandatory
domain as blocked, including four-hour soak, native WebView2, reference
performance, privacy, installer lifecycle, security, and artifacts.

## Production packaging evidence

An actual `npm run release:windows` composition was run with a clean portable
CUDA cache:

- Core production sidecar build and probe passed. The packaged executable
  SHA-256 is
  `48e0925c54b695b4a2c11bc91145df6f8dc1f7f99372e7fdefece0b4664cb403`.
- Voice production runtime rebuilt successfully. Its packaged executable
  SHA-256 is
  `4778e2e6f60d40eba190fa14436df100f56635dd44e65afb9c5b68081eadcc1d`.
  Staging removed all 17 generated Python cache directories while retaining
  only the three exact, hash-pinned governed Fairy voice assets.
- The pinned portable toolchain used CUDA 13.0.3, nvcc 13.0.88, Ninja
  1.13.2, and cuBLAS 13.1.1.3. The production Omni runtime passed strict
  staging with exactly four manifest components and no model weights.
- The packaged Omni executable SHA-256 is
  `7dc65e7aab9b04ceb1f898d7932aebc5aed73378ac61f2f7ed30494a67a9d110`.
  The two packaged CUDA redistributables match the staging manifest:
  `cublas64_13.dll` hashes to
  `b787fada026a2cfe3eb07fe6f15b73b7a2aec6083a3cbc9584eb006737b980d6`
  and `cublasLt64_13.dll` hashes to
  `5d9ef9e66b68713f2b2a9cd6f0219f20f458e5d36e893fe34fe483bbcd68a744`.
- The optimized Tauri application and all seven split cabinets were generated.
  WiX `candle` and `light` completed with database validation enabled; the
  candidate passed the actual ICE validation sequence without `-sval`.
- The schema-v2 media manifest contains exactly eight files totaling
  4,218,051,160 bytes. The MSI is 2,039,172 bytes with SHA-256
  `1df7519fe1480f0f3e8e9af7d097ff4456bdbb9e4913f3fece14255b7695fdd2b`;
  each external cabinet is below the 2 GiB WiX limit.
- The real media set passed strict manifest inventory, size, and SHA-256
  validation. Windows Defender command-line scanning with remediation disabled
  reported no threats.
- The raw release `fairy.exe` hashes to
  `5645c201aebc1ceb2136fc0dd69f74d5f3c882040156af73421858c958b950fd`;
  the MSI payload hashes to
  `a6a6c155909ba3e2e21cbeb238ba5e8f7ea6f6b47eeb4ce1885fe3e3488ee636`
  after Tauri applies its installer bundle-type patch.
- Authenticode inspection reports both the application and MSI as `NotSigned`.

The MSI was also decompiled into an isolated package image for bounded native
inspection. All 7,984 declared files were extracted and mechanically
reconstructed, and the resulting image passed the strict real-bundle policy.
The exact packaged `fairy.exe` created one real WebView2 main window, rendered
`CORE READY`, created no independent Settings WebView, and showed no Voice
worker preheat before being closed cleanly. This proves that the media contains
a launchable native layout; it is not an installed cold-start, upgrade,
uninstall, or signing pass. A raw `target/release/fairy.exe` without the
packaged resource layout is intentionally not a valid native acceptance target.

The exact per-machine MSI was then invoked through Windows Installer. It
reached `InstallFinalize`, failed with Error 1925 because the current Codex
process has no administrator token, and fully rolled back without leaving a
Fairy registration or installation directory. An administrative-image attempt
was blocked by the same environment at installer transaction finalization.
This is an explicit privilege blocker, not installation evidence.

Three post-composition changes harden the PowerShell release probes and bound a
desktop readiness wait under host load. They do not change the staged Fairy,
Core, Voice, or Omni product payload recorded here. A public candidate must
nevertheless be recomposed, signed, and reverified from the final source after
all remaining physical and installer gates can be executed.

## Post-packaging production Omni evidence

The governed live model gate subsequently downloaded and verified the exact
three-file MiniCPM-o 4.5 set:

- model payload: 6,781,995,488 bytes;
- model manifest digest:
  `8afc38a4665340e1308b286f01da8eb7c608d5c1d745e5e4292a057812b76ac0`;
- runtime patch-set digest:
  `b2a095f49fb5d48c587505673b0549a50ee6bd4717066c7a9e562eb5c031d29a`;
- rebuilt staged runtime SHA-256:
  `7dc65e7aab9b04ceb1f898d7932aebc5aed73378ac61f2f7ed30494a67a9d110`.

The live Rust gate rehashed the installed files, verified the stored manifest,
launched the staged production-CUDA runtime from the Unicode workspace path,
and received `backend_ready=true` with `model_probe=ready`. The durable model
state is `ready` and Local Readiness reports the runtime as `passed`.
An isolated self-test capture exited zero with exactly one 483-byte JSON line
on stdout; 43,043 bytes of upstream diagnostics remained on stderr.

The same report recorded Windows x64, AVX2, CUDA/DXGI adapter identity match,
CUDA driver API 13.3, 16,829,644,800 bytes of dedicated VRAM, a
16,024,338,432-byte current local-memory budget, and a 15,330,181,120-byte
Focus requirement. Budget was sufficient, but the strict physical threshold
was not: the capability reason remained `vram_below16gb`. This is the intended
fail-closed result and means the machine cannot supply the mandatory supported
16 GiB+ soak evidence.

The exact hash above is the Omni executable contained in the current MSI
payload. Its isolated three-model CUDA self-test exited zero, emitted exactly
one protocol record on stdout, and reported `backend_ready=true`,
`model_probe=ready`, `cuda_compiled=true`, and
`build_profile=production-cuda`.

## Mocked, deterministic, and real boundaries

The release scripts and fixtures exercise exact schemas, file inventories,
hashes, policy thresholds, and failure behavior with controlled data. Vitest
uses mocked Tauri transport; Playwright uses the governed desktop fixture.
Rust and Python suites exercise real implementation state machines with
deterministic adapters. The new live gate proves real CUDA model
initialization, but does not substitute for sustained multimodal inference,
physical audio/capture devices, a signed installer, a reference game workload,
or a four-hour wall-clock session.

The WSL sandbox attestation was not required in this run. Docker was explicitly
disabled, so PostgreSQL/S3 recovery, capability Outbox, two-device sync, memory
retrieval, document/evidence, and runtime Preview integration were not
executed. Live paid GLM traffic was not run. The fixed MiniCPM-o 4.5 model
weights and production CUDA runtime passed initialization, but production
local inference and long-duration stability are not claimed.

## Existing test changes

`scripts/test-all.ps1` now runs the Phase 8 release disclosures, bundle,
runtime-policy, license, and release-gate fixtures as part of the normal full
repository verification sequence. Core sidecar and composition probes now use
bounded child-process lifecycles, restore the caller's console encoding, parse
exactly one UTF-8 JSON response, and fail closed on missing fields. The desktop
minimum-window readiness wait is explicitly bounded at 15 seconds so host load
does not inherit Playwright's unrelated 5-second locator default. Existing
assertions were not weakened. Production build scripts continue to enforce
model exclusion and license retention; their failure conditions remain strict.

## Gates required before public Beta release

All of the following remain blocked and must be rerun against one exact
candidate:

- install with an administrator token, cold-start the installed application,
  upgrade from a supported prior baseline, uninstall, and verify user-data
  preservation;
- sign the complete candidate, verify every signature, and pass malware
  scanning again after signing;
- complete a four-hour wall-clock local MiniCPM session on a supported NVIDIA
  16 GiB+ reference device, including capture, audio, Voice, pause/resume,
  window switches, context rotations, recovery, quarantine, and cleanup;
- capture native WebView2 evidence for proposal authority, resource pressure,
  crash recovery, quarantine, Companion reload, and no-worker-preheat;
- pass production latency limits and measure median GPU frame-time impact at
  no more than 10% and game 1% Low impact at no more than 15%;
- verify selected-window privacy, sensitive-window pause, no raw media on disk,
  content-free logs, disclosed Cloud/local scope, and telemetry off by default;
- rerun the complete non-Docker and required Docker/PostgreSQL/S3/WSL gates as
  demanded by the release environment, then populate fresh evidence with the
  exact artifact hashes.

Until every item passes, the release gate must remain blocked.
