# Workflow model-command failure settlement

A Workflow failure may settle its Turn/Task projection with its own accepted node
Fence. It does not inherit the current lease of a Model Command from the database.
An expired `model.generate` Command may be claimed with a new Fence solely to
record failure, never to replay the Provider. A live different worker's Command
remains untouched; public Trace failure is a scoped domain projection, not a claim
that its Provider operation stopped.

Acceptance uses two distinct conversations and temporary SQLite, expired and
taken-over Model Commands, a live Workflow claim, and zero Provider invocations.
Failure of the selected Turn/Run is atomic and does not change the other task or
Command. Native Provider shutdown and billing outcome are not inferred by this gate.

## Evidence

- RED: expired lease raised `WorkerFenceError`, rolling back failure settlement;
  a live takeover was incorrectly transitioned to failed using its owner's Fence.
- Expired model commands now acquire a fresh claim for failure recording only;
  live claims are preserved. No Provider request is replayed.
- Both direct scenarios and step failure/Trace/model/Review regression passed:
  16 tests in 25.35 seconds; Ruff passed.
- Live foreign commands remain under their owner's lifecycle. Deadline projection
  and cleanup of orphaned terminal-run resources remain separate recovery work.
