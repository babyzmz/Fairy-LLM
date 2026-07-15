import { AlertTriangle, Check, Clock3, X } from "lucide-react";

import { ActivityRail } from "../chat/ActivityRail";
import type { TurnTraceQueryState } from "../chat/useTurnTraces";
import type { Approval, AssistantTurn, EventEnvelope, Task, TurnTrace } from "../core/client";

interface TaskTimelineProps {
  task: Task | null;
  tasks: Task[];
  events: EventEnvelope[];
  approvals: Approval[];
  turn: AssistantTurn | null;
  trace: TurnTrace | null;
  traceState: TurnTraceQueryState | null;
  developerMode: boolean;
  isActing: boolean;
  onSelectTask(taskId: string): void;
  onDecision(approvalId: string, approved: boolean): Promise<void>;
}

export function TaskTimeline({
  task,
  tasks,
  events,
  approvals,
  turn,
  trace,
  traceState,
  developerMode,
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

          <div className="task-work-chain">
            {turn !== null || trace !== null || traceState !== null || events.length > 0 ? (
              <ActivityRail
                turn={turn?.task_id === task.id ? turn : null}
                trace={trace}
                traceState={traceState}
                events={events}
                developerMode={developerMode}
              />
            ) : (
              <div className="task-trace-empty" role="status">
                <Clock3 size={16} />
                <div>
                  <strong>Waiting for durable activity</strong>
                  <span>{humanStatus(task.status)}</span>
                </div>
              </div>
            )}
          </div>

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

function humanStatus(value: string): string {
  return value.replaceAll("_", " ");
}

function compactRequest(value: string): string {
  return value.length > 42 ? `${value.slice(0, 39)}...` : value;
}

function settle(operation: Promise<void>): void {
  void operation.catch(() => undefined);
}
