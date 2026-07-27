# Realtime Companion Beta Phase 4: Persona and Presence Projection

## Status

Approved for implementation. This design specializes the frozen Phase 0
authority and the user-approved Realtime Companion Beta final design. It does
not create another Persona, another companion application, or another session
owner.

## Outcome

Phase 4 makes every user-visible Realtime reply a projection of the same Fairy
Persona, regardless of whether the active Backend Segment is Local MiniCPM-o
4.5 or Cloud Live.

The active path becomes:

```text
Core Persona Authority
        |
        v
Realtime Persona Snapshot + SHA-256 digest
        |
        v
Local or Cloud Backend Segment
        |
        v
structured dialogue candidate
        |
        v
Tauri RealtimeDialogueDirector
        |
        +--> approved public caption / assistance request
        +--> Coordinator-owned Presence projection
        +--> Fairy Voice short-phrase stream
        `--> suppressed candidate with a safe reason code
```

The Worker may perceive, transcribe, and propose. It may not execute actions,
write memory, switch providers, or publish an assistant reply around the
Director.

## Existing authority retained

Fairy Core remains the sole authority for:

- the fixed Fairy identity and relationship to the user;
- Persona version and digest;
- the user having final authority;
- privacy, time, and work protection;
- conclusion-first, calm, accurate speech;
- rare dry humour and rare use of “主人”;
- the prohibition on customer-service filler, empty praise, role drift, and
  claims about unobserved actions; and
- bounded, Core-verified short memory.

`realtime.persona.snapshot` remains the only snapshot source. The Worker
validates the canonical snapshot envelope but never reads Persona resources.
Every Backend Segment records the exact digest supplied by Core. A continuation
whose digest differs from the Presence Session digest fails closed and requires
a new user-approved session.

## Canonical backend instruction

The snapshot is projected into one bounded backend instruction inside the
Realtime Worker. Local and Cloud receive the same instruction content and
digest. Backend-specific transport syntax may differ, but the identity,
relationship, speech rules, grounding policy, locale, profile, intensity, and
short-memory facts may not.

Snapshot validation requires:

- schema version 1;
- identity name exactly `Fairy`;
- a lowercase 64-character SHA-256 digest;
- locale, activity profile, and interaction intensity equal to the start
  envelope;
- all mandatory user-authority and safety flags set to `true`;
- `max_spoken_sentences` in the supported bounded range; and
- bounded strings and arrays throughout the projection.

Unknown or contradictory policy values reject the start before any Backend,
capture device, Voice Worker, or local model is opened.

## Structured dialogue candidate

The existing Worker event name `perception_candidate` is retained, but its
payload becomes a bounded structured candidate:

```json
{
  "decision": "speak",
  "activity": "game",
  "confidence": 0.91,
  "intent": "warn",
  "grounding": [
    "current_window: health is visibly low",
    "current_window: movement remains toward the threat"
  ],
  "text": "血量已经不支持这份自信了，先拉开一点。",
  "urgency": 0.74,
  "needs_online_assistance": false,
  "response_to_user": false,
  "stable": true,
  "persona_digest": "sha256..."
}
```

Allowed decisions are `listen`, `speak`, and `request_assistance`. Activity and
intent are public bounded labels, not hidden reasoning. Grounding contains only
short public observations from the current epoch. It cannot contain raw frames,
audio, prompts, provider payloads, chain-of-thought, credentials, or arbitrary
tool arguments.

Local Omni decision fields are decoded and validated without inventing missing
observations. A Local `speak` candidate with empty grounding is suppressed.
Cloud assistant text deltas are converted to candidates before leaving the
Worker. A response to a stable current user utterance receives the public
grounding marker `current_user_utterance`; an unsolicited Cloud reply does not
receive that marker and must supply current observation grounding.

User captions remain transcription projections and do not pass through the
assistant dialogue policy. Assistant captions cannot bypass the candidate
path.

## RealtimeDialogueDirector

The Director is a pure, bounded policy unit owned by the Tauri coordinator
boundary. This keeps the decision on the low-latency native path while Core
remains the Persona authority.

The Director accepts:

- the current Presence Session, Backend Segment, Context Epoch, and sequence;
- the active Persona digest and voice output;
- Coordinator-owned privacy and speech state;
- the candidate; and
- safe environment gates for lock, do-not-disturb, and sensitive-window state.

It evaluates, in order:

1. current session, segment, epoch, and monotonically increasing sequence;
2. matching Persona digest;
3. valid decision, bounded fields, confidence, and urgency;
4. current grounding or an explicit response to the current user utterance;
5. privacy pause, lock, do-not-disturb, and sensitive-window gates;
6. user-speaking and Fairy-speaking ownership;
7. exact replay of the most recently accepted stable reply;
8. high-risk language and the no-humour rule;
9. unobserved-action claim patterns; and
10. the requested output mode.

An accepted projection contains only public text, stable/streaming state,
speech eligibility, a Coordinator-issued utterance generation, and the current
identity. A rejected candidate exposes only a stable safe reason code.

Phase 4 suppresses only immediate replay of the same stable reply. Time-based
proactive budgets, semantic event deduplication, profile-specific cooldowns,
and Window Epoch rotation policy belong to Phase 5.

`request_assistance` creates only a bounded candidate projection. It does not
run a tool in Phase 4; Phase 6 connects it to Core Assistance and approvals.

## Presence projection

The Coordinator is the sole source of Realtime Presence truth. The public
states are:

- `idle`
- `preparing`
- `loading_model`
- `connecting`
- `listening`
- `observing`
- `thinking`
- `searching`
- `speaking`
- `standby`
- `privacy_paused`
- `resource_limited`
- `error`

Worker lifecycle and media events are inputs to the Coordinator, not states
that React interprets independently. Tauri validates each transition and emits
the current state with session, segment, epoch, sequence, Persona digest, and an
optional bounded level.

The existing `pet-render`, `pet-input`, and `companion` windows remain. The
companion WebView relays the already-authoritative public projection to the
existing Presence channel; it does not infer Backend state. The panel is
renamed from Game Companion to Realtime Companion Beta and keeps the explicit
Local/Cloud Backend label.

The panel may be hidden while the session continues. Hiding does not stop the
Worker, capture, Coordinator, caption persistence, or speech arbiter.

## Fairy Voice short-phrase stream

Fairy Voice is the default output for Local and Cloud. Text-only remains
available on both. Provider-native voice remains a Cloud-only explicit option.
Local continues to reject it.

Approved cumulative assistant text is segmented without waiting for the entire
reply:

- sentence-ending punctuation flushes immediately;
- a comma or semantic boundary flushes once the phrase is useful on its own;
- otherwise a bounded Chinese-character or word threshold flushes;
- stable completion flushes the remainder;
- empty and duplicate chunks are discarded;
- a reply cannot exceed the Persona sentence limit; and
- complex assistance results speak only a short approved summary.

Each chunk starts Fairy Voice synthesis through the existing on-demand Voice
Worker and streams PCM to the AudioWorklet ring. Synthesis for the next chunk
may begin after the prior synthesis completes; playback stays ordered by ring
boundaries. Opening Fairy does not warm Voice.

The Tauri Director issues a speech generation for each accepted utterance.
React may operate the PCM output surface, but it can enqueue only a current,
approved generation. A stale segment, epoch, sequence, or generation is
discarded.

## Barge-in

Backend VAD `speech_started` is the authoritative barge-in signal.

Within the same event turn:

1. the Worker clears provider-native playback;
2. Tauri increments the speech generation and marks the user as speaking;
3. the approved speech queue becomes stale;
4. the WebView clears the AudioWorklet ring and cancels all active and queued
   Fairy Voice synthesis;
5. Presence becomes `listening`; and
6. later chunks from the interrupted generation are ignored.

`speech_stopped` transfers Presence to `thinking` and ends user speech
ownership. The deterministic interruption path must complete within 120 ms in
unit/integration timing tests. Real audio-device latency remains a native
acceptance measurement and cannot be inferred from a static test.

## Failure and privacy behavior

- A malformed snapshot rejects start with a safe protocol code.
- A malformed candidate is suppressed; it does not terminate a healthy media
  session unless it indicates a broken backend protocol.
- A Persona mismatch is a protocol failure and never silently adopts the new
  identity.
- A Voice failure falls back to approved text while the Realtime session stays
  active.
- Privacy pause clears pending speech and media, then projects
  `privacy_paused`.
- No candidate body, caption body, snapshot, prompt, raw media, or provider
  payload is written to logs or diagnostics.
- The Presence projection never exposes the raw Persona snapshot.

## Acceptance

Phase 4 is complete when:

- Local and Cloud starts use the same Core-issued Persona digest;
- invalid snapshots and candidate Persona drift fail closed;
- no assistant caption reaches UI without Director approval;
- Local structured candidates preserve grounding, intent, confidence, urgency,
  and assistance intent;
- immediate duplicate, ungrounded, privacy-blocked, high-risk humour, and
  unobserved-action candidates are suppressed;
- Presence comes from Coordinator projections and React performs no lifecycle
  inference;
- the existing pet shows all Phase 4 states without a new window or app;
- Fairy Voice begins from approved short phrases before stable completion;
- barge-in invalidates queued speech and clears playback within the deterministic
  120 ms budget;
- text-only and Voice-failure fallback preserve captions;
- focused Core, Rust, Vitest, and Playwright tests pass;
- full existing Core, Rust, Vitest, and Playwright suites do not regress; and
- ordinary startup leaves Voice, Realtime, Omni, and model workers cold.

CUDA inference, model downloads, paid live-provider calls, semantic cooldowns,
activity classification, long-duration soak, Core Assistance, durable memory,
release packaging, and production performance claims remain outside Phase 4.
