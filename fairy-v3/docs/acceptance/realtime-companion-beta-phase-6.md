# Realtime Companion Beta Phase 6 Acceptance

Date: 2026-07-28

## Decision

Phase 6 is accepted as the governed Core Assistance bridge for Realtime
Companion Beta.

A Director-approved knowledge gap creates one durable Assistance aggregate,
one scoped main-conversation Task and Assistant Turn, and one full durable
Fairy answer. Only a bounded plain-text spoken summary returns to the current
Realtime Worker. The Companion displays bounded public state, never hosts
approval controls, and can navigate to the linked main conversation.

## Observable behavior and ownership

- Core owns Assistance provenance, idempotency, status, revision, Task, Turn,
  Message, approval, cancellation, restart reconciliation, and the complete
  answer.
- Tauri owns the active Session/Segment/Epoch router, request deduplication,
  bounded polling, current-Worker delivery, stale-session fencing, and the
  public Companion projection.
- The Worker may emit only a bounded Assistance candidate. It never chooses a
  Core tool, receives the full answer, citation body, hidden prompt, raw media,
  or credential.
- Public research requires the explicit online permission. External writes,
  input control, login, payment, messaging, and other Realtime-denied
  capabilities cannot execute through this path.
- A busy linked conversation leaves Assistance queued without replacing its
  active Task. Approval remains in the main Fairy workspace and resumes the
  same durable Turn.
- Cancellation is revision-fenced and delegates through the ordinary
  Assistant cancellation path. The Assistance guard preserves MCP and Sandbox
  cancellation instead of creating a second execution contract.
- A Context Epoch rotation retains unfinished same-session work but delivers a
  result only to the current Worker identity. Session stop cancels unfinished
  work and drops late state.
- Companion reload restores at most 16 bounded public Assistance projections.
  A different Session cannot inherit them.
- `Open main chat` resolves the Conversation through Core. If the Realtime
  conversation was created after the main WebView cached History, navigation
  refreshes that cache before rendering the selected chat.

## Mocked and native boundaries

Core tests use deterministic scripted providers. They prove completion,
approval, restart, cancellation, busy-conversation behavior, tool gating, and
full-answer/spoken-summary separation without paid traffic.

Rust tests replace the Core client and Worker sink. They prove request
deduplication, bounded retry, stale Session/Segment/Epoch rejection, rotation,
stop cancellation, safe public projection, and secret/payload exclusion.

Vitest replaces Tauri event delivery and native window state. Playwright uses
the governed desktop fixture and one controlled Vite lifecycle. Together they
prove Companion rendering, remount recovery, session isolation, cancellation,
main-chat routing, absence of approval controls, 880x680 and 640x700 bounds,
keyboard behavior, and Reduced Motion. They do not prove live provider answer
quality.

A separate, isolated Tauri debug lifecycle was inspected through loopback-only
WebView2 CDP without keyboard or mouse input.

## Certification evidence

The ordered Windows gate completed:

- Focused Core Assistance tests passed 7/7 with deterministic completion,
  approval, restart, cancellation, queueing, and online/tool policy cases.
- Complete Core pytest passed 864 tests.
- Complete Core Ruff checks passed.
- Focused native Assistance router tests passed 8 tests.
- `cargo test --workspace` passed every Rust unit, integration, and doc-test
  target. The credential-gated live GLM test remained explicitly ignored.
- `cargo clippy --all-targets -- -D warnings` passed.
- `npx tsc --noEmit` passed.
- Focused Realtime, Presence, Companion, and App Vitest passed after the
  native navigation repair.
- Complete Vitest passed 95 files / 521 tests. jsdom printed its known Canvas
  implementation warning; no test was skipped or failed because of it.
- Focused Realtime Playwright passed 18 tests, including Assistance at
  880x680 and 640x700.
- Complete Desktop Playwright passed 79 tests. The performance project
  completed in about 1.3 seconds.

Certification found and repaired two independent defects:

- the Assistance tool-executor guard had failed to delegate cancellable MCP
  and Sandbox execution; the complete Core rerun passed after delegation and
  terminal-race fencing were restored;
- native `Open main chat` selected an out-of-cache Conversation ID without
  refreshing History; a new regression test and WebView2 rerun prove that the
  linked chat now becomes the selected durable conversation.

## Native WebView2 evidence

The isolated native check used a disposable data directory:

- the main WebView rendered at 1440x900, reported `CORE READY`, and had no
  horizontal overflow;
- the Companion rendered at 620x760 with no horizontal overflow;
- hide, reopen, and WebView reload restored `Realtime Companion Beta`;
- no `Approve` or `Reject` control existed in the Companion;
- a Conversation and Realtime Session created after the main History query
  were routed through the native `open_realtime_main_chat` command;
- the main WebView switched to Chat mode and persisted exactly the linked
  Conversation ID after the repair;
- ordinary startup contained the Desktop, Local Worker, Core, capability
  broker, and WebView2 processes, while Realtime Worker, Omni runtime, Voice
  Worker, and CosyVoice remained absent;
- shutdown removed the controlled Desktop, Local Worker, Core, capability,
  Vite, and WebView2 processes. Ports 1430, 1431, and 9223 had no listeners.

## Explicitly deferred gates

This phase does not run or claim:

- Docker or PostgreSQL integration environments;
- live Gemini, GLM, OpenRouter, public-web, or MCP answer quality;
- production CUDA MiniCPM-o 4.5 inference or model download;
- microphone, selected-application audio, game capture, audio-device
  barge-in, echo cancellation, or multi-display behavior;
- long-duration Presence, GPU pressure, thermals, game-impact, or four-hour
  soak results;
- Tauri release, installer, production image, or production bundle builds.

Those remain Phase 7-8 or environment-specific certification gates and stay
fail-closed until their explicit requirements are met.
