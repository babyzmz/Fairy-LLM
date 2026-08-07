import {
  AlertCircle,
  CalendarClock,
  CheckCircle2,
  CirclePause,
  LoaderCircle,
  Pause,
  Play,
  RotateCw,
  X,
} from "lucide-react";
import type { ReactNode, RefObject } from "react";

import type {
  AssistantBackgroundTask,
  AssistantBackgroundTaskPage,
} from "../core/client";
import type { BackgroundTaskAction } from "../app/workspaceTypes";

interface BackgroundTasksPopoverProps {
  page: AssistantBackgroundTaskPage;
  loading: boolean;
  busy: boolean;
  panelRef: RefObject<HTMLDivElement | null>;
  onOpen(task: AssistantBackgroundTask): void;
  onAction(task: AssistantBackgroundTask, action: BackgroundTaskAction): Promise<void>;
  onClose(): void;
}

export function BackgroundTasksPopover({
  page,
  loading,
  busy,
  panelRef,
  onOpen,
  onAction,
  onClose,
}: BackgroundTasksPopoverProps) {
  return (
    <div
      ref={panelRef}
      id="chat-background-tasks"
      className="background-tasks-popover"
      role="dialog"
      aria-modal="false"
      aria-labelledby="background-tasks-title"
    >
      <header>
        <div>
          <span className="eyebrow">Local workflow</span>
          <h2 id="background-tasks-title">Background tasks</h2>
        </div>
        <button
          className="icon-button"
          type="button"
          aria-label="Close background tasks"
          data-autofocus="true"
          onClick={onClose}
        >
          <X size={15} />
        </button>
      </header>
      <div className="background-tasks-scroll">
        {loading && page.nonterminal_count === 0 ? (
          <div className="background-tasks-empty" role="status">
            <LoaderCircle size={17} className="is-spinning" /> Loading tasks
          </div>
        ) : (
          <>
            <TaskGroup
              title="This chat"
              items={page.current}
              busy={busy}
              onOpen={onOpen}
              onAction={onAction}
            />
            <TaskGroup
              title="Other chats"
              items={page.other}
              busy={busy}
              onOpen={onOpen}
              onAction={onAction}
            />
            <TaskGroup
              title="Recent · 24 hours"
              items={page.recent}
              busy={busy}
              onOpen={onOpen}
              onAction={onAction}
              terminal
            />
          </>
        )}
      </div>
    </div>
  );
}

function TaskGroup({
  title,
  items,
  busy,
  terminal = false,
  onOpen,
  onAction,
}: {
  title: string;
  items: AssistantBackgroundTask[];
  busy: boolean;
  terminal?: boolean;
  onOpen(task: AssistantBackgroundTask): void;
  onAction(task: AssistantBackgroundTask, action: BackgroundTaskAction): Promise<void>;
}) {
  return (
    <section className="background-task-group" aria-label={title}>
      <header>
        <strong>{title}</strong>
        <span>{items.length}</span>
      </header>
      {items.length === 0 ? (
        <p>{terminal ? "No recent tasks" : "No active tasks"}</p>
      ) : (
        <div className="background-task-list">
          {items.map((item) => (
            <TaskRow
              key={item.id}
              item={item}
              busy={busy}
              onOpen={onOpen}
              onAction={onAction}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function TaskRow({
  item,
  busy,
  onOpen,
  onAction,
}: {
  item: AssistantBackgroundTask;
  busy: boolean;
  onOpen(task: AssistantBackgroundTask): void;
  onAction(task: AssistantBackgroundTask, action: BackgroundTaskAction): Promise<void>;
}) {
  const status = statusPresentation(item.status);
  return (
    <article className="background-task-row" data-status={item.status}>
      <button className="background-task-open" type="button" onClick={() => onOpen(item)}>
        <span className="background-task-status" data-tone={status.tone} aria-hidden="true">
          {status.icon}
        </span>
        <span className="background-task-copy">
          <strong>{item.title}</strong>
          <span>
            {item.conversation_title} · {status.label}
            {item.workflow_budget_tier === "deep" ? " · Deep" : ""}
          </span>
          {item.public_error ? <small>{item.public_error}</small> : null}
        </span>
        <time dateTime={item.updated_at}>{relativeTime(item.updated_at)}</time>
      </button>
      {item.can_pause || item.can_resume || item.can_cancel || item.can_run_now ? (
        <div className="background-task-actions" aria-label={`Manage ${item.title}`}>
          {item.can_pause ? (
            <ActionButton
              label="Pause"
              disabled={busy}
              icon={<Pause size={13} />}
              action={() => onAction(item, "pause")}
            />
          ) : null}
          {item.can_resume ? (
            <ActionButton
              label="Resume"
              disabled={busy}
              icon={<Play size={13} />}
              action={() => onAction(item, "resume")}
            />
          ) : null}
          {item.can_run_now ? (
            <ActionButton
              label="Run now"
              disabled={busy}
              icon={<RotateCw size={13} />}
              action={() => onAction(item, "run_now")}
            />
          ) : null}
          {item.can_cancel ? (
            <ActionButton
              label="Cancel"
              disabled={busy}
              icon={<X size={13} />}
              action={() => onAction(item, "cancel")}
            />
          ) : null}
        </div>
      ) : null}
    </article>
  );
}

function ActionButton({
  label,
  disabled,
  icon,
  action,
}: {
  label: string;
  disabled: boolean;
  icon: ReactNode;
  action(): Promise<void>;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={() => void action().catch(() => undefined)}
    >
      {icon}
    </button>
  );
}

function statusPresentation(status: string): {
  label: string;
  tone: string;
  icon: ReactNode;
} {
  if (status === "completed") {
    return { label: "Completed", tone: "success", icon: <CheckCircle2 size={14} /> };
  }
  if (["failed", "attention_required", "waiting_for_approval"].includes(status)) {
    return {
      label: status === "waiting_for_approval" ? "Approval required" : "Needs attention",
      tone: "attention",
      icon: <AlertCircle size={14} />,
    };
  }
  if (status === "paused") {
    return { label: "Paused", tone: "paused", icon: <CirclePause size={14} /> };
  }
  if (status === "scheduled") {
    return { label: "Scheduled", tone: "scheduled", icon: <CalendarClock size={14} /> };
  }
  if (status === "cancelled") {
    return { label: "Cancelled", tone: "muted", icon: <X size={14} /> };
  }
  return {
    label: status === "queued" ? "Queued" : "Running",
    tone: "running",
    icon: <LoaderCircle size={14} className="is-spinning" />,
  };
}

function relativeTime(value: string): string {
  const delta = Date.now() - Date.parse(value);
  if (!Number.isFinite(delta) || delta < 60_000) return "now";
  const minutes = Math.floor(delta / 60_000);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  return hours < 24 ? `${hours}h` : `${Math.floor(hours / 24)}d`;
}
