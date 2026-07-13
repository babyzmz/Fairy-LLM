import {
  AlertCircle,
  ClipboardCheck,
  Code2,
  ExternalLink,
  LoaderCircle,
  PanelRightOpen,
  Play,
  RotateCcw,
  Square,
} from "lucide-react";
import { type ReactNode, useState } from "react";

import type { PreviewContext, RuntimeHealth, Task } from "../core/client";

interface PreviewPanelProps {
  task: Task | null;
  context: PreviewContext | null;
  runtimeHealth: RuntimeHealth | null;
  isActing: boolean;
  developerMode?: boolean;
  onStart(): Promise<void>;
  onStop(): Promise<void>;
  onReview(): Promise<void>;
  onAccept(): Promise<void>;
  onDiscard(): Promise<void>;
}

export function PreviewPanel({
  task,
  context,
  runtimeHealth,
  isActing,
  developerMode = false,
  onStart,
  onStop,
  onReview,
  onAccept,
  onDiscard,
}: PreviewPanelProps) {
  const [developerOpen, setDeveloperOpen] = useState(false);
  const preview = context?.preview ?? null;
  const runtime = context?.runtime ?? runtimeHealth?.runtime ?? null;
  const safeUrl =
    preview?.url !== null && preview?.url !== undefined
      ? isSafePreviewUrl(preview.url, preview.execution_target)
        ? preview.url
        : null
      : null;
  const canStop =
    preview !== null && ["ready", "stopping", "interrupted"].includes(preview.status);
  const canDecide = task?.status === "ready";
  const canDiscard = task !== null && ["ready", "failed"].includes(task.status);
  const terminalDecision = task !== null && ["accepted", "rejected", "archived"].includes(task.status);
  const canReview =
    task !== null && task !== undefined && ["executing", "previewing"].includes(task.status);

  return (
    <section className="preview-pane" aria-labelledby="preview-heading">
      <div className="preview-toolbar">
        <div>
          <span className="eyebrow">{preview?.visibility ?? "TASK OUTPUT"}</span>
          <h2 id="preview-heading">Preview</h2>
        </div>
        <div className="preview-actions">
          {canStop ? (
            <button
              className="icon-button"
              type="button"
              aria-label="Stop preview"
              title="Stop preview"
              disabled={isActing || preview?.status === "stopping"}
              onClick={() => settle(onStop())}
            >
              <Square size={15} />
            </button>
          ) : null}
          {developerMode ? <button
            className={`icon-button ${developerOpen ? "active" : ""}`}
            type="button"
            aria-label="Toggle developer details"
            title="Developer details"
            aria-pressed={developerOpen}
            onClick={() => setDeveloperOpen((open) => !open)}
          >
            <PanelRightOpen size={16} />
          </button> : null}
        </div>
      </div>

      <div className="preview-frame">
        {renderPreviewState({
          task,
          context,
          runtimeHealth,
          safeUrl,
          isActing,
          onStart,
        })}
        {safeUrl !== null && preview?.status === "ready" ? (
          <>
            <a
              className="preview-external"
              href={safeUrl}
              target="_blank"
              rel="noreferrer"
              aria-label="Open preview in browser"
              title="Open preview in browser"
            >
              <ExternalLink size={14} />
            </a>
            <div className="scan-line" aria-hidden="true" />
          </>
        ) : null}
      </div>

      <div className="version-decision-bar">
        <span>{decisionLabel(task, preview?.status ?? null)}</span>
        {!terminalDecision ? (
          <div>
          {!canDecide ? (
            <button
              className="primary-command"
              type="button"
              disabled={!canReview || isActing}
              onClick={() => settle(onReview())}
            >
              <ClipboardCheck size={15} />
              Review
            </button>
          ) : null}
          <button
            className="secondary-command"
            type="button"
            disabled={!canDiscard || isActing}
            onClick={() => settle(onDiscard())}
          >
            Discard
          </button>
          <button
            className="primary-command"
            type="button"
            disabled={!canDecide || isActing}
            onClick={() => settle(onAccept())}
          >
            Use this version
          </button>
          </div>
        ) : null}
      </div>

      {developerMode && developerOpen ? (
        <aside className="developer-drawer" aria-label="Developer details">
          <span className="eyebrow">
            <Code2 size={12} /> DEVELOPER MODE
          </span>
          <dl>
            <div>
              <dt>Executor</dt>
              <dd>{runtime?.executor ?? runtimeHealth?.executor.executor ?? "Unavailable"}</dd>
            </div>
            <div>
              <dt>Runtime</dt>
              <dd>{runtime?.status ?? "not started"}</dd>
            </div>
            <div>
              <dt>Preview</dt>
              <dd>{preview?.status ?? "not started"}</dd>
            </div>
            <div>
              <dt>Version</dt>
              <dd>{preview?.version_id ?? task?.target_version_id ?? "none"}</dd>
            </div>
            <div>
              <dt>Root</dt>
              <dd>{runtime?.project_root ?? "none"}</dd>
            </div>
            {preview?.error_code ? (
              <div>
                <dt>Error</dt>
                <dd>{preview.error_code}</dd>
              </div>
            ) : null}
          </dl>
        </aside>
      ) : null}
    </section>
  );
}

interface PreviewStateInput {
  task: Task | null;
  context: PreviewContext | null;
  runtimeHealth: RuntimeHealth | null;
  safeUrl: string | null;
  isActing: boolean;
  onStart(): Promise<void>;
}

function renderPreviewState({
  task,
  context,
  runtimeHealth,
  safeUrl,
  isActing,
  onStart,
}: PreviewStateInput) {
  const preview = context?.preview ?? null;
  if (task === null) {
    return <PreviewNotice icon={<Code2 />} title="No task selected" detail="Preview unavailable" />;
  }
  if (preview?.status === "starting") {
    return (
      <PreviewNotice
        icon={<LoaderCircle className="spin" />}
        title="Preparing preview"
        detail="Runtime intent recorded"
      />
    );
  }
  if (preview?.status === "ready" && safeUrl !== null) {
    return (
      <iframe
        className="preview-iframe"
        src={safeUrl}
        title="Task preview"
        sandbox="allow-forms allow-scripts"
        referrerPolicy="no-referrer"
      />
    );
  }
  if (preview?.status === "ready" && safeUrl === null) {
    return (
      <PreviewNotice
        icon={<AlertCircle />}
        title="Preview URL blocked"
        detail="SCOPE_MISMATCH"
        tone="danger"
      />
    );
  }
  if (preview?.status === "interrupted" || preview?.status === "failed") {
    return (
      <div className="preview-notice preview-notice-danger" role="status">
        <AlertCircle />
        <strong>{preview.status === "interrupted" ? "Preview interrupted" : "Preview failed"}</strong>
        <span>{preview.error_code ?? "WORKER_INTERRUPTED"}</span>
        <button
          className="primary-command"
          type="button"
          disabled={isActing}
          onClick={() => settle(onStart())}
        >
          <RotateCcw size={14} /> Restart preview
        </button>
      </div>
    );
  }
  if (preview?.status === "stopped") {
    return <PreviewNotice icon={<Square />} title="Preview stopped" detail="Runtime released" />;
  }
  if (task.status === "failed") {
    return (
      <PreviewNotice
        icon={<AlertCircle />}
        title="Task failed"
        detail={runtimeHealth?.runtime?.error_code ?? "WORKER_INTERRUPTED"}
        tone="danger"
      />
    );
  }
  if (runtimeHealth !== null && !runtimeHealth.executor.available) {
    return (
      <PreviewNotice
        icon={<AlertCircle />}
        title="Runtime unavailable"
        detail={runtimeHealth.executor.error_code ?? "SANDBOX_UNAVAILABLE"}
        tone="warning"
      />
    );
  }
  const canStart = task.status === "executing";
  return (
    <div className="preview-notice">
      <RotateCcw size={22} />
      <strong>Preview not started</strong>
      <button
        className="primary-command"
        type="button"
        disabled={!canStart || isActing}
        onClick={() => settle(onStart())}
      >
        <Play size={14} /> Start preview
      </button>
    </div>
  );
}

function PreviewNotice({
  icon,
  title,
  detail,
  tone = "neutral",
}: {
  icon: ReactNode;
  title: string;
  detail: string;
  tone?: "neutral" | "warning" | "danger";
}) {
  return (
    <div className={`preview-notice preview-notice-${tone}`} role="status">
      {icon}
      <strong>{title}</strong>
      <span>{detail}</span>
    </div>
  );
}

export function isSafePreviewUrl(
  value: string,
  executionTarget: "local" | "cloud",
): boolean {
  try {
    const parsed = new URL(value);
    if (
      parsed.username !== "" ||
      parsed.password !== "" ||
      parsed.search !== "" ||
      parsed.hash !== ""
    ) {
      return false;
    }
    return executionTarget === "local"
      ? parsed.protocol === "http:" &&
          parsed.hostname === "127.0.0.1" &&
          parsed.port !== ""
      : parsed.protocol === "https:";
  } catch {
    return false;
  }
}

function decisionLabel(task: Task | null, previewStatus: string | null): string {
  if (task?.status === "accepted") return "Version active";
  if (task?.status === "rejected") return "Candidate discarded";
  if (task?.status === "archived") return "Task archived";
  if (task?.status === "ready") return "Review complete";
  if (previewStatus === "ready") return "Preview ready; review still required";
  return task === null ? "No candidate version" : task.status.replaceAll("_", " ");
}

function settle(operation: Promise<void>): void {
  void operation.catch(() => undefined);
}
