# Durable Media Worker Acceptance

## Observable behavior

- Image and music requests return one completed Workspace Artifact without executing the
  provider on the request thread.
- Video start returns after the provider accepts the job; Fairy continues polling and
  downloads the result without any `media.videos.get` request.
- `media.videos.get` is a read-only projection and never contacts the provider.
- Pending image, music, and video work resumes after a Core restart from durable state.
- Replaying the same idempotency key never creates a second provider job or Artifact.
- Cancellation stops Fairy polling and downloading. It does not claim to cancel upstream
  billing because OpenRouter currently exposes no video cancellation endpoint.

## State ownership

| State | Owner | Scope key | Restart rule |
|---|---|---|---|
| Media Job | Core state store | `tenant_id + job_id` | reload unchanged |
| Work schedule | Media work queue | `tenant_id + job_id` | reclaim after lease expiry |
| Worker claim | Media work queue | `lease_owner + lease_fence` | stale owners cannot renew or settle |
| Provider identity | Media Job | `provider_job_id` | never rebind after video submission |
| Generated file | Workspace Version | `workspace_id + version_id + output_path` | import once by expected content hash |
| Artifact | Core state store | `tenant_id + artifact_id` | append once for the Media Job |

## Invariants

- One non-terminal Media Job has at most one active worker claim.
- A claim is tenant-scoped, time-bounded, and fenced by owner plus lease fence.
- Image and music retries reuse the durable provider idempotency key.
- A video with a provider identity is polled, never submitted again.
- A terminal Job has no future `available_at`, active lease, or provider call.
- Worker interruption leaves work reclaimable and does not turn it into user cancellation.
- User cancellation is terminal and late provider completion cannot import a file.
- Job, CommandRun, Task, Workspace, Version, and Artifact scope identities cannot change.
- Provider prompts, credentials, and raw responses never enter user-visible Ledger events.

## Acceptance scenarios

| Scenario | Required result | Forbidden result | Evidence |
|---|---|---|---|
| Image generation | one Artifact and one provider call | request-thread provider execution | scheduler integration test |
| Music generation | approval then one Artifact | Voice session or second message | service integration test |
| Video without GET | background pending to completed | UI-driven provider polling | scheduler integration test |
| GET projection | current durable Job only | provider call or CommandRun | service regression test |
| Process restart | expired claim is reclaimed | permanent spinner/interrupted terminal state | close-and-reopen SQLite test |
| Lease loss | local token interrupted, work retried | stale worker settlement | scheduler test |
| Cancel/poll race | cancellation remains terminal | late Artifact import | concurrency regression test |
| Duplicate request | original Job returned | duplicate charge or Artifact | idempotency test |
| Two tenants | only owning tenant claims | cross-tenant execution | PostgreSQL RLS test |

## Failure and timeout behavior

- Provider authentication, content, protocol, timeout, rate-limit, and network failures are
  persisted as safe error codes and terminate bounded image/music attempts.
- Active video polling uses bounded backoff and becomes terminal on upstream failed,
  cancelled, or expired status.
- A lost heartbeat interrupts only the local attempt. The queue claim becomes reclaimable
  and the Job remains non-terminal.
- Graceful Core close interrupts active media calls, abandons their leases, and waits for
  worker threads without deleting pending work.
- Staging files are cleaned on every completed, failed, cancelled, or interrupted attempt.

## Test boundaries

- Recording providers prove call counts, idempotency, polling ownership, cancellation, and
  restart behavior; they do not prove OpenRouter availability or billing semantics.
- SQLite tests use the real database and close/reopen separate Core service instances.
- OpenRouter adapter tests use HTTP mock transport and validate official endpoint shapes.
- PostgreSQL claim/RLS tests require Docker and must be reported as unverified when the
  Docker daemon is unavailable.
- Real paid media generation is excluded from automatic tests and requires an explicit
  cost-capped smoke run.
