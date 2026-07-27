# Realtime Companion Beta Phase 7: Memory and Long-Session Stability

## Status

Approved for implementation. This design specializes the user-approved
Realtime Companion Beta final design and the frozen Phase 0 contracts. It
preserves Core as the authority for durable memory and Native Tauri as the
authority for resource and process lifecycle.

## Outcome

Phase 7 makes a long Realtime Presence Session bounded, recoverable, and
useful after it ends:

```text
stable public captions
        |
        v
CompanionSessionDigest -- local, bounded, session-scoped
        |
        v
Realtime Memory Proposal -- policy classified
        |
        +--> explicit low-risk fact --> governed automatic promotion
        |
        +--> inferred/sensitive fact --> explicit user confirmation
                                          |
                                          v
                                   Hermes Memory Claim

DXGI/resource samples --> Native Resource Governor --> explicit Worker policy
Sidecar exit ----------> Native Recovery Supervisor --> one new Segment or quarantine
```

Raw frames, audio, interim captions, hidden provider items, prompts, reasoning,
credentials, and application paths never enter a digest, proposal, Context
Rotation summary, or Hermes Claim.

## Considered boundaries

### Memory

Three placements were considered:

1. **Core-owned digest and proposal with Hermes Claim promotion** — selected.
   Core already owns the durable Session, stable transcript, Ledger, retention
   policy, and Hermes repositories.
2. Desktop-only summaries — rejected because they cannot provide durable,
   tenant-scoped idempotency or trustworthy retention and deletion.
3. Worker-authored memory — rejected because the Worker cannot validate
   durable transcripts, apply user memory policy, or write Hermes state.

`CompanionSessionDigest` and `RealtimeMemoryProposal` are Realtime domain
records. A proposal is not itself a Hermes Observation, because a Presence
Session may have no suitable Task and must not create a fake active chat Task.
Promotion writes a normal Hermes Claim and revision through a narrow
Core-owned bridge, with a Core Ledger source event and an immutable link back
to the proposal. This keeps the claim model authoritative without weakening
Task-scoped model-suggestion APIs.

### Resource and Sidecar lifecycle

The Resource Governor and recovery supervisor live in Native Tauri. The
Worker cannot inspect renderer state or the host DXGI budget and cannot safely
create a new Backend Segment. Core does not own GPU or process lifecycle.

The Worker receives only an explicit bounded resource policy. A local Omni
Sidecar crash is reported to Native. Native may create one recovery Segment
for the same Presence Session. A second crash quarantines the local runtime
until a manual verification retry or application update. No code path silently
falls back to CPU or Cloud.

## Companion session digest

Phase 7 adds `CompanionSessionDigest`:

- `id`, `session_id`, and `conversation_id`;
- `activity` and optional bounded `subject_title`;
- `started_at`, `ended_at`, and `duration_seconds`;
- at most 12 bounded `activities`;
- bounded `progress_summary`, `unresolved_issue`, `next_goal`, and
  `notable_outcome`;
- immutable source transcript cursor range and source digest;
- digest policy version, creation time, and revision; and
- ordered linked memory proposal IDs.

Only stable public captions belonging to the same durable Session may support
the digest. The Core rejects summary fields that cannot be traced to the
bounded stable-caption source range. Generation is idempotent by
`(session_id, request_id)` and requires a terminal Session. Reuse with
different input is rejected.

The summarizer is injected behind a Core port. The deterministic implementation
is always available and extracts only explicit bounded statements. A governed
model implementation may improve wording but receives only stable public
captions and must return the same strict schema. Failure leaves the Session
terminal and permits a retry; it never invents an empty successful digest.

Existing `GameMemoryDigest` records remain readable. They project as
`CompanionSessionDigest(activity="game")`. Existing
`realtime.memories.save/list/delete` methods remain compatibility methods and
do not auto-mark newly created records as accepted.

## Memory proposal and promotion policy

Each proposal contains:

- stable proposal ID and digest/session/conversation links;
- `kind`: `game_progress`, `next_goal`, `explicit_preference`, or
  `inferred_fact`;
- `subject`, `predicate`, JSON value, and bounded normalized text;
- target namespace: `device_local` or `user_profile`;
- confidence, sensitivity, source cursor range, and evidence digest;
- policy decision and reason;
- `pending`, `accepted`, `rejected`, or `promoted` status;
- optional Hermes Claim ID; and
- revision and timestamps.

Automatic promotion is allowed only when all of the following are true:

- memory is enabled;
- the source is a stable user caption;
- the fact is an explicit game-progress statement, explicit next goal, or an
  explicit “remember this” preference;
- the scanner classifies the content as clean and non-secret;
- the target namespace is allowed by policy; and
- no conflicting active Claim exists.

Inferred work habits, emotion, skill, relationships, health, identity,
financial data, secrets, private content, and any ambiguous statement always
remain pending. They require the main-window user to confirm. The Companion
window and pet never accept or reject memory.

Acceptance is idempotent and atomically:

1. validates the proposal revision and explicit confirmation;
2. writes a Core Ledger event;
3. creates or revises the scoped Hermes Claim with explicit-user authority;
4. records the proposal as promoted with the Claim ID; and
5. refreshes only the affected Memory projection.

Rejection writes no Claim. Forget and retention use existing Hermes tombstone
and retention behavior. No Realtime memory is cloud-synced by Phase 7.

## Context Rotation carryover

Every Context Rotation carries one strict `RealtimeContextCarryover`:

- stable-caption summary, at most 2,000 Unicode characters;
- current goal and effective activity;
- at most eight Core-verified short memory facts;
- unfinished Assistance request ID and public state; and
- the immutable Persona digest.

It excludes raw or interim transcript items, media, hidden provider items,
tool payloads, credentials, paths, caches, and reasoning. Native builds the
carryover from Core projections, binds it to the current
`session_id/segment_id/context_epoch`, and sends it with the existing
acknowledged rotation command. The Worker validates the size and identity and
passes only the public summary to the Backend. Stale acknowledgements cannot
commit the next epoch.

## GPU Resource Governor

The Native `RealtimeResourceGovernor` is a pure state machine driven by a
bounded sample:

- DXGI local budget and current usage;
- allocation failure count;
- local inference latency;
- capture frame backlog;
- device-removed/access-lost state;
- renderer health; and
- whether the target application materially changed.

The governor uses hysteresis and a minimum dwell period so samples cannot
oscillate the runtime:

| Level | Effective video policy | Other behavior |
|---|---:|---|
| Normal | 1 FPS, existing bounded 2 FPS material-change boost | Full local behavior |
| Pressure | 0.5 FPS | Suppress background structured analysis |
| High | 0.25 FPS | Only user-initiated visual analysis |
| Critical | Paused | Drain media, keep session recoverable, expose Cloud choice |
| Device removed | Terminal Segment failure | Cleanup and show explicit error |

DXGI use at or above 80%, 90%, and 96% contributes to Pressure, High, and
Critical respectively. Repeated allocation failures, excessive latency, or
backlog can raise the same levels. Recovery requires lower exit thresholds for
at least five consecutive samples. Exact thresholds are constants covered by
tests, not mutable hidden preferences.

The Worker command names the level, video interval, background-analysis
permission, user-initiated-only flag, and media-paused flag. `FrameGate`
applies the interval without high-frequency React or Native state. Critical
state does not select Cloud; the user must explicitly continue with a fresh
Cloud Segment.

## Sidecar recovery and quarantine

The actual local Worker/Omni path is supervised, rather than the currently
detached readiness-only manager:

1. A local Sidecar exit or protocol disconnect ends the current Backend
   Segment and zeroizes/drains media.
2. Before any candidate was emitted, Native may automatically restart once.
   The restart uses a new Segment ID, Context Epoch 1, the same Persona digest,
   current policy, and a freshly validated local readiness result.
3. After a candidate was emitted, recovery is still limited to one restart,
   but the UI exposes that the context was interrupted.
4. A second crash, a restart-startup failure, a device-removed signal, or an
   invalid Persona/readiness result quarantines the local runtime.
5. Quarantine is persisted for the current model installation and cleared only
   by explicit `Verify and retry` or a model/application version change.

The recovery envelope retains bounded configuration only. It never retains a
credential, raw media, transcript buffer, hidden context, or Sidecar pipe
payload. Fairy main chat, Core, and the Companion WebView remain alive.
Reloading Companion reconstructs the current Session/Segment/quarantine
projection from Native state.

## Deterministic soak and real-duration evidence

The deterministic soak harness uses virtual time and injected resource,
Sidecar, caption, and window-change events. One scenario represents four hours
and must include:

- at least 20 acknowledged Context Rotations;
- at least 10 pause/resume cycles;
- at least five target-window changes;
- one recoverable Sidecar crash and one quarantining crash;
- resource transitions through all levels and recovery hysteresis;
- Companion projection reload during the active Session; and
- terminal digest/proposal generation without identity leakage.

The harness asserts bounded buffers, monotonic identities, one restart only,
no CPU/Cloud fallback, and no raw media in persisted or protocol state.

A separate real-duration Windows script runs for at least four wall-clock
hours with an eligible local model and records sanitized counters. It is a
release acceptance gate, not replaceable by accelerated CI. If hardware or
model prerequisites are absent, the acceptance document records the gate as
blocked rather than passed.

## Public Core methods and UI

Phase 7 adds:

- `realtime.digests.create`
- `realtime.digests.get`
- `realtime.digests.list`
- `realtime.memory-proposals.list`
- `realtime.memory-proposals.accept`
- `realtime.memory-proposals.reject`

The main-window Realtime/Memory surface shows the digest and pending proposals,
with explicit Accept/Reject actions. Automatically promoted low-risk facts are
shown as saved with their reason and can be forgotten through existing Memory
controls. The Companion shows only a bounded “Session summary saved” or
“Memory review available” state and an `Open main chat` action.

## Failure and privacy behavior

- Digest or proposal failure never changes a completed Realtime Session back
  to active.
- Memory disabled means no proposal or Claim is created.
- Duplicate digest/decision/recovery events converge idempotently.
- Core unavailable defers digest/proposal work without retaining raw media.
- Critical pressure pauses local media and asks; it does not silently switch.
- Device removal and second Sidecar failure are explicit and fail closed.
- Telemetry remains off by default. Soak evidence contains counters and public
  codes only.
- Ordinary Fairy startup keeps Realtime, Omni, Voice, and GPU polling cold.

## Acceptance

Phase 7 is complete when:

- generic session digests and compatibility game projections persist and
  recover correctly;
- proposal classification and Hermes promotion obey explicit
  source/confirmation policy;
- inferred or sensitive memory cannot auto-promote;
- acknowledged Context Rotation carries only the frozen bounded fields;
- Normal/Pressure/High/Critical/device-removed resource behavior is enforced
  in the real runtime path;
- one local Sidecar restart creates a new Segment and the next failure
  quarantines;
- Companion reload preserves the current authoritative state;
- the deterministic four-hour-equivalent soak passes all required event
  counts;
- a real four-hour eligible-hardware soak is recorded before Beta release;
- focused and full Core/Rust/TypeScript/Vitest/Playwright gates pass; and
- all controlled processes and transient artifacts are cleaned up.

Paid Cloud provider traffic, production-model summary quality, real GPU
pressure behavior on unsupported hardware, and the four-hour wall-clock local
soak are reported only from observed configured evidence.
