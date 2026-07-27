# ADR 0022: Governed Realtime Companion Backends

## Status

Accepted for Fairy V3 Windows-first development.

This decision supersedes ADR 0018's game-only product definition, its assumption
that one user-visible session equals one cloud provider connection, and its
React-owned Companion lifecycle. It incorporates and preserves ADR 0018's
device-local stable-public-caption amendment.

## Context

Fairy already has a separate Rust Realtime Worker, Gemini Live and GLM Realtime
providers, selected-window capture, selected-process audio, microphone capture,
barge-in, provider and Fairy voice output, Presence, stable captions, usage,
local transcript persistence, and session audit.

The existing implementation still conflates a cloud provider with a realtime
backend, requires a credential for every start, contains a hard-coded game
instruction inside the Worker, and gives the React Companion surface lifecycle
responsibilities that cannot survive a window reload. Adding a local
MiniCPM-o backend to those assumptions would create a second persona and a
second execution boundary rather than extending Fairy.

The product is therefore one **Fairy Realtime Companion Beta**. `Auto`, `Game`,
and `Focus` are activity profiles inside that product. `Quiet`, `Standard`, and
`Active` are interaction intensities. They are not assistants or personas.

## Decision

### Authority and ownership

Fairy Core remains authoritative for Persona, memory, knowledge, tools,
approvals, and governed execution. It issues a versioned Realtime Persona
Snapshot derived from the canonical Fairy Persona Authority.

Tauri `RealtimeSessionCoordinator` is the sole owner of a user-visible Presence
Session. It resolves the backend, obtains the Persona Snapshot, arbitrates voice,
bridges assistance to Core, persists stable captions, supervises recovery, and
projects state to React and the Pet.

The Pet and React Companion panel are projection and control surfaces. Reloading
or closing the panel does not end an active Presence Session.

The Rust Realtime Worker owns bounded media processing and one active Backend
Segment. The Worker and the local Omni runtime submit perception, speech, and
assistance candidates. They never execute tools, modify files, write memory, or
control keyboard and mouse.

### Three-level session lifecycle

A **Presence Session** is the complete user-visible companion session. It may
contain multiple Backend Segments and Context Epochs.

A **Backend Segment** contains exactly one Local or Cloud backend:

- Local MiniCPM-o runtime;
- Gemini Live;
- GLM Realtime Flash; or
- GLM Realtime Air.

Backend changes require an explicit user action and create a new Segment. Fairy
never silently switches Local/Cloud after failure. A user-approved continuation
may retain the Presence Session while passing only stable-caption summary,
public current context, and the canonical Persona digest to the new Segment.
Raw media and provider-hidden state are never carried across the boundary.

A **Context Epoch** is a bounded model context within one Segment. Window,
privacy, major profile, cache-pressure, repetition, frame-limit, and elapsed-time
changes may rotate the Epoch. Context Epoch rotation never carries raw media,
provider-hidden conversation items, hidden reasoning, or a model cache.

Every asynchronous media result carries Presence Session, Backend Segment,
Context Epoch, and sequence identity. Results for a stale identity are discarded
before they can affect captions, Presence, voice, assistance, memory, or UI.

### Backend boundary

Provider is not synonymous with backend.

`CloudLive` selects one of Gemini Live, GLM Realtime Flash, or GLM Realtime Air
and requires an in-memory cloud credential. `LocalMiniCpmO45` selects a
non-elevated, offline `fairy-omni-runtime` child process and does not accept a
cloud provider or credential.

The local runtime:

- has no TCP or HTTP listener;
- has no outbound network authority;
- receives no Core, provider, or database credential;
- cannot access Fairy's database;
- cannot execute tools;
- does not persist raw audio or frames; and
- is bound to the parent process lifetime.

Local Beta remains unavailable unless runtime hardware and artifact checks pass.
The product hard gate is Windows x64, NVIDIA dedicated VRAM of at least 16 GiB,
AVX2, initialized CUDA Driver, a matching DXGI/CUDA adapter, verified model and
runtime artifacts, a passing self-test, and sufficient current GPU budget. CPU
fallback is never presented as local Realtime Beta.

### Media and privacy

Microphone and selected-application audio are distinct media tracks.
Application audio does not enter microphone speech detection. All media queues
are bounded and video is latest-frame-wins.

Cloud capture requires explicit consent naming microphone, the selected window,
optional application audio, the provider, and potential third-party cost. Cloud
Beta does not follow arbitrary foreground windows.

Local capture states that raw media stays on the device. Optional online
assistance is a separate governed Core action and does not grant networking to
the local runtime.

Sensitive-window classification pauses visual input. Privacy pause stops capture,
clears media buffers and queued frames, and invalidates the Window Epoch. Resume
does not replay or upload paused content.

### Persistence and execution

Stable public captions remain the sole automatic content persistence exception.
They are stored on the local device in the linked Conversation as defined by
ADR 0018's amendment. They do not enter cloud sync, the Ledger payload, logs,
events, or crash reports.

Raw microphone audio, application audio, frames, interim captions, VAD history,
provider-hidden items, hidden reasoning, Persona Snapshot bodies, prompts,
credentials, assistance content, and local model caches are not persisted or
logged.

Long-term memory enters Hermes only through a Memory Proposal accepted by the
user or an unambiguous user instruction to remember a low-risk fact.

All networking, research, knowledge access, file access, commands, and other
external actions travel through Fairy Core and its existing Scope, Command Bus,
policy, and approval boundaries. Realtime cannot perform purchases, login,
representational communication, keyboard control, mouse control, injection, or
automatic gameplay.

### Persona

Local and Cloud backends receive the same Core-issued Persona digest. The Worker
does not read Persona resources or define a fallback character.

The final decision to speak is governed by a dialogue director that checks
current Epoch, grounding, repetition, cooldown, user speech, Fairy speech,
do-not-disturb, lock/privacy state, risk, Persona digest, and voice policy.
Model output is a candidate rather than an action.

## Compatibility and migration

Desktop preferences advance from schema 8 to schema 9. Existing cloud provider,
voice, application-audio, memory, and time-limit choices migrate explicitly.
Realtime Beta, cloud fallback, and online assistance remain disabled unless the
user enables them.

The Worker control protocol advances atomically with the bundled Desktop and
Tauri adapter. Existing persisted cloud session audits remain readable through
Core projections; an old Worker is not mixed with a new host during one desktop
installation.

`GameMemoryDigest` remains readable as the `activity = game` projection of a
future general Companion Session Digest.

## Consequences

- A Companion window reload no longer defines the lifetime of realtime media.
- Local inference can be added without pretending a child process is a cloud
  WebSocket provider.
- Failure and explicit backend continuation remain visible and auditable.
- Local hardware eligibility is conservative and can reject a nominally
  supported GPU under current memory pressure.
- The system has additional Segment and Epoch identities, but stale-result and
  privacy behavior become deterministic.
- MiniCPM can listen, observe, understand, and propose speech while Core retains
  the actual Fairy identity, memory, tools, and governed execution.
