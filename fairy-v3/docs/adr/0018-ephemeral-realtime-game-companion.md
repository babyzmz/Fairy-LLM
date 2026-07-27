# ADR 0018: Ephemeral Realtime Game Companion

> **Superseded in part by ADR 0022.** The game-only product definition,
> single-provider user-session model, and React-owned Companion lifecycle in this
> ADR are historical. ADR 0022 defines the governed Realtime Companion,
> Presence Session / Backend Segment / Context Epoch lifecycle, and Local/Cloud
> backend boundary. This ADR's device-local stable-public-caption amendment
> remains normative and is incorporated by ADR 0022.

## Status

Accepted for Fairy V3 Windows-first development. Amended 2026-07-24 to persist
stable public captions locally so a voice session is reviewable in its linked
conversation (see "Amendment: reviewable captions").

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
events, interim (unstable) captions, and hidden reasoning are transient worker
memory. They are never written to SQLite, PostgreSQL, the Ledger, logs, events,
crash reports, or sync. Queues are bounded, latest-frame-wins, and media buffers
are cleared after use and again when the worker stops or crashes. Stable public
captions are the sole exception and are handled under "Amendment: reviewable
captions" below.

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

## Amendment: reviewable captions

Starting a voice session auto-links a fresh Conversation (ADR-superseding change
already shipped) so the session appears in history. To make that history actually
readable, Core persists the **stable, public captions** — the same confirmed
intervals already shown on screen and fed to Fairy voice — to a device-local table
`core_realtime_transcript_entries`, keyed by session and denormalized to the linked
`conversation_id`. Sequences are server-allocated and monotonic, so a Pet window
remount can never collide or reorder entries.

This carve-out is deliberately narrow:

- Only stable captions are written. Interim captions, raw audio, frames, provider
  conversation items, VAD events, and hidden reasoning remain transient worker
  memory exactly as above.
- The `realtime.transcript.append` and `realtime.transcript.list` Core methods are
  `LOCAL_ONLY`. Captions never route to the cloud, never enter the outbox/sync path,
  and never appear in the Ledger, logs, events, or crash reports. The Companion
  window's Core allow list grants only `realtime.transcript.append`; listing is a
  main-window read.
- The cloud schema carries a mirror table for metadata parity, but because the
  methods are `LOCAL_ONLY` and no projection targets it, that table is never
  populated. Captions physically remain on the user's device.
- The captions carry no new content beyond what the session already displayed; they
  are not raw audio and cannot reconstruct voice or video.

## Consequences

- The database cannot reconstruct realtime **audio or video**; it can now replay the
  public caption text of a voice session on the local device only.
- Game progress memory remains useful but intentionally lossy and user-controlled.
- Realtime failures are isolated from Core, the main workspace, and the Pet.
- Cloud/PostgreSQL still cannot reconstruct a realtime conversation: captions are
  device-local and never synced.
- Adding realtime tools later requires scoped Command Bus contracts rather than a
  provider-specific shortcut.
