# Realtime Companion Beta Completion Design

Date: 2026-07-29

## Status and authority

This design completes the implementation gaps found by comparing the final
Realtime Companion Beta product design with the Phase 0-8 implementation.
It does not reopen the locked Phase decisions. In particular:

- Fairy remains one Persona with Auto, Game, and Focus activity Profiles.
- Tauri remains the sole user-visible Presence Session owner.
- Local and Cloud never switch silently.
- the Local Omni runtime remains an isolated, non-networked Sidecar;
- microphone and selected-application audio remain separate model inputs;
- Stable Public Caption remains the only automatically persisted content text;
- all Assistance and tools remain governed by Fairy Core; and
- keyboard, mouse, game injection, and automatic action stay outside Beta.

This work completes code-level behavior. Physical audio quality, paid provider,
eligible-GPU inference, native WebView2 interaction, installer, signing,
performance, privacy, and long-duration release gates remain separate real
environment evidence.

## Confirmed implementation gaps

The current Phase implementation contains the following functional gaps:

1. The Worker captures raw microphone PCM but has no backend-neutral acoustic
   echo cancellation, noise suppression, automatic gain control, or local VAD.
2. Realtime environment gates for lock, do-not-disturb, and sensitive windows
   exist only as Director types and tests; the active Director never receives
   live environment state.
3. microphone, selected-window, and selected-application-audio failures are
   terminal startup failures instead of independently recoverable channels.
4. fixed selected-window capture exists, but Local Follow Foreground,
   sensitive-window exclusion, governed live source replacement, and the
   corresponding Window Epoch transition do not.
5. `realtime_local_keep_warm_minutes` is not wired into Coordinator policy and
   a Local unload request does not unload the model. Cloud daily-limit
   enforcement is a fallible React estimate rather than a native authoritative
   start gate.
6. the Companion still contains Game-only source labels, does not use the
   application-audio default, records application-audio consent as false, and
   offers application audio for Cloud transports that cannot preserve a
   separate track.

## Audio processing architecture

### Ownership and framing

`fairy-realtime-worker` gains a backend-neutral `RealtimeAudioProcessor`
between native capture and every Realtime Backend.

- input and output are PCM16, 16 kHz, mono;
- processing uses bounded 10 ms frames;
- microphone, Fairy render reference, and selected-application audio use
  separate bounded rings;
- only processed microphone PCM enters `push_microphone`;
- selected-application audio never enters the echo reference or microphone
  payload and continues through `push_application_audio`; and
- raw and processed samples are zeroized on drain, pause, rotation, and drop.

### Fairy-only render reference

On Windows, the Worker receives the verified Desktop host process ID in the
start envelope. A WASAPI process-tree loopback captures only audio rendered by
Fairy and its subprocesses. This covers WebView2 Fairy Voice and Worker-owned
provider-native playback without capturing unrelated system output.

The render reference is transient, bounded, never persisted, never sent to a
model or provider, and never exposed through a public event. If the reference
cannot be established for an audible voice mode, the session reports
`AEC_REFERENCE_UNAVAILABLE` instead of claiming full-duplex AEC.

Text-only sessions do not require a render reference.

### Software 3A and VAD

The first processor implementation is replaceable behind a narrow trait:

- an adaptive normalized least-mean-squares echo canceller consumes the aligned
  Fairy render reference;
- a bounded delay estimator searches only the supported acoustic window and
  resets on device discontinuity or context rotation;
- adaptive noise-floor suppression reduces stationary background noise without
  treating selected-application audio as user speech;
- automatic gain control uses bounded attack, release, and gain limits to avoid
  clipping;
- an energy and spectral-change VAD uses onset/offset hysteresis; and
- local speech onset immediately emits one backend-neutral Barge-in event and
  clears playback before waiting for provider-side VAD.

The processor exposes content-free counters and states only. Synthetic
deterministic tests measure echo attenuation, near-end preservation, gain
limits, VAD hysteresis, queue bounds, reset, and zeroization. Passing those
tests is implementation evidence, not the physical AEC or 120 ms acceptance
gate.

The trait permits a later digest-pinned WebRTC APM replacement if reference
hardware evidence does not meet the final acoustic gate.

## Capture scope and privacy

### Capture modes

Realtime adds an explicit capture mode:

- `selected_window`, the default for Local and the only Cloud mode; and
- `follow_foreground`, available only for Local.

Tauri owns the requested and effective source. React may request a mode or
select a window but cannot forge the active source or Context Epoch.

For Follow Foreground, Tauri samples the foreground window at a bounded
interval. A safe change prepares a `WindowChanged` transition, asks the Worker
to replace window and optional process-audio capture, waits for an identity
bound acknowledgement, then commits the new Context Epoch. Old frames, audio,
candidates, and acknowledgements are discarded.

### Sensitive-window classifier

A native classifier evaluates only in-memory window metadata:

- non-default secure desktop, lock state, or unavailable foreground ownership;
- Fairy-owned windows;
- known credential and password-manager process names;
- banking, payment, private, incognito, and protected-content title markers;
- windows that cannot be captured because the platform marks them protected;
  and
- user-configured excluded executable basenames.

Raw titles and executable paths are never persisted or logged. Public
projection contains only a stable category and `SENSITIVE_WINDOW_BLOCKED`.

When a sensitive source becomes effective:

- visual capture and selected-application audio stop and buffers are cleared;
- microphone remains governed by the user's existing mute/privacy choice;
- the Director suppresses proactive output;
- Presence projects that visual observation is privacy-paused; and
- a safe source requires a new Window Epoch before media resumes.

Cloud never follows the foreground and never changes upload scope without an
explicit user-selected window.

## Independent media channels and recovery

The Worker models microphone, selected-window video, Fairy render reference,
and selected-application audio as independent channel states:

- `starting`, `active`, `paused`, `unavailable`, or `recovering`;
- a stable public error code;
- an identity-bound monotonic sequence; and
- no device name, title, PCM, frame, prompt, or provider payload.

Recovery behavior:

- microphone failure preserves video and application perception, disables the
  microphone channel, and offers retry;
- target-window close pauses video and selected-application audio, preserves
  microphone conversation, and requests a replacement source;
- selected-application-audio failure disables only that channel;
- Fairy render-reference failure disables audible full duplex and falls back
  to text output without ending the Realtime Session;
- Fairy Voice synthesis failure keeps the approved caption and falls back to
  text;
- Core Assistance failure keeps Realtime active and publishes a bounded failed
  Assistance state; and
- Core unavailability pauses new Assistance dispatch while local/basic
  Realtime continues for a bounded interval.

A session fails only when no permitted input channel remains, a backend
protocol invariant fails, the active Backend fails, or the user stops it.

## Standby, keep-warm, and Cloud budget

`RealtimeCoordinatorStart` receives the validated local keep-warm duration.
After three quiet minutes Local enters standby and drains new model media.
After the configured additional keep-warm duration:

- zero minutes unloads immediately on standby;
- a positive value retains the loaded model until that deadline; and
- the Coordinator issues one `UnloadLocalBackend` transition.

Unload terminates the Local Backend Segment and Sidecar cleanly while keeping
the user-visible Presence Session in standby. Wake creates a new governed
Local Segment, reruns current readiness and resource checks, and never silently
uses Cloud.

Cloud daily usage becomes a Core-backed query over all Cloud Realtime Sessions
intersecting the current device-local day. It uses elapsed session wall time,
including a currently active Cloud Session, rather than audio duration and is
not limited to the latest 50 rows.

Tauri calculates the local-day UTC bounds, queries Core, and enforces the
configured 30/60/120/180 minute limit immediately before each Cloud Segment
start or wake. Query failure blocks Cloud start with
`REALTIME_CLOUD_USAGE_UNAVAILABLE`; React no longer owns the gate.

Local usage never counts against the Cloud budget.

## Preferences and UI

The preferences schema gains:

- `realtime_capture_mode`, default `selected_window`; and
- `realtime_excluded_applications`, a bounded list of normalized executable
  basenames stored only on the device.

Existing schema-9 data migrates without enabling Follow Foreground or adding
exclusions. Preference validation rejects paths, wildcards, control
characters, duplicates, and oversized lists.

The Companion:

- uses “Observed window” and “selected window” rather than “Game window”;
- shows Follow Foreground only for a resolved Local Backend;
- keeps Cloud fixed-window scope explicit;
- displays native channel and privacy projections;
- offers source replacement and channel retry actions;
- records the actual application-audio consent in the Core Session;
- applies the saved application-audio default visibly but still requires the
  user to start the session with the scope shown; and
- disables application audio for Cloud providers whose transport cannot
  preserve a separate track.

The existing `game_audio_consent` storage field remains a compatibility name;
its value becomes the truthful selected-application-audio consent.

## Failure, cancellation, and scope invariants

- Every event and command carries Session, Segment, Context Epoch, and channel
  or source sequence as applicable.
- Fast source changes coalesce to the newest safe source.
- Late media, recovery, quota, environment, and source events cannot overwrite
  a newer Session or Epoch.
- Stop, privacy pause, Backend failure, and process exit terminate all capture,
  processing, monitoring, polling, and recovery work.
- Companion reload restores authoritative Tauri projections without restarting
  media or resetting consent.
- A second Session cannot inherit the first Session's source, delay estimate,
  VAD state, excluded-app decision, channel failure, usage result, or retry.
- Logs and diagnostics contain only allowlisted codes and aggregate counters.

## Implementation slices

1. Audio processor, Fairy process-tree reference, local VAD, and Barge-in.
2. Native environment monitor, sensitive classifier, capture mode, governed
   source replacement, and media-channel recovery.
3. Configurable keep-warm unload and authoritative Cloud daily budget.
4. Preferences migration, Settings/Companion UI, truthful consent, and Cloud
   application-audio capability.
5. Deterministic regression coverage and acceptance documentation.

Each reversible slice receives its own Conventional Commit. Environment
dependent gates are recorded but not run until the user requests the joint
verification session.

