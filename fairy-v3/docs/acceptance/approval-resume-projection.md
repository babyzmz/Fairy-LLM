# Approval Resume Projection Acceptance

## Observable behavior

- Approving or rejecting a pending Assistant tool resumes the exact owning Turn in Core.
- The Composer becomes busy again as soon as the approval response confirms that Core owns the resume.
- The existing user message, Activity Rail, and Assistant response remain bound to the same Turn.
- A terminal Turn clears the busy state even when its event races with the approval response.
- An approval for another Turn or Conversation never changes the active Composer.

## Ownership and invariants

| State | Owner | Scope key | Rule |
|---|---|---|---|
| Approval decision | Core approval ledger | `approval_id + command_run_id` | idempotent for the same decision |
| Resume execution | Core Assistant scheduler | `assistant_turn_id` | Core schedules it; desktop never calls `turns.start` again |
| Resume projection | desktop Assistant hook | `conversation_id + assistant_turn_id` | local busy state only; no execution authority |

- `approvals.decide` returns the exact `assistant_turn_id` when the approval belongs to an Assistant Turn.
- `resume_requested` means Core owns continuation for that Turn; it does not authorize the client to start it.
- Duplicate decision responses are stable and do not duplicate a tool effect, model round, or Assistant message.
- A late response cannot make a completed, failed, or cancelled Turn busy again.

## Automated acceptance

| Scenario | Required result | Forbidden result | Evidence |
|---|---|---|---|
| Approve waiting tool | response contains exact Turn ID and `resume_requested=true` | anonymous resume or second Turn | Core service test |
| Project approval | response has no Assistant Turn binding | unrelated Composer becomes busy | Core contract test |
| Desktop applies response | matching waiting Turn becomes busy | second `assistant.turns.start` | React hook test |
| Terminal event races response | terminal state remains settled | permanent spinner or late busy regression | React hook test |
| Wrong Turn ID | active Composer is unchanged | cross-Conversation state leak | React hook test |
| Core restart after approval | same Turn resumes or becomes explicitly terminal | duplicate tool effect | SQLite restart test |

## Environment boundaries

- Core tests use a real temporary SQLite ledger and scripted providers/tools. They prove scheduling, persistence, and exact tool-effect counts without depending on OpenRouter.
- React tests mock the typed Core boundary. They prove projection and call counts, not Core execution.
- No native Tauri behavior is changed by this task; a browser test is sufficient for the Composer state transition.

## Manual check

1. Start a Turn that requests a governed tool and wait for the approval card.
2. Choose Approve once.
3. Confirm the approval card settles, the Composer changes to Stop immediately, and the Activity Rail advances.
4. Confirm one tool effect and one final Fairy response are produced.
5. Repeat with Reject and confirm a bounded final response without a tool effect.
