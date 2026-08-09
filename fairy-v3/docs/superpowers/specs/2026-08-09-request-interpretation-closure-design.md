# Fairy Request Interpretation Closure

**Status:** Proposed remediation specification

**Date:** 2026-08-09

## Purpose

The request interpretation authority has a durable schema, classifier envelope, clarification UI,
and Workflow waiting state, but it does not yet enforce every accepted behavior. This remediation
closes the gaps without adding harmful-content purpose filtering. Provider moderation, Command Bus,
Scope, Approval, Browser restrictions, and secret handling remain independent safeguards.

## Completion rule

The work is complete only when behavior is enforced by Core and measured by tests. A schema shared
by Auto and Manual modes is not sufficient if one mode bypasses classification. A test scenario
label is not a metric. Workflow node names are not sufficient unless their attempts own the actual
durable boundary and recovery behavior.

## Deterministic intent policy

Add a versioned `RequestIntentPolicy` after classifier parsing and before routing. It consumes the
validated interpretation plus Core-owned context references; it never trusts the classifier's
confidence or `missing_information` alone.

- `change`, `run`, and `manage` require an explicit target or a Core-bound target reference.
- `schedule` requires a complete structured schedule rule before it can execute.
- `create` and `generate` require an explicit deliverable or medium.
- Conflicting actions, unresolved pronouns, or an action/route disagreement become
  `clarification_required`.
- Read-only answer, explanation, and review may proceed with a disclosed low-impact assumption.
- Policy output can narrow an action or require clarification, but cannot authorize a broader
  action than the classifier requested.

The policy is used for new turns, Manual mode, scheduled authoring, scheduled occurrences,
clarification revisions, and Steering revisions.

## Clarification revisions

A clarification response is first persisted as a user Message and idempotent response receipt. It
does not automatically clear missing information or force `ready`.

The Workflow resumes into a new interpretation attempt using the original request, all accepted
clarification Messages, and Core-owned references. The resulting immutable revision may be
`ready`, `assumed`, or request another clarification. It may revise targets, constraints,
deliverable, action, and objectives while retaining the source Message hashes. Duplicate response
keys replay the same receipt; stale and cross-Turn revisions fail closed.

No model round, tool call, or final response can begin while the active revision still requires
clarification. Repeated unclear answers stay in `waiting_for_input` without consuming an execution
worker.

## Literal input segmentation

Replace the current line-only splitter with a deterministic Markdown-aware scanner that preserves
the exact source string and marks these ranges as literal data:

- fenced and indented code blocks;
- inline code spans, including variable-length backtick delimiters;
- Markdown block quotes;
- balanced ASCII and Unicode quotation marks;
- attached or explicitly delimited pasted-text blocks.

Unclosed delimiters fail safe by keeping the remainder literal when treating it as executable text
could broaden the route. Segment concatenation must reproduce the exact source and its digest.
The lexical guard examines only actionable text segments and may narrow, never broaden, a
structured interpretation.

Long requests are classified through bounded chunk attempts and a deterministic fan-in summary;
splitting one oversized JSON request into fields is not considered chunked classification.

## Auto, Manual, and Schedule parity

All modes use the same interpretation payload, deterministic policy, attachment metadata, and
literal segmentation.

- Manual media endpoints use a classification-capable coordinator before binding the selected
  generation endpoint.
- Manual evidence classification receives the real attachment count and Core references.
- If no compatible classifier is available, low-impact conversation may use the deterministic
  fallback; mutation, execution, paid media, schedules, and external effects wait for input or
  return a recoverable availability error.
- Schedule create and update run the deterministic intent preflight before persistence and store a
  bounded public interpretation summary with the schedule revision.
- Each occurrence performs the full current interpretation again before execution. A changed or
  ambiguous binding pauses that occurrence for attention rather than guessing.

## Durable Workflow boundaries

Replace the two-node Assistant plan with real versioned continuation nodes:

1. `assistant.request.interpret`
2. `assistant.route.select`
3. `assistant.model.round`
4. `assistant.tool.invoke`
5. `assistant.tool.join`
6. `assistant.response.verify`
7. `assistant.response.finalize`

Each node persists only bounded references and immutable continuation state. Tool nodes use the
existing Command Bus and resource conflict keys. Safe read tools may fan out; writes, approvals,
unknown extensions, and uncertain side effects remain serial. Join results retain original tool
call order. Finalization owns the unique final Message write.

Legacy engine-version-2 runs finish on the current two-node adapter. New turns bind to the new
engine version; a Turn never switches engines midway. Migration waits until no old nonterminal run
remains before deleting compatibility code.

## Evaluation truthfulness

Add a labeled interpretation corpus covering Chinese, English, mixed language, misspellings,
negation, references, unrelated objectives, corrections, quotes, code, pasted text, control and
bidirectional characters, long input, repeated clarification, and schedule authoring.

The deterministic report calculates from per-case outcomes:

- intent/action accuracy;
- clarification precision and recall;
- over-clarification rate;
- target/deliverable extraction accuracy;
- Auto/Manual consistency;
- literal isolation rate;
- actual count of execution nodes or side-effect commands started before required clarification.

The parallel performance result comes from measured serial and parallel samples; no constant label
is reported as a passed metric. Real Provider results remain separate and unverified when no live
artifact is supplied.

## Migration and desktop closure

- PostgreSQL downgrade handles or explicitly blocks active `waiting_for_input` rows before
  restoring old constraints; upgrade and downgrade are exercised against a real temporary
  PostgreSQL database when available.
- SQLite upgrade remains idempotent and preserves interpretation revisions.
- Clarification Card supports repeated questions and conversation isolation.
- Playwright covers Card to Composer response, repeated clarification, restart projection, IME,
  narrow windows, Line Sidebar exclusion, schedules, Preview state, and Reduced Motion.
- The known Presence GPU renderer timeout remains a separate native defect and is not hidden by
  this request-understanding closure.

## Commit order

1. `docs(design): define request interpretation closure`
2. `fix(agent): enforce deterministic request intent policy`
3. `fix(agent): reclassify clarification revisions`
4. `fix(agent): isolate quoted and pasted request content`
5. `fix(agent): align manual and scheduled interpretation`
6. `refactor(agent): persist assistant continuation nodes`
7. `test(agent): measure request interpretation quality`
8. `fix(core): harden interpretation migrations`
9. `test(desktop): gate clarification workflow UX`

Each implementation commit includes its direct regression tests. Critical or Important defects
found during implementation are fixed in an independent Conventional Commit before proceeding.

## Verification boundary

Run Ruff, Core, Capabilities, Cloud contracts, TypeScript, Vitest, Rust and Clippy, chat
Playwright, full Playwright, the deterministic eval, and the process-residue audit. Real Provider,
PostgreSQL/S3, WSL Sandbox, and native WebView2 are reported separately and never inferred from
fixtures. Development does not run Docker, Tauri Release, or production image builds.
