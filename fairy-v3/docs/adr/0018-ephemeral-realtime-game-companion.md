# ADR 0018: Ephemeral Realtime Game Companion

## Status

Accepted for Fairy V3 Windows-first development.

## Context

The game companion needs low-latency, full-duplex speech and visual understanding
without turning live play into a durable transcript or a second execution path.
The existing Core owns durable state and all side effects. The Pet is a projection
surface and must not acquire project, approval, or command authority.

## Decision

Fairy runs realtime media in a separate, non-elevated Rust worker whose lifetime is
bound to the desktop process. The worker captures the microphone, optional audio
from the selected game process, and only the selected game window. It connects to
one provider for the complete session: Gemini Live, GLM Realtime Flash, or GLM
Realtime Air. Auto resolves once from the locale and never silently switches during
a session.

Raw microphone and game audio, captured frames, provider conversation items, VAD
events, captions, and hidden reasoning are transient worker memory. They are never
written to SQLite, PostgreSQL, the Ledger, logs, events, crash reports, or sync.
Queues are bounded, latest-frame-wins, and media buffers are cleared after use and
again when the worker stops or crashes.

Core persists only a low-content session audit: provider/model, consent flags,
status, aggregate durations and counts, safe error code, and timestamps. After a
session ends, the user may explicitly save a structured `GameMemoryDigest` of at
most 2 KiB containing the game, activities, progress, next goal, and notable
outcome. It is not derived or saved automatically.

Realtime provider setup advertises no tools in this milestone. Unexpected tool
calls are rejected with a bounded public result. A future realtime tool must travel
Worker -> Tauri -> Core Command Bus with a real Conversation/Task Scope and policy
check; it may not execute in the worker or Pet. Keyboard and mouse control are not
part of this capability.

Native provider speech is played directly with barge-in. Fairy voice uses the
existing local voice path and consumes only public, confirmed caption intervals.
Provider or media failure terminates the session with an explicit error and a user
initiated reconnect; there is no silent provider fallback.

## Consequences

- The database cannot reconstruct a realtime conversation.
- Game progress memory remains useful but intentionally lossy and user-controlled.
- Realtime failures are isolated from Core, the main workspace, and the Pet.
- Adding realtime tools later requires scoped Command Bus contracts rather than a
  provider-specific shortcut.
