# Realtime Companion Beta Phase 8 Acceptance

Date: 2026-07-28

## Decision

Phase 8 release engineering is complete: Fairy now has an MSI-only packaging
contract, third-party notices, support/privacy/troubleshooting disclosures,
strict bundle inspection, bounded release evidence, and a fail-closed Beta
release gate.

Fairy Realtime Companion Beta is **not approved for public release** from this
machine. A pinned, portable CUDA toolchain now builds the production Omni
runtime, and the production Tauri composition produced one MSI plus eight
external cabinets and an exact media manifest. Windows Installer ICE
validation could not run because the Windows Installer service is inaccessible
in the current Codex environment; the retained candidate was linked with ICE
suppressed only for internal inspection. It is unsigned and is therefore not a
release candidate. Install, upgrade, uninstall, signature verification, the
mandatory four-hour production-model soak, native WebView2 recovery evidence,
and reference-device performance/privacy evidence remain absent.

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
- Core passed 869 tests in 509.33 seconds; locked Ruff check and format passed.
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
- The bundled Core executable was rebuilt, probed, and hashed as
  `c31fd63d5ed2315d31c4aea4d502f96701f6d35712b62cba09b978f2acb0106f`.
- Pinned MinGit `2.55.0.windows.2` was composed and probed with the Core and
  Rust worker.
- Complete Vitest passed 98 files / 530 tests. The known jsdom Canvas warning
  did not skip or fail a test.
- Complete Playwright passed 79 tests in one controlled Vite lifecycle. The
  performance project completed in about 1.2 seconds.
- TypeScript and the production Vite build passed. Core readiness was
  1,590 ms against a 3,000 ms budget; initial renderer gzip was 733.7 KiB
  against an 800 KiB budget.
- Generated contracts were unchanged, and `git diff --check` passed.

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
  `c31fd63d5ed2315d31c4aea4d502f96701f6d35712b62cba09b978f2acb0106f`.
- Voice production runtime rebuilt successfully. Its packaged executable
  SHA-256 is
  `0c9d19fce7f66918a3ad05b470b58ee96d946647c5d531b2a08bf8e001286331`.
- The pinned portable toolchain used CUDA 13.0.3, nvcc 13.0.88, Ninja
  1.13.2, and cuBLAS 13.1.1.3. The production Omni runtime passed strict
  staging with exactly four manifest components and no model weights.
- The packaged Omni executable SHA-256 is
  `187a1e705a014a503a108c23175018e8d951ba3515a7959ba72763b1ec67b307`.
  The two packaged CUDA redistributables match the staging manifest:
  `cublas64_13.dll` hashes to
  `b787fada026a2cfe3eb07fe6f15b73b7a2aec6083a3cbc9584eb006737b980d6`
  and `cublasLt64_13.dll` hashes to
  `5d9ef9e66b68713f2b2a9cd6f0219f20f458e5d36e893fe34fe483bbcd68a744`.
- The optimized Tauri application and all eight split cabinets were generated.
  WiX then reached database validation and failed with `LGHT0217`/`LGHT0216`
  because ICE01 through ICE09 could not access the Windows Installer service.
- Re-linking the already generated cabinets with `-sval -reusecab` produced an
  internal inspection candidate. This bypass is recorded as a blocker and
  cannot satisfy the release gate.
- The schema-v2 media manifest contains exactly nine files totaling
  4,217,563,062 bytes. The MSI is 2,058,763 bytes with SHA-256
  `ffb9dc81e5543e056bc07ef6cdc1d955d6c81c1ce961737df93cdff08adb70d6`;
  each external cabinet is below the 2 GiB WiX limit.
- The real media set passed strict manifest inventory, size, and SHA-256
  validation. Windows Defender command-line scanning with remediation disabled
  reported no threats.
- Authenticode inspection reports both `fairy.exe` and the internal MSI as
  `NotSigned`.

The MSI was also decompiled into an isolated package image for bounded native
inspection. The reconstructed image contained 8,053 files and the packaged
`fairy.exe` created a real main window. This proves that the media contains a
launchable native layout; it is not an MSI install, upgrade, uninstall, ICE,
or signing pass. A raw `target/release/fairy.exe` without the packaged resource
layout is intentionally not a valid native acceptance target.

## Mocked, deterministic, and real boundaries

The release scripts and fixtures exercise exact schemas, file inventories,
hashes, policy thresholds, and failure behavior with controlled data. Vitest
uses mocked Tauri transport; Playwright uses the governed desktop fixture.
Rust and Python suites exercise real implementation state machines with
deterministic adapters. These gates do not substitute for CUDA inference,
physical audio/capture devices, a signed installer, a reference game workload,
or a four-hour wall-clock session.

The WSL sandbox attestation was not required in this run. Docker was explicitly
disabled, so PostgreSQL/S3 recovery, capability Outbox, two-device sync, memory
retrieval, document/evidence, and runtime Preview integration were not
executed. Live paid GLM traffic was not run. The fixed MiniCPM-o 4.5 model
weights were not downloaded, so production local inference was not claimed.

## Existing test changes

`scripts/test-all.ps1` now runs the Phase 8 release disclosures, bundle,
runtime-policy, license, and release-gate fixtures as part of the normal full
repository verification sequence. Existing assertions were not weakened.
Production build scripts were changed only to enforce model exclusion and
license retention; their failure conditions remain strict.

## Gates required before public Beta release

All of the following remain blocked and must be rerun against one exact
candidate:

- rerun WiX with the Windows Installer service available and pass all ICE
  validation against the exact MSI and cabinet set;
- install, cold-start, upgrade, uninstall, and verify user-data preservation;
- sign the complete candidate, verify every signature, and pass malware
  scanning again after signing;
- download and verify the pinned MiniCPM-o 4.5 model through the governed model
  manager without bundling weights into the installer;
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
