# Realtime Companion Beta Phase 0 Design

## Status and authority

This document converts the approved **Fairy Realtime Companion Beta** product
design into the bounded Phase 0 contract-freeze milestone for Fairy V3 Windows
Desktop.

The product has one Fairy Persona and one user-visible Realtime Companion.
`Auto`, `Game`, and `Focus` are activity profiles inside that product, not
separate assistants. `Quiet`, `Standard`, and `Active` are interaction
intensities, not personas.

This milestone replaces the product and architecture assumptions in ADR 0018
that are specific to an ephemeral game-only companion. It preserves ADR 0018's
amendment for device-local stable public captions until the replacement ADR is
accepted and the storage migration is implemented.

Phase 0 freezes contracts and migration behavior. It does not:

- download MiniCPM-o or another model;
- add a CUDA or llama.cpp-omni dependency;
- start a local Omni process;
- claim local hardware compatibility;
- add a second desktop application or Python backend;
- enable Realtime Beta by default;
- add keyboard, mouse, game injection, or autonomous control; or
- run Docker, release packaging, live providers, GPU benchmarks, or soak tests.

## Non-negotiable product boundaries

The following decisions are normative for every later phase:

1. Fairy Core remains the authority for Persona, memory, knowledge, tools,
   approvals, and governed execution.
2. The Pet and React control panel are projection and control surfaces only.
3. Tauri `RealtimeSessionCoordinator` becomes the sole owner of a user-visible
   Presence Session.
4. The Rust Realtime Worker owns bounded media processing and one active Backend
   Segment, but it cannot execute tools or persist raw media.
5. `fairy-omni-runtime` is a non-elevated, offline sidecar with no listening
   socket, Core credential, database access, tool authority, or durable media.
6. A Backend Segment uses exactly one Local or Cloud backend. Local/Cloud
   switching is never silent and always requires a user action.
7. The local Beta hard gate is Windows x64, NVIDIA dedicated VRAM of at least
   16 GiB, AVX2, matching DXGI/CUDA devices, verified model/runtime artifacts,
   a passing self-test, and sufficient current GPU budget.
8. CPU fallback cannot be presented as local Realtime Beta.
9. Microphone and application audio remain separate media tracks.
10. Only stable public captions may be automatically persisted as content.
    Raw audio, raw frames, interim captions, hidden provider items, hidden
    reasoning, and model caches remain transient.
11. Long-term memory enters Hermes only through an explicit Memory Proposal or
    an unambiguous user instruction to remember a low-risk fact.
12. Local and Cloud backends receive the same Core-issued Persona digest.

## Contract ownership

Phase 0 introduces one canonical contract source per boundary:

| Contract | Owner | Consumers |
| --- | --- | --- |
| Realtime Backend and Worker protocol | Rust `realtime-worker` crate | Tauri coordinator, Cloud backend, later Local backend |
| Desktop preference schema | Tauri desktop preferences | Settings, Companion UI, Presence projection |
| Realtime Persona Snapshot | Fairy Core | Tauri coordinator, Worker backends |
| Hardware Capability Report | Tauri | Settings readiness, Backend resolver |
| Omni model manifest | Tauri model manager | installer, runtime self-test |
| Presence Session / Backend Segment / Context Epoch | Tauri coordinator | Worker, Core audit, React projection |
| Assistance request/result | Fairy Core | Tauri coordinator, Worker candidate flow |

The contracts may have independent schema versions. They must not infer
compatibility from the application version alone.

## Backend contract

Provider is no longer synonymous with backend.

```rust
pub enum RealtimeBackendKind {
    LocalMiniCpmO45,
    CloudLive,
}

pub enum RealtimeCloudProviderKind {
    GeminiLive,
    GlmRealtimeFlash,
    GlmRealtimeAir,
}

pub enum RealtimeActivityProfile {
    Auto,
    Game,
    Focus,
}

pub enum RealtimeInteractionIntensity {
    Quiet,
    Standard,
    Active,
}

pub enum RealtimeVoiceOutput {
    FairyVoice,
    ProviderNativeVoice,
    TextOnly,
}
```

The shared backend abstraction accepts distinct microphone, application-audio,
video, text, and assistance-result inputs. It emits public captions, Presence,
barge-in, bounded usage, safe diagnostics, resource pressure, context rotation,
and assistance candidates.

Cloud adapters retain the existing Gemini and GLM WebSocket implementations.
The later Local adapter owns an Omni child process and binary named-pipe media
transport. No `ProviderSocket` branch may pretend the local child process is a
WebSocket provider.

Local start validation requires no cloud credential and rejects
`ProviderNativeVoice`. Cloud start validation requires a non-empty credential
and a cloud provider. Both validate session identity, capture scope, requested
voice output, and bounded feature flags before opening a provider, device, or
sidecar.

The worker control protocol advances to
`fairy-realtime-worker-v2`. Desktop, Tauri, and the bundled worker ship together,
so v1 runtime compatibility is not retained inside a running desktop version.
Persisted v1 session audit data remains readable through an explicit Core
projection.

## Worker start and event shapes

The Phase 0 `Start` contract contains:

- `session_id`, `segment_id`, and initial `context_epoch`;
- backend kind and optional cloud provider;
- optional zeroizing cloud credential;
- zeroizing serialized Persona Snapshot;
- activity profile and interaction intensity;
- voice output;
- optional selected-window source identity;
- microphone, screen, application-audio, and online-assistance flags.

Local start requires `cloud_provider = None` and `cloud_credential = None`.
Cloud start requires both values.

The event vocabulary is frozen as:

- `ready`;
- `backend_state`;
- `model_load_progress`;
- `session_state`;
- `public_caption`;
- `presence`;
- `barge_in`;
- `perception_candidate`;
- `assistance_request`;
- `assistance_state`;
- `resource_pressure`;
- `context_rotated`;
- `privacy_paused`;
- `usage`;
- `diagnostic`;
- `pong`.

Diagnostics contain only a stable safe code, component, recoverability, and
bounded numeric counters. They cannot contain Persona snapshots, credentials,
prompts, captions, window titles, application names, audio, images, provider
payloads, assistance content, or hidden reasoning.

## Persona Snapshot contract

Fairy Core exposes `realtime.persona.snapshot`. The snapshot is a versioned,
canonical JSON projection of the existing Persona Authority, not a second
persona file and not a Worker-authored prompt.

The response contains:

- schema version, authority version, Persona digest, locale;
- activity profile and interaction intensity;
- fixed Fairy identity and user-authority relationship flags;
- speech constraints for conclusion-first delivery, rare dry humour, rare use
  of “主人”, no service filler, and no empty praise;
- realtime policy for grounding, proactive limits, maximum spoken sentences,
  and prohibition on claiming unobserved actions; and
- bounded short memory containing only the current goal, subject application or
  game, recent progress, and Core-verified context.

The Persona digest is SHA-256 over canonical UTF-8 JSON with sorted object keys
and compact separators. Every Backend Segment stores the digest it received.
The Worker rejects a snapshot whose schema, identity, digest, profile, or
intensity does not match the start envelope.

The hard-coded Worker `companion_instruction()` is removed when protocol v2 is
implemented. Worker code never reads Persona resource files directly.

## Hardware Capability Report

Tauri owns a versioned, read-only capability report. It separates static
eligibility from current resource availability.

The report includes:

- Windows architecture and CPU AVX2 support;
- enumerated DXGI adapter identity, vendor classification, dedicated VRAM,
  current local-memory budget, and current usage;
- CUDA Driver availability and the CUDA device matched by adapter LUID;
- total system memory and relevant disk capacity;
- model installation, verification, identifier, and manifest digest;
- runtime installation, self-test status, protocol, and digest; and
- a single primary readiness reason plus structured warnings.

The primary readiness reasons are:

`eligible`, `model_missing`, `runtime_missing`, `unsupported_os`,
`unsupported_architecture`, `unsupported_vendor`, `vram_below_16gb`,
`avx2_unavailable`, `cuda_unavailable`, `driver_incompatible`,
`adapter_mismatch`, `insufficient_free_vram`, `insufficient_disk`,
`model_verification_failed`, `self_test_failed`, and
`runtime_quarantined`.

The report never includes a raw window title, credential, model path outside the
managed Fairy directory, or user content. GPU names may be shown locally but are
not eligible for telemetry as raw strings.

At session start, the resolver requires:

```text
available local-memory budget
  >= manifest predicted peak
   + Fairy renderer reserve
   + profile safety reserve
```

Initial profile safety reserves are 3 GiB for Focus, 4 GiB for Auto, and 5 GiB
for Game. These are product policy values, not claims that a 16 GiB adapter will
always be usable.

## Model manifest contract

The managed model identifier is
`openbmb/minicpm-o-4.5-fairy-beta`. A release manifest is immutable and contains:

- schema version and manifest digest;
- model identifier and version;
- compatible Omni runtime protocol;
- license identifier;
- predicted peak VRAM in MiB;
- an ordered file list with normalized relative path, positive byte size,
  lowercase SHA-256 digest, and at least one approved HTTPS download URL; and
- pinned upstream runtime revision and Fairy patch-set digest.

Paths are rejected if absolute, empty, parent-traversing, duplicated after
case-folding, or outside the managed model root.

Phase 0 does not publish fake artifact metadata. A manifest with a zero size,
empty URL list, non-hex digest, unpinned upstream revision, or missing patch-set
digest is invalid and cannot become `Ready`.

Models install below
`%LOCALAPPDATA%\Fairy\models\minicpm-o-4.5\`. Downloads use a bounded resumable
`.partial` file, verify size and SHA-256, validate layout, then atomically move
into the versioned managed directory. The model is not bundled in MSI/NSIS.

## Presence Session lifecycle

The user-visible lifecycle has three identities:

1. **Presence Session** — the complete session shown to the user, with a default
   maximum of four hours and explicit extension.
2. **Backend Segment** — one local runtime instance or one cloud provider
   connection. A crash, explicit backend change, provider reconnect, provider
   limit, Persona version change, or major profile change creates a new segment.
3. **Context Epoch** — a bounded model context inside a segment. Window changes,
   privacy resume, profile classification changes, cache pressure, repetition,
   frame limits, or elapsed-context policy rotate the epoch.

Epoch rotation carries only stable-caption summary, current goal and activity,
Core-verified short memory, unfinished assistance identity, and Persona digest.
It never carries raw audio, frames, provider-hidden items, or a prior model cache.

Every asynchronous media result is labeled with session, segment, epoch, and
sequence. A result whose identity is no longer current is discarded before it
can update Presence, captions, voice, assistance, memory, or UI.

## Preferences Schema 9

Schema 9 exposes:

- `realtime_beta_enabled`, default `false`;
- `realtime_backend`, default `auto`;
- `realtime_cloud_provider`, migrated from `realtime_provider`;
- `realtime_allow_cloud_fallback`, default `false`;
- `realtime_activity_profile`, default `auto`;
- `realtime_interaction_intensity`, default `standard`;
- `realtime_voice_output`, default `fairy_voice` for new installations;
- `realtime_game_audio_default`, preserving the prior value and otherwise
  defaulting to `false`;
- `realtime_online_assistance_enabled`, default `false`;
- `realtime_memory_enabled`, preserving the prior value;
- `realtime_presence_max_minutes`, default `240`;
- `realtime_cloud_daily_limit_minutes`, default `180`; and
- `realtime_local_keep_warm_minutes`, default `10`.

Migration maps:

- v8 `realtime_provider` to `realtime_cloud_provider`;
- v8 `realtime_voice_mode = native` to `provider_native_voice`;
- v8 `realtime_voice_mode = fairy` to `fairy_voice`;
- v8 `realtime_max_session_minutes` to the cloud daily limit only when its value
  is one of 30, 60, 120, or 180, otherwise to 180; and
- every migrated installation to `realtime_backend = auto` with Beta disabled.

The local backend resolver treats `provider_native_voice` as unavailable and
requires an explicit user change to Fairy voice or text-only. Migration does not
silently enable microphone, screen, cloud fallback, application audio, online
assistance, memory capture beyond the existing setting, or telemetry.

## Privacy threat model

Phase 0 records the following threat classes and required controls:

| Threat | Required control |
| --- | --- |
| Capturing an unintended window | Explicit source identity, Window Epoch, fixed-window Cloud Beta |
| Replaying stale media after pause or switch | Bounded queues, buffer clear, epoch invalidation |
| Raw media persistence | In-memory media only, no temporary BMP/WAV workflow |
| Local sidecar network access | No listener, no outbound networking, release verification |
| Credential or prompt disclosure | Zeroizing control fields, safe diagnostics, log tests |
| Tool execution outside governance | Candidate-only Worker, Core Command Bus and approval |
| Silent Local/Cloud data transfer | Explicit user action and new Backend Segment |
| Sensitive foreground content | denylist classification and automatic visual privacy pause |
| Unbounded GPU or queue pressure | latest-frame-wins, fixed capacities, resource governor |
| Persona drift | Core snapshot, digest validation, dialogue director |
| Inferred sensitive memory | Memory Proposal and user confirmation |
| React window reload ending a session | Tauri coordinator ownership and state projection |

Cloud consent must name microphone, selected-window frames, optional application
audio, provider, and potential third-party cost. Local consent states that raw
media stays on-device and that online assistance is a separate governed action.

Telemetry remains disabled by default. If a later Beta diagnostic opt-in is
implemented, it may contain only coarse GPU classification, total VRAM bucket,
backend, artifact digests, latency distributions, safe crash/OOM codes, context
rotation count, and cloud error classification.

## Phase 0 deliverables

Implementation of this design produces independent Conventional Commits for:

1. an ADR that supersedes the game-only product definition while retaining the
   stable-caption amendment;
2. Rust protocol v2 domain types and backend-specific validation, without
   dialing a local backend;
3. Core Realtime Persona Snapshot contract and deterministic projection;
4. Hardware Capability Report and model-manifest schema types with validation,
   using injected test probes rather than claiming real GPU support;
5. Presence Session, Backend Segment, and Context Epoch state models owned by a
   coordinator boundary;
6. Preferences Schema 9 migration and TypeScript consumer updates; and
7. a privacy threat model and Phase 0 contract acceptance suite.

Protocol types may be introduced behind inactive code paths. `realtime_beta_enabled`
remains false and no local launch path is exposed until later phases satisfy their
own acceptance gates.

## Phase 0 acceptance

Phase 0 is complete only when:

- no active Worker path uses a hard-coded game Persona;
- local start validation can be tested without a credential while cloud start
  still rejects missing credentials;
- all start and event types round-trip through bounded length-prefixed frames;
- Persona Snapshot digests are deterministic and tied to the canonical Fairy
  Authority;
- invalid hardware reports and manifests fail closed;
- stale session/segment/epoch results are rejected by pure state tests;
- Preferences v8 migrates deterministically to v9 without enabling Beta or new
  consent;
- no UI claims that Local Beta is available from contract-only probes;
- diagnostics and tests contain no caption body, prompt, credential, audio,
  image, or raw provider payload;
- TypeScript, focused Vitest, Core pytest, Rust fmt/clippy/tests, and existing
  Realtime Playwright tests pass; and
- no Voice, Realtime, Omni, model-download, or GPU worker is warmed during an
  ordinary Fairy startup.

Docker, release builds, production packaging, live cloud calls, real CUDA
qualification, model downloads, C++ inference, performance targets, and the
four-hour soak remain later-phase gates.
