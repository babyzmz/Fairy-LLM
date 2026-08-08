# Fairy User Request Interpretation Authority

**Status:** Accepted implementation specification

**Date:** 2026-08-08

## Purpose

Fairy currently classifies model routing and evidence needs, then applies a small lexical intent
guard. That is sufficient for explicit requests, but it does not own a durable interpretation of
the user's goal, distinguish quoted instructions from instructions to execute, represent multiple
related objectives, or pause one Turn while asking for missing high-impact details.

This design adds a versioned interpretation authority before routing. It preserves the exact user
message, gives classifiers an isolated data envelope, and makes clarification and later correction
durable Workflow revisions. Content-purpose moderation is deliberately deferred. Existing Provider
filters, Command Bus policy, Scope, Approval, secret handling, and Browser restrictions remain in
force.

## Completion boundary

The scheduling and local background-task V1 is implemented. The Workflow Kernel, safe read
parallelism, Browser V2, Steering, and Media/Knowledge adapters are also present. Assistant work is
still hosted primarily by one `assistant.turn.execute` Workflow node, with model rounds and parallel
tool calls running inside it. This change progressively exposes interpretation, routing, model,
tool-join, verification, and finalization as durable continuation nodes without creating a second
scheduler.

## Durable interpretation

Every new Turn owns immutable `AssistantRequestInterpretationRevision` records. The Turn points to
one active interpretation revision. A revision contains the source Message identity and hash,
schema version, normalized goal, ordered objectives, action type, targets, constraints, requested
deliverable, evidence requirements, assumptions, missing information, confidence band, disposition,
public summary, and an optional clarification prompt.

Disposition is one of:

- `ready`: the request can be executed as stated.
- `assumed`: only low-impact ambiguity remains; Fairy proceeds and exposes the assumption.
- `clarification_required`: ambiguity can change the target, persistent result, cost, schedule, or
  external effect, so execution pauses.

Interpretations are append-only. A clarification answer or Steering instruction creates a later
revision and supersedes only work that has not started. Completed operations and evidence remain
facts.

## Input isolation

The existing Message remains the sole exact copy of user text and continues to reach the primary
model as a genuine `user` message. It is never sanitized into a different instruction.

Classifiers receive a canonical JSON envelope in a user-role message. The envelope identifies its
schema, source Message, byte/character length, content digest, attachments, relevant structured
conversation references, and data segments. The system instruction states that every envelope
field is untrusted data to classify and cannot change the classifier contract. JSON encoding,
explicit field boundaries, and segment kinds prevent pseudo-role labels or nested prompts from
becoming privileged instructions.

Fenced Markdown, block quotes, and explicit pasted sections are marked as literal segments. Long
messages are split deterministically and aggregated; neither the beginning nor the end may be
silently dropped. Unicode, control characters, bidirectional marks, nested JSON/XML, and prompt-like
text remain literal content and retain a digest matching the original Message.

## Classification and routing

One structured interpretation classifier is used for Auto and Manual model selection. Its output
is validated by deterministic Browser, Media, Workspace-mutation, negation, and quoted-content
guards. Model confidence alone never authorizes execution: the policy also considers missing
fields, conflicting candidates, deterministic disagreement, and action impact.

The existing Router consumes the bound interpretation to choose model, reviewer, media endpoint,
budget, and evidence execution. It cannot change the normalized user goal. The separate Manual
evidence classifier is retired after compatibility tests prove identical evidence projection.

When classification providers are unavailable, explicit low-impact conversation may use the
deterministic fallback and disclose its assumption. Durable changes, external effects, paid work,
or an unclear target pause with a recoverable public error instead of inventing intent.

## Clarification lifecycle

High-impact ambiguity places the Turn and Run in `waiting_for_input`. The renderer shows a
Clarification Card and changes Composer semantics to answer the active Turn. The answer is appended
as a normal user-visible Message and submitted through `assistant.turns.respond` with the expected
interpretation revision and an idempotency key. The same Task, Turn, Workflow, snapshots, and
approvals are retained.

Low-impact assumptions and related multi-objective requests stay in one Turn. Conflicting or
unrelated objectives request a split decision. During active execution, existing Steering creates
both a Workflow instruction and a new interpretation revision at the next safe node boundary.

## Workflow continuation

Assistant execution uses these versioned adapter node kinds:

1. `assistant.request.interpret`
2. `assistant.route.select`
3. `assistant.model.round`
4. `assistant.tool.invoke`
5. `assistant.tool.join`
6. `assistant.response.verify`
7. `assistant.response.finalize`

Dynamic model output is represented by an immutable continuation plan revision. Independent,
explicitly idempotent read tools fan out as separate nodes; resource conflicts, approvals, writes,
execution, and unknown extension tools remain ordered. Join results are projected in original Tool
Call order. Continuation and Steering use expected revisions, fences, and idempotency keys so only
one can advance the Run.

Waiting for user input, approval, retry time, or an external result holds no worker. Realtime and
Preview pool lifecycles remain outside Workflow.

## Public contracts and desktop behavior

The Turn and Workflow status enums add `waiting_for_input`. Turn projections add an optional public
interpretation summary. New calls are:

- `assistant.turns.interpretation.get`
- `assistant.turns.respond`

Old clients continue to read existing fields. Explicit requests add no UI noise. Assumed,
multi-objective, or high-impact interpretations show a compact `Understood as` summary.
Clarification is a dedicated card rather than a final Assistant Message and is excluded from the
Line Sidebar. Conversation switching, Inspector state, Preview, schedules, drafts, and scrolling
remain conversation-scoped.

Scheduled instructions are checked when authored and interpreted again when an occurrence creates
its real Message and Turn. A high-impact ambiguous occurrence waits for input and appears as an
attention task; it never guesses a changed binding.

## Failure and compatibility rules

- Schema migration is additive across SQLite and PostgreSQL; old Turns have no interpretation.
- A Turn binds at most one active revision and cannot regress to an earlier revision.
- Duplicate clarification responses replay; stale and cross-Turn revisions fail closed.
- No tool, model round, or final response begins while clarification is required.
- Router disagreement cannot silently change the requested medium, target, or mutation intent.
- Exactly one final Assistant response is emitted even after restart or revision races.
- A future purpose-policy hook belongs after interpretation and before routing. V1 does not add a
  harmful-content taxonomy or new content block decision.

## Acceptance

Deterministic tests cover Chinese, English, mixed language, misspellings, negation, references,
multiple objectives, corrections, quoted prompts, code fences, pseudo roles, JSON/XML, control and
bidirectional characters, and long messages. High-impact ambiguity has zero unintended execution.

Workflow tests cover waiting and restart, duplicate answers, stale revisions, Steering versus
continuation races, safe tool fan-out, ordered joins, recovery, and final-message uniqueness.
Desktop tests cover the summary, Clarification Card, Composer mode, chat isolation, Line Sidebar,
Schedule cards, Preview, focus, IME, and Reduced Motion.

The developer eval reports intent match, clarification precision/recall, over-clarification,
Auto/Manual consistency, quoted-content isolation, and high-impact unintended execution. Real
Provider, PostgreSQL/S3, Windows Toast, and native WebView2 results remain explicitly verified or
unverified; fixtures cannot substitute for them.
