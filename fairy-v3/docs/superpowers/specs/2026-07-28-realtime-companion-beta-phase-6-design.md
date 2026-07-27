# Realtime Companion Beta Phase 6: Core Assistance

## Status

Approved for implementation. This design specializes the user-approved
Realtime Companion Beta final design and the frozen Phase 0–5 contracts.
It does not grant the Realtime Worker, Companion WebView, or pet any tool
authority.

## Outcome

Phase 6 turns a governed Realtime knowledge-gap candidate into a durable Core
request:

```text
MiniCPM / cloud candidate
          |
          v
Tauri Assistance Router -- current session and permission fence
          |
          v
Core RealtimeAssistance aggregate
          |
          v
Conversation Task + Assistant Turn + Harness Scope
          |
          +--> Research / Browser / Knowledge / MCP
          |        through Command Bus and Approval
          v
durable assistant Message in the main conversation
          |
          +--> bounded spoken summary -> current Worker segment
```

The complete answer is the ordinary durable Fairy response in the main chat.
Realtime receives only a short public summary. Any tool approval remains in
the main workspace.

## Considered boundaries

Three placements were considered:

1. **Core-owned durable Assistance with a native Tauri router** — selected.
   Core already owns Conversation, Task Scope, Harness, tools, approvals,
   citations, and durable Messages. Tauri can survive Companion reloads and
   deliver the bounded result to the current Worker identity.
2. Companion React orchestration — rejected because closing or reloading the
   secondary WebView would cancel or duplicate work and expose lifecycle
   authority to presentation code.
3. Worker or Tauri tool execution — rejected because it bypasses Core Scope,
   Command Bus, approval, knowledge snapshots, and the immutable audit trail.

## Public Core methods

Phase 6 adds:

- `realtime.assistance.request`
- `realtime.assistance.get`
- `realtime.assistance.cancel`

`request` accepts:

- `session_id`, `conversation_id`, and caller-stable `request_id`;
- `segment_id` and `context_epoch` for provenance, not ownership;
- a bounded user-visible `question`;
- `activity_profile`, `locale`, and a redacted stable application label;
- at most 16 bounded public `observed_facts`; and
- `allow_network`, which is false unless the user enabled read-only online
  assistance.

The result projects:

- durable status and revision;
- linked Task, Turn, and final Message identifiers when available;
- `spoken_summary` (plain text, at most 320 Unicode characters);
- `display_markdown` (the complete durable assistant answer);
- bounded citation records;
- freshness metadata;
- `requires_user_confirmation`; and
- a stable public failure code.

The same `(session_id, request_id)` is idempotent. Reuse with different input
is rejected. At most one nonterminal Assistance request may own a session.

## Durable state and lifecycle

`RealtimeAssistance` is a local Core aggregate with:

```text
queued -> running -> awaiting_approval -> completed
   |         |              |
   +---------+--------------+-> failed | cancelled
```

The aggregate stores public bounded input, request fingerprint, linked Core
identifiers, public result fields, timestamps, and a revision. It never stores
raw frames, audio, interim captions, hidden provider items, credentials,
prompts, reasoning, or application paths.

`request` validates that:

- the Realtime Session exists and is linked to the supplied Conversation;
- the session is nonterminal;
- the question and facts are bounded and nonblank;
- network access is explicit when the candidate requires public web data; and
- the Conversation has no conflicting nonterminal Task.

If the Conversation is busy, the request remains `queued` with the stable
`ASSISTANCE_CONVERSATION_BUSY` state and no Task side effect. The native router
may retry `request` with the same idempotency key after a bounded delay. It
does not replace `active_task_id`.

Once admitted, Core creates an `ANSWER` Task in the linked Conversation,
creates an Assistant Turn using the current governed model selection, and
starts the existing durable Assistant scheduler. The ordinary Harness binds
the Conversation/Project Knowledge snapshot, Memory snapshot, Persona
authority, tool registry generation, and execution policy. The existing
Assistant lifecycle writes the user message and final Fairy message.

`get` reconciles the aggregate from the linked Turn and Message:

- a running Turn remains `running`;
- a pending tool approval projects `awaiting_approval` and
  `requires_user_confirmation=true`;
- a completed Turn copies its final assistant Message into
  `display_markdown`, derives a bounded first-semantic-paragraph spoken
  summary, and extracts only validated research citation metadata;
- a failed/cancelled Turn projects its stable public result without exposing
  provider or tool internals.

The reconciliation is deterministic and idempotent. A Core restart can resume
the existing durable Turn and reconstruct the same result.

## Tool and permission policy

Realtime Assistance does not define a second tool registry. It uses the
Task-bound Harness and current Core execution policy, with an additional
Realtime restriction:

| Capability | Realtime behavior |
|---|---|
| Project Knowledge and scoped file read | Allowed through the Task Scope |
| Public web and game guide research | Allowed only when `allow_network=true` |
| MCP read-only knowledge | Allowed only when registered and scoped |
| File mutation, command, install, or external write | Main workspace approval required |
| Email, messages, payment, login, account, secret access | Prohibited |
| Keyboard, pointer, game control, or desktop automation | Prohibited |

The Worker submits only a bounded public intent. It cannot name a tool,
approve a command, receive a credential, or execute a callback. The Companion
window can cancel or open the main chat; it cannot approve.

## Tauri router and identity fencing

The `RealtimeAssistanceRouter` receives only Director-approved
`assistance_request` events. It:

1. resolves the current Core-linked Conversation and explicit online
   preference;
2. submits an idempotent Core request on a bounded background worker;
3. polls `get` with a bounded interval while the session remains active;
4. emits a public `assistance_state` projection;
5. sends `HostCommand::AssistanceResult` with only the bounded spoken summary;
   and
6. emits a main-view request that preserves the linked Conversation so the
   user can inspect the complete answer or approval.

Only one job per request ID is active. Duplicate Worker events converge.
Companion reload does not cancel a job. Context rotation carries the unfinished
request ID; successful delivery targets the current segment/epoch for the same
session, never relabels an old Worker event, and never replays raw context.
Session stop cancels unfinished Core Assistance. Core unavailability pauses
tool features and projects `ASSISTANCE_CORE_UNAVAILABLE`; basic local dialogue
may continue briefly.

## UI

The Companion panel shows one bounded Assistance row:

- Searching, waiting for the current chat, approval required, completed,
  failed, or cancelled;
- a safe public summary only after completion;
- `Open main chat` for the full answer or approval; and
- `Cancel` while nonterminal.

The UI does not render raw citations, tool payloads, hidden traces, or the full
answer. Realtime failures do not end the Presence Session or switch Backend.

## Failure and privacy behavior

- Disabled online assistance fails public-web candidates closed without
  starting a Task.
- Missing or mismatched Session/Conversation scope rejects before persistence.
- Core restart retains the durable request and linked Turn.
- Worker restart or epoch rotation cannot duplicate the Task.
- A failed Core request produces a short failure projection and leaves
  Realtime audio/video lifecycle unchanged.
- Tool approval, rejection, cancellation, and timeout remain ordinary Core
  Ledger transitions.
- No network request originates in the Worker or Companion.
- Ordinary app startup keeps Realtime, Voice, Omni, and assistance execution
  cold.

## Acceptance

Phase 6 is complete when:

- the three Core methods validate and persist idempotent Assistance requests;
- one admitted request creates exactly one scoped Task and Assistant Turn;
- project Knowledge, read-only research/game-guide tools, and MCP reads remain
  under the existing Harness and Command Bus;
- online access fails closed unless explicitly enabled;
- prohibited external actions and input control are unavailable;
- approval-required work appears only in the main workspace and can resume the
  same Assistance request;
- the complete answer is a normal durable main-chat Message;
- Realtime receives only a bounded spoken summary;
- restart, duplicate event, Companion reload, epoch rotation, cancellation,
  and Core-unavailable paths are fenced and recoverable;
- focused Core/Rust/Vitest/Playwright and full regression suites pass; and
- a controlled Tauri dev check proves the secondary window has no approval or
  tool authority and all on-demand processes are cleaned up.

Paid provider traffic, real web freshness, real game-guide quality, real MCP
servers, and production-model answer quality require configured credentials
and remain truthful environment-dependent release evidence, not synthetic
claims.
