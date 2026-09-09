# Scratch plain-file delivery checkpoint

## Reproduction and invariant

A real temporary scratch conversation requests README.md, creates an execution
plan, approves and applies the file, then returns its final summary. No application
entrypoint or Preview is requested. Both engine 3 and engine 4 fail: the unsupported
runtime-template path skips Preview correctly but also skips the actual scratch
checkpoint; final promotion rejects the unfinished checkpoint step. Engine 4's
failure is `completed scratch delivery has unfinished execution steps`.

The fix must save the actual managed candidate before promoting it, not merely
mark a step complete. Keep explicit project review behavior unchanged. Do not
start a Browser/Preview/Voice worker for a plain document. A failed checkpoint must
not produce a successful final reply or promote a partial candidate.

## Acceptance

- Scripted classifier/provider; real service, Scope, approval/apply, SQLite and
  managed workspace. Core tests use the development FileSystemWorkspaceProvisioner
  whose checkpoint computes a content digest, not the native Rust Git worker.
  Observe the actual checkpoint invocation and file contents in engines 3 and 4.
- Create a second scratch task and verify its plan/state are untouched.
- After completion, the managed file and contents seen by the checkpoint must match
  the requested content; the conversation points to the saved candidate and no
  runtime exists. Exactly one final response, no bypass of the file approval.
- Native acceptance must separately verify `git show HEAD:README.md` using the Rust
  Workspace worker. The development adapter's digest is not proof of a Git commit.
- Inject checkpoint failure and verify no promotion/success; no raw exception text
  or sensitive paths should become a final user-facing message.
- Run relevant execution, scratch/Workspace, Assistant completion and objective
  tests. Real Provider/native/WSL remain separate acceptance boundaries.

Initial RED: two failures in 14.53 seconds, including the exact unfinished-step
exception on engine 4. This document does not claim the fix is already verified.

The local-worker crate is a library used by `fairy --local-worker`, not a standalone
binary. For a reproducible backend-only native gate, `examples/stdio_worker.rs`
calls the same `run_stdio` implementation without linking a Tauri window. Build it
with `cargo build --locked --offline -p fairy-local-worker --example stdio_worker`,
then set `FAIRY_ACCEPTANCE_LOCAL_WORKER` to that executable and run
`pytest tests/execution/test_scratch_text_delivery.py`. Without this explicit
configuration, the two native scenarios report skipped instead of pretending to
have exercised Git. Provider output is still scripted; native Workspace/Git is real.

## Verification evidence

- Minimal production correction: in the non-Preview branch, request a real
  checkpoint for scratch tasks, as the runnable branch already does. Project tasks
  keep explicit review/checkpoint semantics.
- Four development-adapter cases passed (10.96 seconds), including injected
  checkpoint failures on both engines: no success message and no candidate promotion.
- Related execution/Core/runtime/early-approval/objective suite: **97 passed in
  96.13 seconds**. The earlier 541-test tool-identity gate predates this correction
  and is not being presented as a rerun of it.
- Native `stdio_worker` example built offline in debug mode; final same-file gate
  with `FAIRY_ACCEPTANCE_LOCAL_WORKER` configured: **6 passed in 13.75 seconds**,
  including two real Rust/Git cases and exact `HEAD:README.md` contents.
- `cargo clippy --locked --offline -p fairy-local-worker --all-targets -- -D warnings`
  passed; `cargo test --locked --offline -p fairy-local-worker --all-targets` passed
  all **30 integration tests**. No Tauri windows, Voice models or real Provider
  requests were started. Worker tests create and close their own temporary Preview
  servers; the plain-file delivery gate itself starts no Preview.
- All tests are newly added; no existing behavioral assertion was relaxed. A first
  draft incorrectly assumed the development provisioner used Git; code inspection
  corrected that test boundary and the explicit Rust gate now supplies the evidence.
