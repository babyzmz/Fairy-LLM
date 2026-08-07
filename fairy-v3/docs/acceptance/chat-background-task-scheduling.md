# Chat Background Task Scheduling Acceptance

## Observable behavior

- Collapsing the chat Inspector preserves its state and exposes the background-task and restore
  controls without changing message scroll position.
- The background-task popover overlays chat, prioritizes the current conversation, is keyboard
  accessible, and closes when its owning UI context changes.
- A future or recurring instruction appears immediately as a Schedule card but is absent from
  model context until an occurrence creates the real user Message.
- Closing to tray keeps due work active. Explicit exit and restart recover without duplicate
  Messages, Turns, Workflow Runs, or external effects.
- A busy conversation and an overlapping recurrence coalesce to one latest pending occurrence.
- Invalid model, permission, Workspace, or Version bindings pause for attention rather than
  silently changing execution scope.

## State and scope invariants

- A Schedule and every Occurrence remain within one tenant and owning conversation scope.
- `(tenant_id, schedule_id, scheduled_for)` is unique and dispatch settlement is fenced.
- Schedule pause/cancel never changes an already dispatched Turn.
- Schedule cards do not appear in Message context, Line Sidebar entries, or Realtime transcript.
- Only the existing Assistant application creates Messages, Turns, and Workflow Runs.
- The trigger service holds no Workflow worker while waiting for time, conversation capacity, or
  approval.

## Required scenarios

1. Create, edit, pause, resume, run-now, and cancel one-shot and recurring schedules.
2. Restart before and after occurrence creation and prove exactly one dispatched Turn.
3. Cross spring-forward and fall-back boundaries in multiple IANA timezones.
4. Miss several recurring instants and recover only the latest with an accurate coalesced count.
5. Keep an occurrence running through the next instant and serialize the owning conversation.
6. Fail three consecutive occurrences, pause automatically, repair binding, and resume.
7. Collapse and restore Inspector without losing Preview tab, width, runtime, chat scroll, Composer
   draft, Line Sidebar position, or active Turn.
8. Navigate from current/other/recent task rows and from a Windows notification.
9. Prove that transient attachments, screenshots, stale scope, and notification secret content are
   rejected.

## Verification boundaries

- Scripted clocks and providers prove deterministic scheduling but not OS clock-service behavior or
  real model quality.
- Playwright proves renderer interaction but not Windows notification activation or WebView2 focus.
- SQLite migration and Alembic offline checks do not prove live PostgreSQL locking or RLS.
- Native Tauri notification/focus and a real local provider remain explicit manual gates.
- Docker, Tauri release, and production image builds are outside this development change.
