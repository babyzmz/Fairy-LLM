# Voice Replies and Realtime Reliability Design

**Date:** 2026-07-30
**Status:** Approved
**Scope:** Fairy V3 Windows Desktop voice replies, Realtime Companion startup, and
Local MiniCPM-o 4.5 readiness persistence

## Context

Fairy currently exposes related voice features through separate controls whose
labels do not reflect their real lifecycle:

- chat and pet auto-play are independent preferences;
- the Voice settings card reports `starts on first playback`, even after the
  user explicitly asks to test the voice;
- the test sends a synthesis request before the asynchronously warming Voice
  Worker is ready;
- the pet menu can open Realtime Companion but has no direct control for
  ordinary Fairy voice replies;
- Realtime startup collapses unknown backend failures into
  `Realtime session failed`;
- Local MiniCPM verification evidence is memory-only, so a successful full
  verification is lost when Fairy restarts.

The target machine also provides concrete runtime evidence of two independent
backend faults:

1. the latest persisted Realtime Session failed before Worker launch with
   `REALTIME_PERSONA_UNAVAILABLE`;
2. the development voice environment contains both `onnxruntime` and
   `onnxruntime-gpu`, with the CPU package winning module resolution and exposing
   only `AzureExecutionProvider` and `CPUExecutionProvider`.

The Realtime Persona failure is deterministic. Tauri sends the internal
`realtime.persona.snapshot` request with a string JSON-RPC ID, while
`CoreBridge::call` accepts only integer IDs. The request is rejected inside the
bridge and never reaches Core.

The Voice Worker failure is also deterministic. Worker launch starts an
asynchronous model warmup, returns its handshake, and immediately accepts a
synthesis request. The request handler rejects every request until health is
`ready`, so the first explicit test commonly fails with
`VOICE_WORKER_NOT_READY`. Settings does not refetch health while this happens,
leaving the user with stale `idle` copy.

## Goals

- Present ordinary Fairy voice replies and Realtime Companion as two clear,
  independent capabilities.
- Give ordinary replies one shared preference across the main chat and pet.
- Allow the pet menu to explicitly prepare or stop ordinary voice replies
  without playing a test phrase or acquiring capture permissions.
- Keep Realtime microphone, screen, and application-audio capture behind an
  explicit per-session consent surface.
- Make Voice Worker startup observable, cancellable, bounded, and safe across
  concurrent requests.
- Queue eligible playback while the Voice Worker warms instead of failing the
  first request.
- Detect an invalid CUDA/TensorRT/ONNX Runtime environment before model load.
- Fix Realtime Persona startup and preserve the exact stable error code and
  startup stage on every failure.
- Persist successful Local MiniCPM runtime verification evidence and restore it
  only when all compatibility facts still match.
- Preserve on-demand loading: normal Fairy startup must not prewarm Voice,
  Realtime, or the local omni runtime.

## Non-goals

- Starting microphone or screen capture directly from the pet menu without
  confirmation.
- Keeping Voice or Realtime Workers alive across application exit.
- Automatically downloading or modifying Python packages from the application.
- Replacing CosyVoice, MiniCPM-o, the Fairy Persona Authority, or the current
  Realtime backend selection policy.
- Making provider-native voice control ordinary Fairy message playback.
- Running Docker, release builds, or production image builds during feature
  implementation.

## Product model

### Ordinary Fairy voice replies

`Fairy voice replies` is the single user-facing preference for automatic
playback of new Fairy messages. It replaces the separate main-chat and pet
auto-play toggles.

The preference and runtime are deliberately separate:

- the preference records whether eligible new Fairy replies may be spoken;
- the runtime state records whether the on-demand Voice Worker is stopped,
  starting, warming, ready, playing, stopping, or failed.

Enabling voice replies is an explicit action. It persists the preference and
immediately prepares the Voice Worker in the current application session.
Normal application startup does not prepare the Worker. If the preference is
already enabled after restart, the first eligible reply starts the Worker and
waits in the bounded playback queue until readiness succeeds or fails.

Disabling voice replies cancels queued automatic playback, stops active
automatic playback, and releases the Voice Worker. A user-initiated message
playback remains available and may start a temporary on-demand Worker without
changing the automatic-reply preference.

The pet menu item is a stateful command:

- `Enable voice replies` when the preference is disabled;
- `Prepare voice replies` when enabled but the runtime is cold or failed;
- `Voice replies ready` when ready;
- `Disable voice replies` as the corresponding checked/toggle action.

It never plays a test phrase and never opens a microphone, screen, or
application-audio source.

### Realtime Companion

`Start Realtime Companion` is a separate pet-menu and Settings action. It opens
or focuses the governed Companion window. The user selects an observed window
and explicitly confirms microphone, screen, and optional selected-application
audio for that session. Only the Companion window's final Start action acquires
capture sources.

Realtime may use the same Fairy Voice Worker for generated speech. This does
not enable ordinary automatic voice replies, and disabling ordinary voice
replies does not force a running Realtime Session to stop. Worker ownership is
reference-counted by active consumers so one feature cannot release a Worker
still required by the other.

The redundant `Enable Realtime Beta` UI toggle is removed. Entering the
Companion flow and providing per-session consent is the authoritative
activation. The internal preference may be migrated or retired, but it cannot
remain as a hidden second gate that rejects an otherwise explicit start.

## Settings information architecture

The Voice category becomes `Voice & Realtime` with two primary cards.

### Fairy voice replies card

The default surface contains:

- one automatic voice-replies switch;
- one truthful runtime status;
- a primary prepare/stop action appropriate to the current state;
- volume and speech rate;
- a secondary `Play sample` diagnostic available only after readiness.

It does not show `starts on first playback` as a substitute for runtime state.
The displayed lifecycle is:

```text
Stopped
Starting worker
Checking GPU runtime
Loading voice model
Ready
Playing
Stopping
Unavailable: <actionable reason>
```

Main-chat and pet auto-play are no longer separate public controls. Migration
uses logical OR: if either legacy preference was enabled, the unified
preference is enabled. The legacy fields are removed after all producers,
consumers, fixtures, and migration tests move to the new authority.

### Realtime Companion card

The default surface contains:

- current backend summary;
- readiness summary;
- `Start Realtime Companion`;
- a collapsed Advanced section.

Advanced contains backend selection, cloud provider, activity profile,
interaction intensity, Realtime voice output, cloud fallback, memory, budget,
and source-exclusion settings. Model install, repair, and explicit full verify
remain accessible from readiness details.

Realtime voice output remains independent of ordinary automatic replies:

- `Fairy voice` uses the shared local Voice Worker;
- provider-native voice remains valid only for compatible cloud providers;
- text-only never starts voice playback.

## Voice Worker lifecycle

### Authoritative state

Tauri owns the process and runtime lifecycle. React Query may display the state
but is not authoritative. The state snapshot includes:

- monotonic sequence;
- lifecycle state;
- active consumer count;
- queued playback count;
- model readiness;
- CUDA, TensorRT, and ONNX provider readiness;
- device name;
- stable error code;
- bounded public diagnostic;
- start and transition timestamps.

Tauri exposes explicit start, status, and stop commands. Commands are
idempotent:

- multiple starts join one in-flight startup;
- stop cancels startup and queued work;
- a late startup completion cannot overwrite a newer stop;
- a dead child process transitions to failed or stopped and releases ownership.

State changes emit a sequenced event. Settings and the pet menu subscribe while
mounted and also perform an authoritative status read after reconnect or
focus. Sequence fencing prevents stale events from overwriting newer state.

### Preflight

The Worker performs a lightweight preflight before importing or loading
CosyVoice:

- expected Torch CUDA build is importable;
- `torch.cuda.is_available()` is true;
- the selected adapter is available;
- the pinned TensorRT runtime is importable and compatible;
- ONNX Runtime exposes `CUDAExecutionProvider`;
- model and Fairy prompt assets exist.

Failure is fail-fast and actionable. In particular, a CPU ONNX Runtime package
masking the pinned GPU build reports `VOICE_ONNX_CUDA_PROVIDER_UNAVAILABLE`.
Fairy does not silently fall back to CPU for the verified GPU voice path.

Development setup and release packaging receive the same preflight. Setup
scripts may repair the locked development environment only when explicitly run
by the developer; the installed application never invokes pip.

### Warmup and playback queue

Worker startup and model load are distinct protocol operations. A Worker
handshake does not mean the model is ready. Tauri waits for a terminal warmup
result before dispatching synthesis.

Playback requests received during startup enter a FIFO queue with:

- a fixed maximum item count;
- a fixed maximum total character count;
- per-request cancellation;
- scope identity for task, conversation, turn, and message;
- an expiry deadline;
- automatic-vs-manual priority without starvation.

Automatic duplicate requests for the same message collapse to one entry.
Cancelled, expired, superseded, or no-longer-authorized entries never play.
Failure drains the queue with the same stable startup error. Successful
readiness dispatches queued requests without reordering within the same
priority.

### Ownership and shutdown

Voice Worker consumers are:

- ordinary automatic reply playback;
- manual message playback;
- Settings sample playback;
- Realtime Fairy voice.

Each consumer holds a bounded lease. Stop ordinary replies removes only the
automatic-reply lease. The Worker exits when no active or keep-warm lease
remains. Application exit always terminates the child process. There is no
normal-startup prewarm.

## Realtime startup transaction

Realtime startup is an explicit state machine:

```text
Resolving backend
Creating governed session
Loading Persona snapshot
Starting backend runtime
Preparing Fairy voice (when selected)
Acquiring microphone
Acquiring observed-window frames
Acquiring selected-application audio (when selected)
Active
```

Every transition is represented in the Companion UI and has a stable public
stage identifier. The internal Persona request uses an integer JSON-RPC ID, as
required by `CoreBridge`.

If any stage fails:

1. preserve the original stable error code;
2. report the governed session as failed;
3. release only resources acquired by that attempt;
4. stop subscriptions and polling for the failed attempt;
5. ignore late events using attempt and session identity;
6. present a stage-specific recovery action.

Frontend error mapping may translate stable codes into readable copy, but an
unknown stable code must remain visible in a diagnostic detail instead of
becoming only `Realtime session failed`.

Examples:

- `REALTIME_PERSONA_UNAVAILABLE`: Persona could not be loaded; retry after Core
  recovery.
- `VOICE_ONNX_CUDA_PROVIDER_UNAVAILABLE`: repair the voice runtime or select
  text-only.
- `CAPTURE_SOURCE_UNAVAILABLE`: select another observed window.
- `MICROPHONE_PERMISSION_DENIED`: grant Windows microphone permission.
- `OMNI_RUNTIME_QUARANTINED`: explicitly verify the local runtime.

Pet-menu activation only opens or focuses this transaction surface. It cannot
skip source selection or consent.

## Local MiniCPM verification attestation

### Stored evidence

A successful explicit verify writes a small versioned local attestation
separate from model install state. It contains no credentials or conversation
data. The compatibility key includes at least:

- attestation schema version;
- model version and manifest digest;
- runtime compatibility identifier;
- upstream runtime revision;
- patch-set digest;
- runtime build/profile digest;
- adapter LUID or equivalent stable adapter identity;
- dedicated VRAM class;
- Windows driver identity/version;
- CUDA/runtime capability facts used by self-test;
- measured peak VRAM when available;
- verification timestamp.

The file is written atomically only after model integrity, layout validation,
and native runtime self-test all pass.

### Restore and invalidation

At startup, Local readiness performs cheap metadata and compatibility checks.
It restores passed self-test evidence only when every immutable compatibility
fact matches and no runtime quarantine exists.

The attestation is rejected when:

- the model manifest, version, layout, or managed files changed;
- the runtime binary/profile, upstream revision, or patch set changed;
- the selected adapter or driver changed;
- the schema is old, missing, partial, or malformed;
- the prior runtime is quarantined;
- a model install, repair, or removal is in progress;
- required cheap safety checks fail.

Rejected evidence yields `not tested` or the relevant failure and requires an
explicit verify. An explicit verify still performs full hashing and runtime
self-test. Normal startup never performs the expensive full hash merely to
restore unchanged successful evidence.

## Scope and state ownership

- Desktop Preferences owns the unified automatic voice-replies preference.
- Tauri owns Voice Worker process state and playback leases.
- Core owns governed Realtime Session records and revisions.
- Tauri Realtime Worker owns local capture/runtime state for one session and
  segment.
- Core Persona Authority remains the only source of Realtime Persona snapshots.
- Local readiness attestation is device-local and belongs to the installed
  model/runtime/adapter compatibility scope.

No pet surface owns project, conversation, approval, Persona, or Realtime
session state. The pet menu invokes governed commands and displays bounded
state only.

Voice playback scope remains keyed by task, conversation, turn, and message.
Realtime callbacks remain keyed by session, segment, context epoch, and startup
attempt. Switching conversations or sessions must prevent late playback or
events from crossing scopes.

## Error and recovery policy

- Errors use stable uppercase codes and bounded public diagnostics.
- Logs may contain technical stack details but never credentials, prompt
  contents, raw captured audio, screen frames, or private paths in UI output.
- Retry starts a new attempt identity and cannot revive a failed attempt.
- Startup and stop have finite deadlines.
- UI busy states always terminate on success, failure, cancellation,
  interruption, or timeout.
- A failed Voice Worker may be retried after environment repair without
  restarting Fairy.
- A failed Realtime Session remains in Core history with its exact terminal
  code; retry creates a new governed Session.

## Compatibility and migration

- Migrate `voice_auto_play_chat || voice_auto_play_pet` into
  `voice_replies_enabled`.
- Remove the redundant public Realtime Beta switch and align backend activation
  validation with explicit session consent.
- Keep existing message manual-play commands compatible while routing them
  through the new readiness queue.
- Existing Local model install state remains valid. Missing attestation is
  treated as not tested and never fabricated.
- Existing quarantines continue to override any passed attestation.
- No Core database migration is needed for the Persona ID fix or startup-stage
  UI. If startup-stage evidence is persisted, it must use the existing governed
  report/revision contract rather than a parallel session store.

## Testing strategy

### Voice unit and integration coverage

- legacy preference migration for all four input combinations;
- start success, failure, cancellation, interruption, timeout, and repeated
  idempotent start;
- CUDA/TensorRT/ONNX provider preflight, including the CPU-package masking case;
- first playback queued during warmup and played exactly once after Ready;
- queue bounds, expiry, duplicate collapse, cancellation, and failure drain;
- automatic replies across two conversations never cross scopes;
- manual playback does not enable automatic replies;
- stopping ordinary replies does not stop a Worker leased by Realtime;
- late startup and playback events cannot overwrite a newer stop or scope.

### Realtime coverage

- integer Persona RPC ID reaches Core and returns the canonical snapshot;
- Persona failure preserves `REALTIME_PERSONA_UNAVAILABLE`;
- every startup stage renders and terminates correctly;
- failure at each stage cleans up acquired resources and reports one terminal
  Core state;
- unknown stable errors retain their code in diagnostics;
- repeated Start is idempotent or blocked while one attempt is active;
- two Sessions and rapid source/session switching reject late events;
- pet-menu action opens/focuses Companion without acquiring capture.

### Attestation coverage

- successful verify writes and restart restores evidence;
- unchanged facts avoid full hashing and runtime self-test;
- model, runtime, patch, adapter, driver, schema, partial-file, and quarantine
  changes each invalidate evidence;
- atomic-write interruption leaves no trusted partial evidence;
- explicit verify replaces stale evidence only after complete success;
- removal deletes or invalidates the attestation.

### UI and native coverage

- Settings and pet menu display the same sequenced Voice state;
- keyboard and pointer operation of prepare, stop, sample, and Companion actions;
- constrained Settings and pet-menu layouts;
- Realtime consent remains required on every new Session;
- native WebView2 verifies worker process launch/exit, audio playback, capture
  acquisition, cancellation, and application shutdown cleanup.

## Validation boundaries

Vitest and Rust tests may mock process, audio, and capture boundaries to prove
state transitions and cleanup. They cannot prove CUDA provider loading,
CosyVoice model readiness, audible output, microphone permission, selected
window capture, or GPU runtime compatibility.

Final acceptance therefore requires the target Windows machine:

- locked voice environment preflight;
- one cold Voice prepare and one spoken ordinary reply;
- one stop and process-release check;
- one Local Realtime start using explicit microphone/screen consent;
- one Realtime stop and cleanup check;
- restart recovery of Local MiniCPM readiness without a full reverify;
- confirmation that normal Fairy startup does not prewarm Voice or Realtime.

Docker, release, and production image builds remain out of scope unless
separately requested as a release gate.
