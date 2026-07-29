# Realtime Companion Beta Completion Acceptance

Date: 2026-07-29

## Status

Design approved in conversation. Implementation and evidence are pending.

This record separates code-level completion from the real Windows audio, GPU,
provider, WebView2, installer, and long-duration evidence that must be run with
the user present.

## Observable behavior

- Audible Realtime modes process microphone audio through bounded AEC, noise
  suppression, gain control, and local VAD using only a transient Fairy
  process-tree render reference.
- Local VAD interrupts Fairy playback without waiting for provider VAD.
- Microphone, selected window, Fairy render reference, and selected-application
  audio fail and recover independently.
- A closed target window preserves microphone conversation and requests a new
  source.
- Local Follow Foreground changes sources only through a Tauri-owned,
  acknowledged Window Epoch transition.
- Sensitive, excluded, locked, DND, or secure-desktop state stops visual and
  application-audio observation without persisting the matched metadata.
- Local standby observes the configured keep-warm duration and unloads its
  Backend Segment once.
- Cloud Segment start and wake fail closed when authoritative daily wall time
  is unavailable or reaches the configured limit.
- Cloud never offers selected-application audio when its provider transport
  cannot preserve a distinct channel.
- The UI uses general Realtime terminology and persists the actual
  application-audio consent.

## State ownership and scope keys

| State | Owner | Scope |
| --- | --- | --- |
| Presence lifecycle, effective capture source, privacy, standby | Tauri Coordinator | Session, Segment, Epoch |
| Audio processing and media-channel health | Realtime Worker | Session, Segment, Epoch, channel sequence |
| Local inference lifecycle and loaded model | Omni Sidecar under Tauri/Worker supervision | Backend Segment |
| Cloud daily usage | Core repository, queried through Tauri local-day bounds | device-local day, Cloud Backend |
| Capture preferences and exclusions | device-local Desktop Preferences | device |
| Full Assistance answer and approval | Fairy Core | Conversation, Task, Turn |
| UI rendering and user requests | React Companion | current authoritative projection only |

## Invariants

- No raw microphone, render reference, selected-application audio, or frame is
  written to disk, logs, Core, Ledger, diagnostics, or telemetry.
- The AEC reference contains Fairy process-tree output only and never becomes a
  model input.
- Application audio never enters microphone bytes.
- Local and Cloud cannot switch without explicit user approval.
- Cloud cannot follow the foreground.
- A sensitive-window match exposes no raw title or executable path.
- Old source, media, channel, usage, and recovery events are fenced by active
  identity and sequence.
- All queues and history are bounded and zeroized on teardown.
- A channel failure cannot terminate healthy independent channels.
- Core or usage-query failure cannot silently bypass Cloud cost policy.
- Companion reload cannot take session ownership from Tauri.

## Deterministic automation

Implementation adds focused tests for:

- synthetic delayed echo attenuation and near-end speech preservation;
- NS/AGC limits, VAD onset/offset, duplicate Barge-in suppression, reset, queue
  caps, and zeroization;
- Fairy-only reference scope and invalid host PID rejection;
- microphone, window, application-audio, and render-reference failure
  isolation, retry, cancellation, timeout, and late-event fencing;
- sensitive classifier categories, excluded-app validation, title/path
  non-disclosure, DND/lock projection, and fast foreground switching;
- two Sessions with distinct sources, exclusions, failures, and late events;
- standby at zero and nonzero keep-warm, one unload, wake readiness failure,
  and no Cloud fallback;
- Cloud usage across more than 50 sessions, midnight overlap, active sessions,
  Local exclusion, query failure, start/wake fencing, and exact limit;
- preference migration and invalid exclusion lists;
- truthful consent, general terminology, Cloud channel capability, keyboard
  actions, constrained layout, Reduced Motion, reload, and source replacement.

These tests will be written with the feature work. Per the user's instruction,
the full gate and functional/native test execution is deferred until the joint
verification session.

## Required real-environment acceptance

The following cannot be replaced by mocks or deterministic signal fixtures:

- physical speaker/microphone AEC quality and Barge-in p95 at or below 120 ms;
- headset, speaker, device change, silence, double-talk, and loud playback;
- selected-window close/reselect and Local Follow Foreground across real
  Windows applications, multiple displays, lock, UAC/secure desktop, private
  browsing, password manager, payment, DRM, and user exclusions;
- real Fairy Voice and provider-native playback reference scope;
- eligible NVIDIA 16 GiB+ MiniCPM-o Local Session and GPU pressure;
- live Gemini/GLM behavior and Cloud billing-duration comparison;
- native WebView2 reload, focus, keyboard, window sizing, and no input
  competition;
- four-hour soak, context rotations, pauses, window changes, channel recovery,
  Sidecar crash/restart/quarantine, and process cleanup;
- signed MSI install, upgrade, uninstall, and exact release candidate gate.

Each unavailable environment remains `blocked` or `not run`, never `passed`.

## Planned verification order

When the user requests the joint gate run:

1. structural preflight and formatting;
2. focused Core, Rust, C++, Vitest, and Playwright tests;
3. affected-package regressions;
4. complete repository gate without Docker unless separately requested;
5. guarded Tauri dev WebView2 and physical media tests;
6. eligible-GPU Local and live-provider tests;
7. four-hour soak and reference performance/privacy capture;
8. administrator installer/signing/release validation; and
9. process, listener, generated-output, and worktree cleanup.

## Evidence log

No implementation or verification command has been run for this completion
slice yet.

