# Durable Media Workflow Acceptance

## Observable behavior

- Media work is compiled into submit, wait, poll, and archive Workflow nodes.
- Delayed waits and provider polling do not occupy a Workflow worker slot.
- A provider identity is persisted before polling; a submitted video is never submitted twice.
- Replaying the same idempotency key returns the same Job, Run, provider job, and Artifact.
- Cancellation is terminal locally and does not claim to reverse upstream billing.

## State ownership

| State | Owner | Scope key | Rule |
|---|---|---|---|
| Media Job | Core state store | `tenant_id + job_id` | provider identity never rebinds |
| Durable schedule | Workflow Kernel | `tenant_id + workflow_run_id` | one four-stage plan |
| Attempt | Workflow Kernel | `node + attempt + fence` | late owners cannot settle |
| Provider effect | Command Bus | `command_run_id + idempotency_key` | policy remains authoritative |
| File and Artifact | Workspace/Core | Version + digest | imported exactly once |

## Invariants and boundaries

- Assistant may link a Media Run using `parent_run_id`; this is not a general multi-Agent UI.
- Media Adapter validates payloads, classifies retry, and emits public summaries and evidence.
- Realtime voice sessions and Preview Runtime Pool are not Media Workflow nodes.
- Scripted and HTTP-mock providers prove scheduling and call counts, not paid-provider availability.
- Real paid generation requires an explicit cost-capped smoke run.
