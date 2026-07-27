# Realtime Companion Beta Phase 4 Implementation Plan

> **Design:** `docs/superpowers/specs/2026-07-28-realtime-companion-beta-phase-4-design.md`
>
> **Authority:** the frozen Phase 0 design and the user-approved Realtime
> Companion Beta final design.

## Goal

Route Local and Cloud assistant output through one Persona-bound dialogue
director, project Realtime Presence from the Tauri coordinator, stream approved
Fairy Voice short phrases, and make barge-in invalidate all speech within the
deterministic 120 ms budget.

## Non-goals

This phase does not implement activity classification, proactive cooldown
budgets, semantic event deduplication, Window Epoch policy, frame gating,
standby policy, Core Assistance execution, durable companion memory, long soak,
GPU governance, packaging, or live provider/model certification.

## Task 1: Validate and project the canonical Persona Snapshot

**Files**

- Create:
  `desktop/src-tauri/crates/realtime-worker/src/persona.rs`
- Modify:
  `desktop/src-tauri/crates/realtime-worker/src/lib.rs`
- Modify:
  `desktop/src-tauri/crates/realtime-worker/src/runtime.rs`
- Modify:
  `desktop/src-tauri/crates/realtime-worker/src/backend/cloud.rs`
- Modify:
  `desktop/src-tauri/crates/realtime-worker/src/backend/local_omni.rs`

**Implementation**

1. Decode the zeroizing snapshot into strict bounded types.
2. Validate schema, digest, canonical Fairy identity, authority flags, locale,
   profile, intensity, sentence limit, and short-memory bounds.
3. Produce one deterministic bounded instruction shared by Local and Cloud.
4. Pass the instruction and digest into both Backend Segment adapters.
5. Reject invalid snapshots before backend connection and capture startup.

**Tests**

- valid canonical snapshots produce the same instruction for Local and Cloud;
- identity, digest, locale, profile, intensity, or mandatory-policy drift fails;
- oversized short memory and unknown schema fail;
- debug and error output never contain snapshot text.

**Commit**

`feat(realtime): enforce canonical persona snapshots`

## Task 2: Preserve structured backend dialogue candidates

**Files**

- Modify:
  `desktop/src-tauri/crates/realtime-worker/src/backend/mod.rs`
- Modify:
  `desktop/src-tauri/crates/realtime-worker/src/backend/local_omni.rs`
- Modify:
  `desktop/src-tauri/crates/realtime-worker/src/backend/cloud.rs`
- Modify:
  `desktop/src-tauri/crates/realtime-worker/src/provider.rs`
- Modify:
  `desktop/src-tauri/crates/realtime-worker/src/protocol.rs`
- Modify:
  `desktop/src-tauri/crates/realtime-worker/src/runtime.rs`
- Modify native Omni contract files only if required to preserve fields already
  emitted by the pinned runtime.

**Implementation**

1. Add bounded candidate decision, activity, intent, grounding, text,
   confidence, urgency, online-assistance, response-to-user, stable, and Persona
   digest fields.
2. Preserve Local Omni decision fields instead of reducing them to text.
3. Convert Cloud assistant caption deltas into candidates; keep user captions
   as transcription events.
4. Track only the public marker that a current stable user utterance exists.
5. Reject invalid floats, unknown decisions, oversized fields, or invalid
   grounding at the adapter boundary.
6. Keep Worker event names compatible with the frozen v2 event vocabulary.

**Tests**

- Local `listen`, `speak`, and `request_assistance` map correctly;
- ungrounded Local text remains a candidate for Director suppression;
- Cloud assistant captions cannot bypass `perception_candidate`;
- current user utterance grounding is scoped to one response;
- every protocol event still round-trips under the control frame bound.

**Commit**

`feat(realtime): preserve structured dialogue candidates`

## Task 3: Add the Tauri RealtimeDialogueDirector and speech arbiter

**Files**

- Create:
  `desktop/src-tauri/src/realtime_dialogue.rs`
- Modify:
  `desktop/src-tauri/src/realtime_coordinator.rs`
- Modify:
  `desktop/src-tauri/src/realtime_worker.rs`
- Modify:
  `desktop/src-tauri/src/lib.rs`

**Implementation**

1. Add pure candidate, gate, decision, rejection, output-mode, and utterance
   generation types.
2. Bind the Director to the Coordinator’s session, segment, epoch, sequence,
   Persona digest, and requested voice output.
3. Enforce grounding, environment gates, speech ownership, immediate duplicate,
   high-risk humour, and unobserved-action checks.
4. Transform accepted candidates into public captions or bounded assistance
   candidates before Tauri emits them.
5. Never emit raw backend assistant captions or rejected candidate bodies.
6. Increment the speech generation on barge-in, privacy pause, stop, backend
   failure, and segment/epoch changes.
7. Keep rejection diagnostics to stable safe codes and bounded counters.

**Tests**

- stale identity/sequence and Persona mismatch reject;
- ungrounded proactive text rejects while a current user response is accepted;
- privacy, lock, DND, sensitive window, and speech ownership suppress;
- exact stable replay suppresses;
- high-risk humour and unobserved-action claims suppress;
- text-only accepts a caption but not speech;
- barge-in invalidates every prior generation within 120 ms.

**Commit**

`feat(desktop): govern realtime dialogue output`

## Task 4: Make Presence a Coordinator projection

**Files**

- Modify:
  `desktop/src-tauri/src/realtime_coordinator.rs`
- Modify:
  `desktop/src-tauri/src/realtime_worker.rs`
- Modify:
  `desktop/src/realtime/realtimePresence.ts`
- Modify:
  `desktop/src/presence/PresenceBridge.tsx`
- Modify:
  `desktop/src/realtime/RealtimeCompanion.tsx`
- Modify related Rust and Vitest tests.

**Implementation**

1. Add the frozen Phase 4 Presence state enum and legal transitions.
2. Attach session, segment, epoch, sequence, and Persona digest to every native
   projection.
3. Map Worker lifecycle/media inputs in Tauri.
4. Emit only Coordinator projections to the companion.
5. Remove React lifecycle inference from `session_state`.
6. Map public states to existing pet visual states without creating a window.
7. Preserve the current instance/sequence fencing in the Presence channel.

**Tests**

- legal lifecycle reaches every public state;
- stale or regressive projection is ignored;
- Persona digest never appears as raw snapshot content;
- React does not infer Presence from terminal/session strings;
- companion hide/show retains the active projection.

**Commit**

`feat(desktop): project realtime presence authoritatively`

## Task 5: Stream approved Fairy Voice phrases and harden barge-in

**Files**

- Create:
  `desktop/src/realtime/realtimeSpeech.ts`
- Create:
  `desktop/src/realtime/realtimeSpeech.test.ts`
- Modify:
  `desktop/src/realtime/RealtimeCompanion.tsx`
- Modify:
  `desktop/src/realtime/RealtimeCompanion.test.tsx`
- Modify:
  `desktop/src/voice/nativeVoice.ts`
- Modify related AudioWorklet tests if required.

**Implementation**

1. Segment cumulative approved assistant text at sentence, useful comma, and
   bounded semantic-length boundaries.
2. Flush the remainder on stable completion without replaying prior text.
3. Pipeline synthesis using `readyForNext` while preserving playback order.
4. Track all active playback handles and the Coordinator speech generation.
5. On barge-in, cancel every synthesis handle, clear queued phrases and the
   AudioWorklet ring, and ignore late chunks.
6. Keep approved captions visible when Voice fails and expose one bounded
   warning.
7. Do not start the Voice Worker until the first approved phrase.

**Tests**

- first useful phrase starts before stable completion;
- cumulative deltas do not replay text;
- Chinese and English boundaries remain bounded;
- stable completion flushes one remainder;
- a new utterance cannot append to the interrupted generation;
- barge-in clears queued and active playback under 120 ms;
- Voice failure retains captions and session state.

**Commit**

`feat(desktop): stream governed Fairy voice`

## Task 6: Generalize the companion and certify Phase 4

**Files**

- Modify:
  `desktop/src/realtime/RealtimeCompanion.tsx`
- Modify:
  `desktop/src/realtime/realtime-companion.css`
- Modify:
  `desktop/src/presence/input/PresencePanel.tsx`
- Modify related component and Playwright tests.
- Create:
  `docs/acceptance/realtime-companion-beta-phase-4.md`

**Implementation**

1. Rename Game Companion user-facing labels to Realtime Companion Beta.
2. Keep the explicit Local/Cloud Backend identity and privacy text.
3. Present the new Coordinator states without changing the existing restrained
   Fairy visual system.
4. Add fixture coverage for Persona parity, Director suppression, Presence,
   short-phrase streaming, text-only fallback, and barge-in.
5. Record which native/live requirements were measured and which remain
   deferred.

**Validation order**

1. `cargo fmt --all -- --check`
2. focused realtime-worker and Desktop Rust tests
3. `cargo clippy --workspace --all-targets -- -D warnings`
4. full Rust workspace tests
5. focused Core Persona/Realtime tests
6. full Core tests
7. `npx tsc --noEmit`
8. focused Vitest
9. full Vitest
10. focused Realtime Playwright
11. full Playwright
12. controlled Tauri dev WebView2 inspection when no user input competes
13. process, port, generated-output, and `git status` cleanup

No Docker, release, installer, production image, model download, CUDA inference,
or paid live-provider call runs in this phase.

**Commit**

`test(realtime): certify persona projection`
