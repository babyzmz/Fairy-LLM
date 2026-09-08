# Assistant command heartbeat ownership

Inspection found that Turn heartbeat enumerates trace/invocation command IDs and
renews using the owner and Fence read from the database. History membership is not
proof that this Assistant process owns the command. After takeover or a deferred
domain handoff this can extend a lease belonging to another worker.

Bind Assistant command claims to an unforgeable-by-client, per-application worker
identity. Heartbeat receives that trusted identity from its adapter, validates
Task/Conversation/Scope and renews only matching owned claims using the existing
atomic Fence check. A new process must not adopt an old live lease; ordinary
expired-lease recovery still requires an explicit fresh claim. A domain-owned
deferred media command stays under its own scheduler heartbeat.

Do not introduce a second scheduler, expose an owner override in RPC or treat
heartbeat rejection as proof of physical cancellation. Test two conversations,
both execution engine versions, owned renewal, foreign takeover and domain
handoff. Preserve existing lost-heartbeat restart and media recovery gates.

## Evidence

- RED: both engines renewed a command after another worker acquired it. Owned
  command renewal and the other conversation's unchanged lease already passed.
- Assistant Command Bus instances now share one private per-application worker ID;
  other Command Bus consumers retain their previous default unique-claim behavior.
  The ID is never accepted from a frontend RPC. Scoped heartbeat rejects foreign
  live ownership and leaves deferred media ownership to its domain scheduler.
- Reopen tests exposed another RED path: the model checkpoint helper returned a
  foreign live Command to continuation code. It now requires the model command
  type and this application's ownership, or an explicit expired-lease reclaim.
- Eight engine/ownership/reopen combinations and one real media-domain handoff
  test pass. The parent heartbeat issues no renewal for the child-owned command;
  the original Media Job still completes exactly once.
- Combined ownership, model/worker recovery, media, and Command Bus gate:
  **48 passed (70.26s)**. Changed Ruff and whitespace checks pass. Tests use SQLite
  and scripted providers, not PostgreSQL contention or physical Provider stop.
