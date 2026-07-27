# Realtime Companion Beta Phase 8: Release Packaging and Gates

Date: 2026-07-28

## Authority

This design implements Phase 8 of the user-approved Fairy Realtime Companion
Beta final design and preserves every frozen Phase 0–7 contract.

## Decision

Phase 8 produces an unsigned, signed-ready Windows x64 MSI release candidate
and a machine-readable release-gate report. Packaging success is not release
approval. The gate fails closed unless every mandatory functional, privacy,
stability, performance, native, legal, and artifact requirement has current
evidence.

The first Beta distributes one installer format: MSI. The existing custom WiX
layout remains authoritative because the bundled runtimes require external
cabinet media. README and release instructions must not claim an NSIS bundle.

The MiniCPM-o model is not included in the MSI. It remains a separately
downloaded, digest-verified managed artifact.

## User-visible release contract

- Product and Companion surfaces retain the explicit `Beta` label.
- Local copy states that Windows x64, a qualifying NVIDIA GPU with at least
  16 GiB physical VRAM, verified artifacts, and sufficient current GPU budget
  are all required. It never promises that a 16 GiB card is automatically
  usable.
- Cloud copy states that microphone audio and the selected window can be sent
  only after permission and that third-party provider charges may apply.
- Telemetry remains disabled by default.
- The installer contains the support matrix, privacy disclosure,
  troubleshooting guide, and third-party notices as local resources.

## Release artifact composition

The MSI contains:

- the Fairy Desktop binary;
- the generated Fairy Core sidecar;
- the local Workspace Worker;
- the Voice Worker runtime and its existing governed assets;
- the production CUDA Omni runtime and required DLLs;
- pinned MinGit;
- Omni model manifests, but no model weights;
- local legal, support, privacy, and diagnostics documents.

The bundle must not contain:

- API keys, credentials, `.env` files, local databases, logs, or user data;
- MiniCPM model weights or partial downloads;
- source checkouts, test fixtures, build caches, crash dumps, or raw media;
- a Python/Qt legacy entry point;
- a TCP/HTTP server for the Omni runtime.

Every MSI release candidate is accompanied by an external cabinet set,
SHA-256 artifact manifest, release-gate JSON, and human-readable acceptance
record. Signing is a deployment action and is not performed with repository
credentials.

## Third-party notices

`THIRD_PARTY_NOTICES.md` is the distributable legal index. It identifies the
material shipped runtimes and direct application dependencies, their license
families, source locations, and whether bytes are bundled or downloaded
separately.

Full license texts already present in upstream runtime inputs are copied into
the legal resource directory during packaging. The notice must explicitly
cover at least:

- Tauri and the Rust runtime dependency set;
- React, TanStack Query/Router, Motion, Lucide, Three.js, pdf.js, Zod,
  React Markdown, and remark-gfm;
- Fairy Core's Python runtime and direct dependencies;
- MinGit/Git for Windows;
- Voice Worker/CosyVoice governed runtime assets;
- llama.cpp-omni/ggml and the Fairy DDA integration;
- MiniCPM-o 4.5 as a separately downloaded Apache-2.0 model.

The release gate validates required notice entries and bundled legal-resource
paths. It does not infer that a dependency is absent merely because its
license could not be discovered.

## Support matrix

The support matrix distinguishes availability from release certification:

| Device/runtime state | Local Beta | Cloud Beta |
| --- | --- | --- |
| Windows 10/11 x64, qualifying NVIDIA 16 GiB+, all readiness checks pass | Supported Beta | Supported when configured |
| Qualifying GPU but insufficient current VRAM | Blocked for that session | Supported when configured |
| NVIDIA below 16 GiB | Not offered | Supported when configured |
| AMD/Intel discrete GPU | Not offered in this Beta | Supported when configured |
| No discrete GPU | Not offered | Supported when configured |
| Model/runtime missing, failed, or quarantined | Blocked with diagnosis | Supported when configured |

Local Beta remains disabled by default. CPU fallback cannot be presented as
Local Realtime. Cloud use is never a silent fallback.

## Privacy and diagnostics

The bundled privacy document defines:

- selected-window-only capture and sensitive-window pause;
- separate microphone and application-audio channels;
- no raw microphone, application audio, continuous frames, interim captions,
  prompts, hidden model state, or credentials written to disk;
- stable public captions as the only automatic content-text persistence;
- local-only transcript storage and proposal-governed long-term memory;
- explicit Cloud upload scope;
- telemetry off by default.

If the user opts into future Beta diagnostics, the only permitted fields are
bounded hardware class, total VRAM, Backend, model/runtime digests, latency
quantiles, safe crash/OOM categories, rotation counts, and Cloud error
categories. Caption text, prompts, audio, images, raw window titles, game
names, questions, and Assistance answers are prohibited.

The troubleshooting guide maps only stable public reason/error codes to
actions. It never asks the user to expose a transcript, prompt, credential, or
raw provider response.

## Machine-readable release gate

`scripts/test-realtime-companion-beta-release.ps1` validates a caller-supplied
evidence directory and optional bundle directory. It emits a bounded JSON
report and returns non-zero when a required gate is missing, stale, malformed,
or failed.

Required evidence:

- Phase 0–7 acceptance records;
- complete Core, Rust, TypeScript, Vitest, and Playwright pass summaries;
- successful real four-hour eligible-hardware soak;
- native WebView2 Companion recovery/quarantine evidence;
- reference local CUDA latency and queue results;
- reference Game Profile GPU frame-time and 1% Low results;
- privacy/static-content scan;
- artifact composition and third-party notice validation;
- installer install/start/upgrade/uninstall lifecycle;
- malware/signing status supplied by the release operator.

The gate records `blocked`, not `passed`, for an unavailable environment.
Signing may be `not_run` for an unsigned internal release candidate, but a
public-release decision then remains blocked.

The report contains paths only relative to the evidence root, artifact names,
digests, timestamps, gate states, and bounded public reasons. It never embeds
test logs, user identifiers, machine names, captions, prompts, or credentials.

## Installer lifecycle

The candidate MSI is tested in a disposable Windows environment:

1. install per-machine;
2. start Fairy cold and verify no Voice, Realtime, or Omni worker preheats;
3. open the main window and Realtime Companion Beta surface;
4. verify local-unavailable and Cloud disclosure states;
5. close and reopen through the normal lifecycle;
6. upgrade from the previous supported installer when available;
7. uninstall and confirm program files/processes are removed while user data
   is not silently destroyed.

An installer lifecycle unavailable in the current environment remains a
blocked gate. Unit tests or an unpacked debug binary do not substitute for it.

## Failure behavior

- A missing legal resource fails packaging validation.
- A missing cabinet, digest mismatch, or unexpected executable fails artifact
  validation.
- Missing model/runtime bytes keep Local Beta unavailable; they do not fail
  ordinary Fairy startup.
- Missing soak, native, reference GPU, signing, or malware evidence blocks
  release without deleting the candidate artifacts.
- A failed install/upgrade/uninstall test blocks release.
- No gate invokes paid Cloud providers, downloads the model, changes system
  privacy settings, or silently installs prerequisites.

## Verification

Fast and deterministic gates:

- release-document and support-matrix tests;
- bundle resource and forbidden-content inspection;
- PowerShell parser and fail-closed fixture tests;
- third-party notice coverage;
- repository boundary and secret scans;
- Core, Rust, TypeScript, Vitest, and Playwright regression.

Real environment gates:

- production sidecar and MSI build;
- MSI composition, external cabinet, and SHA-256 validation;
- disposable install/upgrade/uninstall;
- native WebView2 interaction;
- eligible-hardware four-hour soak;
- reference CUDA latency, queue, thermals, and Game Profile impact;
- release-operator signing and malware scan.

## Completion

Phase 8 engineering is complete when the MSI inputs, release documents,
machine-readable fail-closed gate, automated tests, and truthful acceptance
record are committed and verified. Public Beta release is approved only when
the release-gate report passes every mandatory real-environment gate.
