# Realtime Companion Beta Completion Implementation Plan

> Authority: `docs/superpowers/specs/2026-07-29-realtime-companion-beta-completion-design.md`
>
> Acceptance: `docs/acceptance/realtime-companion-beta-completion.md`

## Execution rules

- Work only in `D:\桌面\~\deskllmchat\fairy-v3`.
- Preserve the user-owned untracked `D:\桌面\~\deskllmchat\CLAUDE.md`.
- Keep every reversible slice in an independent Conventional Commit.
- Add deterministic regression coverage with each slice, but do not run the
  full gate, functional E2E, Tauri/WebView2, physical audio, GPU, provider,
  installer, or soak tests until the user requests the joint verification run.
- Do not start Voice, Realtime, Omni, CUDA, Vite, Core, or other background
  services during ordinary editing.
- Never log or persist raw media, render references, titles, executable paths,
  Persona snapshots, prompts, credentials, or provider payloads.

## Slice 1: audio processing and local Barge-in

Commit: `feat(realtime): add bounded audio processing`

### Worker protocol and launch

- Add the verified Desktop host process ID to the Tauri launch envelope and
  Worker `Start`/`RecoverLocal` protocol.
- Validate the ID as nonzero and never accept it from React.
- Add content-free audio processing and media-channel events.

### Media graph

- Add a bounded 16 kHz/mono/10 ms frame assembler.
- Add Fairy process-tree WASAPI loopback as the far-end reference for audible
  modes.
- Implement the replaceable audio processor with:
  - bounded delay estimation;
  - normalized LMS echo cancellation;
  - adaptive noise-floor suppression;
  - bounded AGC; and
  - onset/offset-hysteresis VAD.
- Reset and zeroize processing state on pause, context rotation, source change,
  recovery, and stop.
- Feed only processed microphone frames to each Backend.
- Generate one local Barge-in on speech onset, clear Worker-owned playback, and
  suppress a duplicate provider Barge-in for the same utterance.

### Regression coverage to write

- delayed synthetic echo attenuation;
- near-end/double-talk preservation;
- gain and clipping bounds;
- VAD onset, hangover, reset, and duplicate suppression;
- queue bounds and stale reference eviction;
- audible-mode reference failure and text-only behavior;
- host PID validation and secret-free events.

## Slice 2: capture privacy and channel recovery

Commit: `feat(realtime): govern capture scope and recovery`

### Native privacy policy

- Add `realtime_privacy.rs` for in-memory environment facts and sensitive
  classification.
- Reuse native lock and do-not-disturb facts without moving policy to React.
- Validate device-local excluded executable basenames.
- Never return or store the matched title/path; project only a category and
  safe code.

### Capture scope

- Add `selected_window` and Local-only `follow_foreground`.
- Add requested/effective capture source and source sequence to native status.
- Poll foreground state at a bounded interval only while a Local session with
  Follow Foreground is active.
- Coalesce rapid changes, prepare `WindowChanged`, replace Worker capture,
  require acknowledgement, then commit the Epoch.
- Pause visual/application-audio channels for sensitive sources and require a
  fresh Epoch to resume.

### Media channel state

- Track microphone, selected window, selected-application audio, and Fairy
  render reference independently.
- Convert startup and runtime failures to scoped channel transitions when at
  least one permitted input remains.
- Add retry microphone/audio and replace-window actions.
- Fence every transition by Session/Segment/Epoch/channel sequence.
- Stop all monitors and retries on terminal state.

### Regression coverage to write

- sensitive categories and no raw metadata leakage;
- selected versus Follow Foreground and Cloud rejection;
- fast foreground changes and stale acknowledgement fencing;
- lock, DND, privacy pause, safe resume, and two-Session isolation;
- each media channel failure, retry, cancellation, interruption, and timeout;
- closed target source replacement without ending microphone conversation.

## Slice 3: standby and Cloud budget authority

Commit: `feat(realtime): enforce standby and cloud budgets`

### Local keep-warm

- Pass the validated preference into Coordinator start.
- Replace the fixed ten-minute constant with per-Session policy.
- Add a single unload action and acknowledgement.
- End the Local Backend Segment/Sidecar while keeping Presence standby.
- On wake, rerun current Local readiness/resource checks and create a new Local
  Segment; never fall back to Cloud automatically.

### Cloud daily usage

- Add a Core query for Cloud Session wall time intersecting caller-supplied UTC
  day bounds.
- Include all matching and active Sessions and exclude Local.
- Derive local-day UTC bounds in Tauri.
- Enforce the limit before Cloud start, continuation, and standby wake.
- Fail closed when usage cannot be read.
- Remove the React-side latest-50 audio-duration estimate.

### Regression coverage to write

- zero/nonzero keep-warm, one unload, late tick, and wake failure;
- more than 50 Cloud Sessions, local exclusion, midnight overlap, active
  Session, exact limit, query failure, and time-bound validation;
- concurrent or repeated starts cannot oversubscribe through stale usage.

## Slice 4: preferences and Companion UX

Commit: `feat(desktop): complete realtime companion controls`

### Preferences and migration

- Add capture mode and bounded excluded applications with safe defaults.
- Validate and normalize executable basenames.
- Preserve schema-9 settings and do not silently enable Follow Foreground.

### Settings and Companion

- Add capture-mode and exclusions controls.
- Replace Game-only source terminology.
- Show Local-only Follow Foreground and fixed Cloud scope.
- Render authoritative source, privacy, and channel states.
- Add replace/retry controls with keyboard and constrained-window behavior.
- Apply the saved application-audio default visibly.
- Record actual application-audio consent.
- Disable separate application audio for unsupported Cloud transports and
  explain the scope.
- Remove the stale Phase 1 runtime copy.

### Regression coverage to write

- preference migration and invalid lists;
- two distinct sessions and late event isolation;
- truthful consent and Cloud capability;
- reload recovery, keyboard, focus, IME-safe text input, 880x680 and 640x700,
  Reduced Motion, and no horizontal overflow.

## Slice 5: evidence reconciliation

Commit: `docs(acceptance): record realtime completion evidence`

- Update the completion acceptance record with files and written regression
  coverage.
- List every existing test changed and the specification reason.
- Mark all unrun commands and real-environment gates explicitly.
- Verify only by read-only inspection that no service was started and no
  generated output was created.
- Record the exact commands reserved for the joint verification session.
- Do not claim completion or release readiness until those gates run.

