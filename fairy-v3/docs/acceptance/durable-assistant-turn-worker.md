# Assistant Workflow Migration Acceptance

This document records the compatibility boundary after the durable Workflow Kernel became the
only Assistant execution engine. Detailed runtime acceptance lives in
`durable-agent-workflow-kernel.md`.

## Current contract

- Every newly created Assistant Turn has `execution_engine_version = 2`, a non-null
  `workflow_run_id`, and one immutable execution target.
- `assistant.turns.start`, pause, resume, cancel, retry, workflow get, and steering are facades
  over the Workflow Kernel. There is no Assistant-specific queue or background Worker.
- Terminal engine-version-1 Turns remain readable as conversation history and are never resumed.
- A non-terminal engine-version-1 or unbound Turn blocks SQLite/PostgreSQL upgrade. Operators must
  finish, cancel, or explicitly recover that Turn using the old release before upgrading.
- The migration removes `core_assistant_turn_work` only after that guard succeeds.

## Required regression evidence

- SQLite migration preserves terminal history, blocks unfinished legacy work, and is idempotent.
- PostgreSQL migration has the same guard, drops the old RLS table, and has a reversible downgrade.
- Workflow lease/fence and tenant RLS tests replace old Assistant queue claim tests.
- Restart tests prove one final message and recovered partial projection without an old queue.
