import { AlertTriangle, Check, Clock3, X } from "lucide-react";

import type { Approval, EventEnvelope, Task } from "../core/client";

interface TaskTimelineProps {
  task: Task | null;
  tasks: Task[];
  events: EventEnvelope[];
  approvals: Approval[];
  isActing: boolean;
  onSelectTask(taskId: string): void;
  onDecision(approvalId: string, approved: boolean): Promise<void>;
}

export function TaskTimeline({
  task,
  tasks,
  events,
  approvals,
  isActing,
  onSelectTask,
  onDecision,
}: TaskTimelineProps) {
  const pendingApproval = approvals.find((approval) => approval.decision === "pending") ?? null;
  const isActive = task !== null && activeTaskStatuses.has(task.status);

  return (
    <section className="timeline-pane" aria-labelledby="timeline-heading">
      <div className="pane-header">
        <div>
          <span className="eyebrow">{task === null ? "NO ACTIVE TASK" : task.status}</span>
          <h1 id="timeline-heading">Task Timeline</h1>
        </div>
        {tasks.length > 0 ? (
          <label className="compact-select">
            <span className="sr-only">Select task</span>
            <select
              aria-label="Select task"
              value={task?.id ?? ""}
              onChange={(event) => onSelectTask(event.target.value)}
            >
              {tasks.map((item) => (
                <option key={item.id} value={item.id}>
                  {compactRequest(item.user_request)}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        {isActive ? (
          <div className="task-signal" aria-label="Task is active">
            <span /> LIVE
          </div>
        ) : null}
      </div>

      {task === null ? (
        <div className="pane-empty" role="status">
          <Clock3 size={22} />
          <strong>No task selected</strong>
        </div>
      ) : (
        <>
          <div className="task-request">
            <span className="message-author">REQUEST</span>
            <p>{task.user_request}</p>
          </div>

          <ol className="event-timeline" aria-label="Execution events">
            {events.length === 0 ? (
              <li className="event-empty">
                <span className="event-node" />
                <time>--:--:--</time>
                <div>
                  <strong>Waiting for durable events</strong>
                  <p>{humanStatus(task.status)}</p>
                </div>
              </li>
            ) : (
              events.map((event) => (
                <li key={event.id} className={isPresenceEvent(event) ? "event-active" : ""}>
                  <span className="event-node" />
                  <time dateTime={event.created_at}>{eventTime(event.created_at)}</time>
                  <div>
                    <strong>{event.message || humanEventType(event.event_type)}</strong>
                    <p>{humanEventType(event.event_type)}</p>
                  </div>
                </li>
              ))
            )}
          </ol>

          {pendingApproval !== null ? (
            <div className="approval-block" role="group" aria-label="Pending approval">
              <div>
                <span className="eyebrow">
                  <AlertTriangle size={12} /> APPROVAL REQUIRED
                </span>
                <strong>{pendingApproval.reason}</strong>
                <p>{pendingApproval.requested_by}</p>
              </div>
              <div className="approval-actions">
                <button
                  className="secondary-command"
                  type="button"
                  disabled={isActing}
                  onClick={() => settle(onDecision(pendingApproval.id, false))}
                >
                  <X size={14} /> Reject
                </button>
                <button
                  className="primary-command"
                  type="button"
                  disabled={isActing}
                  onClick={() => settle(onDecision(pendingApproval.id, true))}
                >
                  <Check size={14} /> Approve
                </button>
              </div>
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}

const activeTaskStatuses = new Set([
  "resolving_scope",
  "building_workspace",
  "planning",
  "awaiting_approval",
  "executing",
  "installing",
  "previewing",
  "reviewing",
  "repairing",
]);

function isPresenceEvent(event: EventEnvelope): boolean {
  return ["running", "starting", "previewing", "applying"].some((part) =>
    event.event_type.includes(part),
  );
}

function humanEventType(value: string): string {
  return value.replaceAll(".", " / ").replaceAll("_", " ");
}

function humanStatus(value: string): string {
  return value.replaceAll("_", " ");
}

function compactRequest(value: string): string {
  return value.length > 42 ? `${value.slice(0, 39)}...` : value;
}

function eventTime(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf())
    ? "--:--:--"
    : parsed.toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      });
}

function settle(operation: Promise<void>): void {
  void operation.catch(() => undefined);
}
