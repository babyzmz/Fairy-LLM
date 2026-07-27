# Realtime Companion Beta Phase 3 Design

## Status and authority

This document applies the approved Realtime Companion Beta final design and
ADR 0022 to Phase 3: backend abstraction, authoritative resolution, Backend
Segments, and explicit backend changes.

Phase 0 already froze the backend and worker protocol. Phase 1 supplied
fail-closed local readiness. Phase 2 supplied the pinned offline Omni runtime,
binary media protocol, and bounded supervisor primitives. Phase 3 connects
those boundaries to the active Realtime path.

Phase 3 does not implement the dialogue director, final Persona-to-Presence
projection, activity classification, Core assistance, long-term memory, soak
certification, or release packaging. Those remain Phases 4 through 8.

## Current gap

The repository already distinguishes `LocalMiniCpmO45` and `CloudLive` in
contract types, validates backend-specific starts, and models Presence Session,
Backend Segment, and Context Epoch identities. The active runtime still has
four incompatible assumptions:

1. `RealtimeRuntime` constructs `ProviderSocket` directly and therefore cannot
   host a non-WebSocket backend.
2. the Worker rejects every local start with `LOCAL_BACKEND_UNAVAILABLE`;
3. the React Companion hard-codes `cloud_live` and treats `auto` as Cloud; and
4. desktop activation checks a renderer-supplied backend instead of resolving
   the stored preference, current local readiness, credential state, and
   per-session cloud consent itself.

Phase 3 removes those assumptions without weakening any Phase 0–2 gate.

## Backend ownership

The Realtime Worker owns one active `RealtimeBackend` object per Backend
Segment. The shared runtime owns media capture and input gating, while the
backend object owns only backend-specific connection and event translation.

The internal backend interface is synchronous at its boundary and may use
bounded worker threads internally:

```rust
trait RealtimeBackend {
    fn start(&mut self) -> Result<BackendReady, BackendError>;
    fn push_microphone(&mut self, packet: AudioFrame) -> Result<()>;
    fn push_application_audio(&mut self, packet: AudioFrame) -> Result<()>;
    fn push_video(&mut self, frame: VideoFrame) -> Result<()>;
    fn push_text(&mut self, input: TextInput) -> Result<()>;
    fn push_assistance_result(&mut self, result: AssistanceResult) -> Result<()>;
    fn poll(&mut self) -> Result<Vec<BackendEvent>>;
    fn pause(&mut self, reason: PauseReason) -> Result<()>;
    fn stop(&mut self) -> Result<BackendUsage>;
}
```

`CloudLiveBackend` contains the existing `ProviderSocket`, Gemini/GLM protocol
translation, and provider-native audio playback events. `LocalOmniBackend`
contains the Phase 2 Omni process supervisor, strict control stream, and binary
named-pipe media writer. No `ProviderSocket` branch represents the local
runtime.

The local adapter receives its executable, managed model root, immutable
manifest identity, and media-pipe token from the trusted Tauri host. These
values are not accepted from the renderer and are never emitted to React,
logs, diagnostics, Core, or the Ledger.

## Omni production session path

The Omni stdio mode must load the same verified model files that its Phase 1
self-test certified. The trusted Worker launches it with the managed manifest
and model root and then performs:

1. strict `hello`;
2. strict `load`;
3. `context_begin`;
4. bounded binary media writes;
5. `media_commit` and bounded decision polling;
6. `context_rotate`, cancellation, and stop.

Contract and CPU compatibility builds remain incapable of returning
`backend_ready = true`. A local segment becomes active only after the runtime
reports the exact production compatibility, build profile, manifest digest,
model version, and ready backend. Any mismatch terminates the Segment.

The local runtime remains offline, non-elevated, job-bound, and free of
temporary raw-media files. It receives no cloud credential, provider socket,
Core/database access, or tool authority.

## Authoritative backend resolver

Tauri resolves the stored `RealtimeBackendPreference`; React never resolves a
backend by itself.

The resolver input contains only per-session consent and requested capture
features. It reads:

- Beta enablement and stored backend preference;
- stored cloud provider and whether its credential can be loaded;
- `realtime_allow_cloud_fallback`;
- current profile and voice output;
- Phase 1 local readiness, including current GPU budget; and
- explicit per-session microphone, selected-window, optional application-audio,
  and cloud-upload consent.

Resolution is deterministic:

1. `LocalBeta` requires complete local readiness and rejects provider-native
   voice.
2. `Cloud` requires a configured provider credential and explicit cloud media
   consent.
3. `Auto` selects Local when every local gate passes.
4. Otherwise `Auto` selects Cloud only when cloud fallback is enabled, the
   provider credential is configured, and the current session explicitly
   consents to cloud media upload.
5. Otherwise it returns one stable public reason and starts nothing.

The start command reruns resolution and requires the renderer's expected
resolution to match. A changed preference, readiness report, credential, or
consent returns `REALTIME_BACKEND_RESOLUTION_STALE`; it cannot silently choose
another backend.

Resolution has no side effects: it starts no Worker, Omni runtime, Voice
Worker, capture source, provider socket, model verification, or download.

## Backend Segment lifecycle

Tauri remains the sole owner of the user-visible Presence Session. Starting the
first backend creates Segment 1 and Epoch 1. A new Segment is required for:

- a user-approved Local/Cloud change;
- a local runtime restart after an active candidate;
- a cloud reconnect after a broken provider connection;
- a provider session limit;
- a changed Persona digest; or
- a major profile change that cannot remain in the current backend context.

A Segment identity is generated by Tauri, never by React. The Coordinator
records backend, optional cloud provider, Persona digest, creation reason, and
monotonic ordinal. Worker events must match the active Session, Segment, and
Epoch before they reach UI, captions, Presence, or usage accounting.

Backend failure pauses the Presence Session and exposes explicit actions. It
does not invoke another resolver or open another backend automatically.

`continue_with_backend` is a separate Tauri command. It requires a current
failed/paused Segment, an explicit user action, a fresh resolver result, and a
new Segment identity. It carries only a bounded stable-caption summary, public
current context, and the unchanged Persona digest. It never carries raw media,
provider-hidden items, credentials, or model cache.

## Core audit compatibility

The existing Core Realtime session record keeps its historical `provider`
column for storage compatibility. Phase 3 adds `local_mini_cpm_o45` as a valid
audited endpoint and maps it to the pinned local model identifier. Tauri and the
Worker continue to use the separate backend and optional cloud-provider fields;
the legacy Core column is not used to resolve or execute a backend.

Backend Segment state remains Tauri-owned in Phase 3. Stable public captions
and session usage continue through the existing Core APIs. Durable general
Companion Session Digest work remains Phase 7.

## React projection

The Companion surface requests a read-only resolution preview and displays:

- `LOCAL · MiniCPM-o 4.5`, or
- `CLOUD · Gemini Live`, `CLOUD · GLM Realtime Flash`, or
  `CLOUD · GLM Realtime Air`;
- the stable unavailable reason when no backend can start; and
- the correct local or cloud privacy statement.

Cloud requires a distinct session checkbox that names microphone audio and the
selected-window frames sent to the selected provider. Local states that raw
media stays on-device and that online assistance is separately governed.

The start button is enabled only for the exact current resolution and consent.
An active session displays the backend from Coordinator/Worker state rather
than inferring it from preferences or the historical Core provider field.

## Failure and privacy invariants

- No Local/Cloud change occurs without a user action.
- Auto resolution happens only before a Segment starts.
- A local start cannot load or expose a cloud credential.
- A cloud start cannot receive Omni launch paths.
- Contract and CPU runtimes cannot activate Local Beta.
- Stale Segment/Epoch events are discarded.
- Failed resolution starts no process, capture, or socket.
- Raw media is never copied across Segments or persisted.
- Diagnostics contain only stable safe codes and bounded counters.
- Ordinary startup and Settings remain lazy.

## Acceptance

Phase 3 is complete only when:

- the active Worker uses the common backend interface for Cloud and Local;
- existing Gemini and GLM behavior passes through `CloudLiveBackend`;
- a contract Omni integration fixture exercises local control/media translation
  while remaining unavailable as a production backend;
- production local launch is possible only with a schema-2 ready report and
  verified managed paths;
- Auto resolver truth tables cover preference, readiness, credential, fallback,
  consent, and voice policy;
- local failure never opens a cloud socket and cloud failure never starts Omni;
- explicit continuation creates a new Segment and stale events are rejected;
- React shows the exact resolved backend and correct privacy copy;
- TypeScript, Vitest, Core pytest, Rust fmt/clippy/workspace tests, focused
  Playwright, complete Playwright, and a controlled native startup pass; and
- no controlled process, listener, trace, screenshot, model, or native build
  scratch remains.

CUDA compilation, real MiniCPM inference, live paid cloud calls, Phase 4
dialogue behavior, long soak, and release packaging are not claimed by Phase 3.
