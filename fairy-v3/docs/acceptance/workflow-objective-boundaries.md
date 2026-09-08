# Compound request objective boundaries

## Gap and authority

The existing immutable execution intent records ordered objectives and dependencies,
but the tool policy mostly checks its top-level action. A request such as
"inspect the README, explain the problem, then fix it" therefore lacks a durable
boundary between inspection and modification. A model's statement that it has
finished inspection is not sufficient evidence to open that boundary.

This is a continuation of recovery Phases 2/6, not a new task center, agent system
or content-filter product. Original user text, quoted/code segments, classifier
fallback, Scope, Command policy, approvals and existing tool budgets remain.

## Execution design

- Freeze the interpreted objective list and interpretation revision in the new
  engine's route continuation. Execute in declaration order (which respects every
  backward dependency); this version does not parallelize separate objectives.
- Add real objective-begin and objective-complete Workflow nodes around the
  existing model/tool/fan-in/verification chain. Their identity includes Run,
  Workflow plan revision, interpretation revision and objective index. They use
  existing Kernel leases, fences and atomic graph publication, not another queue.
- Each model/tool/verify node carries its objective binding. Tools still have the
  original Invocation and Command identity. A forged/stale objective reference
  cannot run tools or publish a completion certificate.
- Effective tool authority is the intersection of the original intent and the
  active objective. An objective can narrow the original action, never expand it.
  Thus a top-level review remains read-only even if an inconsistent classifier
  inserts a later change objective. Global source prohibitions and targets remain
  mandatory. A change request with an initial review objective permits only
  necessary reads/internal evidence until that objective has passed verification.
- Derive active objective state from owned, persisted Workflow nodes, not a
  front-end selection or mutable process-global variable. If using an in-memory
  projection field on ExecutionIntentSnapshot, exclude it from persisted JSON and
  reject an unexpected stored projection. Explicit historical intent reads stay
  immutable. Restrict objective projection reads to 32 begin/complete records plus
  the one route marker (one extra row detects overflow), not the complete tool graph
  or conversation history. Single-objective work makes no projection query.
- Model context identifies the current objective and remaining objectives. Prior
  certified public summaries/evidence are supplied as completed facts, not fresh
  permission grants. Intermediate drafts must not become extra final chat messages
  or concatenate confusingly into the final response stream.

## Completion evidence

- Objective completion requires a matching verified draft and successful owned
  prerequisite nodes. The model cannot call an unrestricted "objective complete"
  tool or edit completion state.
- For a read/review objective before editing existing Workspace files, require
  actual in-scope current file evidence in addition to the public analysis draft.
  A list/index receipt alone cannot substitute for reading the referenced content.
  Empty/new scratch work can use the bound source message as its input, but cannot
  claim existing-file inspection without a file receipt.
- Existing evidence requirements/citation validation remain in force. Completing
  a mutation objective additionally requires its real Changeset/file-plan outcome;
  run/review actions require their actual command outcomes. Waiting approvals,
  unfinished work, failed or uncertain effects do not satisfy a dependency.
- Store a certificate in the complete node's result: original interpretation and
  objective identity, source-message identity/hash, bounded public summary, draft
  hash and verified receipt/outcome references. This is Kernel-written evidence,
  not authority copied from model tool arguments.
- Only the final objective may proceed to the existing unique final reply and
  Turn/Workflow settlement. Normal/Deep budgets span the whole Run unchanged.

## Steering, restart and compatibility

- A new user instruction creates the existing new plan/intent revision. Unstarted
  old objective nodes are superseded. Completed old facts remain available, but
  old certificates do not satisfy new revision dependencies automatically.
- A node interrupted before/after certificate or next-node publication resumes
  by its original identity; it cannot duplicate writes, approvals or replies.
- Pure single-objective chat retains the minimal chain and no objective queries.
- Existing nonterminal engine-3 work continues unchanged. Engine 4 remains opt-in
  until joint acceptance. A versioned continuation marker distinguishes any
  already-persisted pre-objective engine-4 chain; never run two schedulers for one
  Turn or reinterpret an in-flight side effect as a new objective.

## Tests and acceptance

1. RED: inspect/explain then change must not offer or dispatch a write before
   verified in-scope read evidence and analysis completion.
2. Exercise actual scoped project reads, Changeset approval/apply and final reply
   through the real service with a scripted classifier/model. Include a second
   conversation/project, quoted commands and an inconsistent/low-confidence
   classifier; zero expanded authority.
3. Reject forged completion IDs, stale interpretation/plan revisions, absent
   evidence, repeated completion and cross-Turn certificates.
4. Interrupt at begin, after reads, before/after certificate, during approval and
   between objectives; close/reopen temporary SQLite and retain exactly one effect
   and one final reply. Keep completed facts through Steering without reusing their
   old permission state.
5. Verify bounded projection queries, unchanged single-objective latency path,
   model/tool budgets and global worker limits. Activity/Composer/sidebar/Preview
   regressions remain part of the final Desktop gates.

These deterministic checks do not replace real provider, WSL, PostgreSQL or native
desktop acceptance. This document defines pending implementation, not completion.

## Incremental implementation evidence

- The initial real-service regression failed because `edit.propose_changeset` was
  offered during the analysis draft after `execution.plan`. Objective begin/complete
  nodes now delimit the actual model/tool/join/verify chain. Each phase intersects
  root authority and validates its active interpretation before dispatch.
- New continuations carry a route-only protocol marker. The request-interpretation
  node keeps its existing strict payload contract; persisted old continuations are
  not reinterpreted. Engine 4 is still opt-in.
- In the temporary SQLite integration scenario, the real scoped `project.read`,
  file plan, approval and apply chain leaves source files unchanged, updates only
  the managed Workspace and emits one final reply. A missing file read causes
  bounded correction before any mutation phase. Approval close/reopen retains
  progress. Scripted classifier/provider output is the only model boundary.
- Remaining before compound objectives are complete: read-after-write freshness
  and duplicate-call identity, multiple mutation objectives/file-plan generations,
  additional interruption and Steering boundaries, terminal effects beyond file
  changes, and final cross-module/native gates. These are not covered by a passing
  two-objective scenario and are not marked complete.
- Verification: policy/repository/step/model/review/control regression **91 passed**
  in the 51.58-second run; final three objective integration cases **3 passed in
  8.82 seconds** after fixing only their corruption fixture. Fixture corrections
  converted immutable result mappings to JSON dictionaries and used a mismatched
  conversation binding instead of violating the existing unique Run owner key.
  Production uniqueness/ownership constraints were not weakened. Ruff passed.
- Existing tests changed only by adding five policy cases and an optional
  `objectives` parameter to the scripted classifier helper (the original default
  output is unchanged). All other assertions were retained.
