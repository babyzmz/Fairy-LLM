# Realtime Companion Beta Phase 5: Activity Policy and Bounded Observation

## Status

Approved for implementation. This design specializes the user-approved
Realtime Companion Beta final design and the frozen Phase 0–4 contracts.
`Auto`, `Game`, and `Focus` remain activity profiles of the same Fairy Persona,
not separate assistants or session owners.

## Outcome

Phase 5 replaces timer-driven commentary and renderer-owned idle shutdown with
one Coordinator-governed activity policy:

```text
selected-window frames + public backend candidates + user activity
                              |
                              v
                 bounded Worker Frame Gate
                              |
                              v
            Tauri Activity Classifier and Event Policy
                              |
             +----------------+----------------+
             |                                 |
             v                                 v
  Window/Context Epoch rotation      Dialogue cooldown + dedup
             |                                 |
             +----------------+----------------+
                              v
              Coordinator-owned standby/wake state
```

The Worker may calculate bounded visual differences and propose an activity.
Tauri owns the effective profile, interaction policy, Context Epoch, cooldown,
standby transition, and public Presence projection. React only displays the
projection and sends explicit user controls.

## Considered boundaries

Three placements were considered:

1. **Coordinator policy with Worker perception** — selected. Native policy
   survives companion-window reloads, while image decoding and frame
   comparison stay beside the media queue.
2. Worker-owned policy — rejected because a backend process could then decide
   user-visible speech, Profile, and long-lived Presence state.
3. React-owned timers and classification — rejected because hiding or
   reloading the secondary WebView would change session behavior.

The selected boundary preserves the Phase 4 Persona and dialogue authority and
does not move raw media into Tauri, Core, logs, or durable storage.

## Effective activity classification

The user preference is `auto`, `game`, or `focus`. Explicit `game` and `focus`
resolve immediately and never change without a new explicit user request.

`auto` starts in a conservative `focus` posture. It accepts only bounded public
activity evidence from current-epoch structured candidates:

- activity is `game` or `focus`;
- confidence is finite and at least 0.72;
- the candidate has current grounding;
- session, segment, epoch, sequence, and Persona digest are current; and
- the evidence is not produced while privacy-paused, locked, or on a sensitive
  window.

A switch requires three consistent qualifying observations within the latest
five observations. A contradictory qualifying observation resets the streak.
The same event sequence cannot be counted twice. This hysteresis prevents a
single animation, browser tab, game launcher, or model mistake from changing
behavior.

An Auto switch rotates the Context Epoch and clears event history, pending
speech, frame history, and old-epoch results. It does not create another
Persona: the canonical snapshot remains bound to the `auto` preference and its
digest remains unchanged.

## Interaction intensity and proactive speech

Direct replies to a current user utterance do not use proactive cooldowns.
They still pass grounding, privacy, risk, Persona, speech-ownership, and
duplicate gates.

Proactive candidates use the effective profile:

| Profile | Quiet | Standard | Active |
|---|---:|---:|---:|
| Game | safety/high-value only; 90 s floor | 45 s | 20 s |
| Focus | safety/privacy only | 8 min | 3 min |

Time alone never creates speech. A candidate must contain a new current event.
Quiet accepts only urgency at least 0.85 or a bounded safety/privacy/security
intent. Focus accepts only the explicit value categories from the final design:
persistent error, repeated failure, requested screen explanation, permitted
stagnation reminder, stage completion, explicit goal/time reminder, privacy,
or security.

The Director evaluates using a monotonic timestamp supplied by its native
caller. Tests use a virtual monotonic clock; wall-clock changes cannot bypass
the policy.

## Event cooldown and semantic deduplication

Every accepted proactive event receives a canonical fingerprint derived from:

- effective activity;
- bounded intent;
- normalized, sorted public grounding labels; and
- normalized public text.

The Director retains at most 32 fingerprints for at most ten minutes. Exact
fingerprints and near-identical normalized text for the same intent and
grounding are suppressed. Old entries are evicted before evaluation. Rejected
candidates do not consume the proactive cooldown.

Epoch rotation, Backend Segment replacement, and session end clear the cache.
No caption body, raw frame, prompt, or hidden provider item enters a diagnostic
or durable record; only safe rejection counters may be retained.

## Window and Context Epoch

The Coordinator remains the sole epoch issuer. Rotation reasons are:

- selected window changed;
- Auto effective profile changed;
- privacy pause resumed;
- bounded context age or frame budget reached;
- detected task discontinuity; and
- explicit user reset.

Rotation is a two-sided fenced transition:

1. Coordinator reserves the next positive epoch.
2. Tauri invalidates the prior Director and speech generation.
3. Worker clears queued media and backend output.
4. Local Omni sends `context_rotate` to the sidecar and waits for the matching
   `context_rotated` event.
5. Cloud cancels current generation and starts a fresh provider context before
   accepting new media. It never relabels old provider output with a new epoch.
6. Only after acknowledgement does Tauri publish the new active identity and
   `standby` projection.

A failed acknowledgement ends the Backend Segment with a safe error. It never
falls back to another Backend silently.

Only a bounded carry-forward summary may cross an epoch: recent stable public
caption summary, current user goal, effective activity, Core-verified short
memory, an unfinished Assistance request identifier, and Persona digest. Raw
audio, frames, interim captions, provider conversation items, and KV cache do
not cross.

Phase 5 implements the epoch protocol and empty/bounded public-summary
envelope. Phase 6 supplies Assistance state; Phase 7 supplies the complete
rotation summary and time/frame-budget triggers.

## Frame Gate

Capture remains latest-frame-only. The Worker evaluates, but does not persist:

- a small grayscale perceptual signature;
- four-region difference;
- coarse motion amount;
- current Window Epoch;
- recent selected-application audio activity;
- direct user question activity; and
- whether the backend is already loading, thinking, or speaking.

Policy:

- capture may run at 10–15 FPS, but the model baseline is at most 1 FPS;
- a material visual/audio/user event may boost to 2 FPS for at most five
  seconds;
- perceptually static frames are not sent;
- a bounded heartbeat may send one frame after five minutes;
- only the newest eligible frame is retained; and
- frame history is cleared on pause, source change, epoch rotation, and stop.

JPEG byte hashes are not used as the visual-equivalence decision. Cursor
blinks, compression noise, and small clock changes must not force a complete
visual encode. Decode failure drops the frame with a safe counter and does not
terminate microphone conversation.

Selected application audio remains a distinct backend input. Phase 5 removes
the Cloud microphone-mixing queue; application audio may only use a backend
transport that preserves its separate scope. If a provider cannot preserve the
scope, that channel fails closed instead of masquerading as user speech.

## Standby and wake

The Tauri Coordinator owns the last meaningful user/window activity time.
React’s idle-stop interval and maximum-duration timer are removed.

After three idle minutes:

- Local enters `standby`, stops model video encode/decode, drains media, and
  keeps the verified model loaded.
- Cloud closes the current provider context/segment, enters `standby`, and
  retains only bounded local VAD/prebuffer state. No billable provider stream
  remains open.

Wake sources are a user utterance, explicit pet/Companion action, material
target-window change, or restoration of the selected foreground window. Local
resumes in a new Context Epoch. Cloud creates a new Backend Segment using the
already authorized session settings; it does not upload standby media.

After ten Local idle minutes the Coordinator may request unload, but Phase 7’s
Resource Governor owns the final unload decision. The four-hour Presence limit
is enforced natively and projects an extension-required state; it is never
silently extended or dependent on a WebView timer.

Privacy pause is stronger than standby: it immediately disables microphone and
video generation, clears buffers, and requires a new epoch on resume.

## UI projection

The Realtime Control Panel shows:

- requested Profile and effective Auto activity;
- Quiet/Standard/Active intensity;
- explicit Local or Cloud Backend identity;
- `standby`, privacy pause, and wake state from Coordinator projections; and
- profile changes as “applies by governed transition,” never as a local React
  state pretending the backend changed.

The panel can close or reload without ending the Presence Session. No new
window, pet, Persona, or activity-specific chat is introduced.

## Failure and privacy behavior

- Low-confidence or conflicting activity evidence keeps the current profile.
- Stale epoch/frame/candidate results are dropped.
- Frame decode failure does not expose bytes or stop audio conversation.
- Context rotation failure terminates only the active Backend Segment.
- Cloud standby cannot silently continue provider billing.
- Local standby cannot open a Cloud socket.
- No raw audio, raw frame, perceptual signature, window title, prompt, or
  candidate body is logged or persisted.
- Ordinary startup and opening Settings leave Realtime, Voice, and Omni cold.

## Acceptance

Phase 5 is complete when:

- Auto changes activity only after hysteresis and explicit profiles never
  auto-switch;
- profile/intensity budgets match the table and user replies bypass only the
  proactive cooldown;
- ten-minute bounded event dedup suppresses repeated and near-identical
  proactive comments;
- Window/Context Epoch rotation invalidates all old media, output, Presence,
  and speech generations;
- the Frame Gate uses perceptual/region change rather than JPEG byte equality,
  is latest-only, respects 1/2 FPS bounds, and stops static sends;
- selected application audio is never mixed into microphone speech;
- Local and Cloud standby actions differ as specified and React owns no idle or
  maximum-duration lifecycle timer;
- wake creates a new epoch or segment without replaying standby media;
- panel labels and native projections show requested/effective Profile,
  intensity, Backend, and standby accurately;
- focused Rust/Vitest/Playwright and full regression suites pass; and
- a controlled Tauri dev run proves panel reload/close does not own the session
  and ordinary startup leaves on-demand workers cold.

Model inference, paid provider traffic, real four-hour soak, Core Assistance,
durable session memory, GPU pressure governance, installer/release packaging,
and production performance claims remain outside Phase 5.
