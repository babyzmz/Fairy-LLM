# Fairy V3 Chat Background Task Scheduling Design

**Status:** Accepted implementation specification

**Date:** 2026-08-07

## Purpose

Fairy already executes finite Assistant work through the durable Workflow Kernel, but users can
only observe a Run in its owning Turn. This design adds local, durable one-shot and recurring
Assistant schedules without creating a global task center or a second workflow scheduler.

The product surface remains chat-native. The background-task entry exists only while the
Workspace Inspector is collapsed in chat mode. It opens a non-modal popover over the message
area; it never appears in History, project Timeline, Settings, the tray, or the Pet menu.

## Product surface

The Workspace Inspector gains a collapse control. Collapse keeps Preview, Files, and Obsidian
mounted and inert, preserves the active tab, width, scroll positions, and runtimes, and lets the
chat column occupy the freed space. While collapsed, the chat toolbar exposes a background-task
button and an Inspector restore button. Restoring the Inspector, changing conversation or main
view, pressing Escape, or clicking outside closes the popover.

The popover is anchored at the chat toolbar, overlays rather than reflows messages, is 400 pixels
wide on desktop, and is bounded to the viewport minus 24 pixels on constrained windows. It shows
non-terminal work for the current conversation first, non-terminal work from other conversations
second, and at most 20 terminal items from the previous 24 hours last. Selecting an item opens its
conversation and locates the Schedule card, Turn, or Activity Rail.

Composer supplies the creation surface. Normal send and `Run now` create the existing durable
Assistant Turn. `Later` and `Repeat` accept text only and reject transient files or screen captures.
Repeat presets are daily, weekdays, weekly, or an integer hour/day interval; V1 exposes no Cron.
A newly created schedule immediately appears as a Schedule card, but it is not an Assistant
Message and never enters model context. The occurrence writes the real user Message only when it
is dispatched.

## Durable schedule model

`AssistantSchedule` owns the conversation, instruction template, trigger rule, IANA timezone,
next fire time, execution target, model-selection and permission snapshots, Task/Workspace/Version
scope, active revision, failure count, state, and a conversation timeline sequence. Its states are
`active`, `paused`, `completed`, and `cancelled`.

`AssistantScheduleOccurrence` is immutable for a schedule revision and scheduled instant. It
records dispatch/result identity, the resulting Turn and Workflow Run, coalesced count, and a
bounded public error. `(tenant_id, schedule_id, scheduled_for)` is unique. Editing a schedule
changes future occurrences only. Schedule cards use the schedule timeline sequence while Messages
retain their existing sequence and authority.

The public Assistant surface adds schedule CRUD-without-delete, `run_now`, and a bounded
background-task projection. Running work continues to use the existing Turn pause, resume,
cancel, retry, and Workflow inspection methods. The generic Workflow tables remain internal.

## Triggering and recovery

The local Schedule Trigger Service owns time-based dispatch. It claims due schedules with a lease
and fence, creates an occurrence idempotently, and asks the existing Assistant application to
create and start a Turn. Waiting consumes no Workflow worker. Closing to tray leaves Core and the
trigger service running; explicit exit stops dispatch. Startup performs one catch-up pass.

One-shot schedules execute once after an offline interval. Recurring schedules coalesce all missed
or overlapping instants into the latest single pending occurrence and record the count. A schedule
with an active occurrence or a conversation with an active Turn never starts a concurrent Turn.
Waiting approval counts as active work. Three consecutive terminal failures pause a recurring
schedule. A successful occurrence resets the counter.

Model, credential, permission, Workspace, Version, or execution-target drift pauses the schedule
for attention instead of selecting a substitute. Pausing or cancelling a schedule affects only
future occurrences; an already dispatched Turn must be controlled independently. `run_now` is
disabled while an occurrence is active and does not move the regular next-fire instant.

Recurring wall-clock rules use the stored IANA timezone. A nonexistent spring-forward time moves
to the first valid local instant. An ambiguous fall-back time uses its first occurrence only.
Wake, timezone, and clock changes cause recomputation, while the occurrence uniqueness key and
claim fence prevent duplicate dispatch.

## Notifications and navigation

Core emits scoped schedule/occurrence Ledger events. Tauri converts only completion, failure,
approval, and attention-required events into Windows notifications. If the main window is focused
on the owning conversation, the native notification is suppressed and only in-app state updates.

Notification payloads contain a public title/summary, conversation ID, and optional Turn ID. They
must not include instructions, model/tool content, file contents, paths, or credentials. Activation
reuses the sequenced main-view request to show the main window, open the conversation, and request
Turn location. A deleted source produces a visible unavailable-source notice and never recreates
scope.

## Explicit exclusions

- No global task center, project Timeline entry, tray entry, Settings entry, or Pet entry.
- No Cloud trigger service, automatic local-to-Cloud migration, or general child-Agent UI.
- No Cron, webhook, temporary attachment, or screen-capture persistence.
- No Realtime or Preview Pool migration into Workflow.
- No Docker, release build, or production image during implementation.

