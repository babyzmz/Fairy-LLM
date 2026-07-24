import { Bug, Code2, MonitorSmartphone, PanelTop, Sparkles } from "lucide-react";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";

import { BrowserPanel } from "./BrowserPanel";
import { isSafePreviewUrl, PreviewPanel } from "./PreviewPanel";
import { projectMediaJobs, WorkspaceOutputsPanel } from "./WorkspaceOutputsPanel";
import type { WorkspaceModel } from "./workspaceTypes";

type PreviewMode = "runtime" | "generated" | "browser" | "responsive" | "diagnostics";

const terminalMediaStatuses = new Set(["completed", "failed", "cancelled", "interrupted"]);

export function PreviewWorkspace({ model }: { model: WorkspaceModel }) {
  const mediaJobs = useMemo(() => projectMediaJobs(model.mediaJobs), [model.mediaJobs]);
  const hasMedia = mediaJobs.length > 0;
  const hasActiveMedia = mediaJobs.some((job) => !terminalMediaStatuses.has(job.status));
  const [mode, setMode] = useState<PreviewMode>(hasActiveMedia ? "generated" : "runtime");
  const manualMode = useRef(false);
  const scopeKey = [
    model.workspaceTask?.conversation_id ?? "no-conversation",
    model.workspaceTask?.id ?? "no-task",
    model.workspaceTask?.workspace_id ?? "no-workspace",
    model.workspaceTask?.target_version_id ?? "no-version",
  ].join(":");

  useEffect(() => {
    manualMode.current = false;
    setMode(hasActiveMedia ? "generated" : "runtime");
    // Only reset when the inspected scope changes, not on every media update.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scopeKey]);

  useEffect(() => {
    if (manualMode.current) return;
    if (hasActiveMedia) setMode("generated");
    else if (!hasMedia) setMode((current) => (current === "generated" ? "runtime" : current));
  }, [hasActiveMedia, hasMedia]);

  const preview = model.preview?.preview ?? null;
  const runtimeUrl = useMemo(() => {
    if (!preview?.url || !isSafePreviewUrl(preview.url, preview.execution_target)) return null;
    return preview.url;
  }, [preview]);

  const selectMode = (next: PreviewMode) => {
    manualMode.current = true;
    setMode(next);
  };

  // A stale "generated" selection with no jobs falls back to the runtime view.
  const effectiveMode: PreviewMode = mode === "generated" && !hasMedia ? "runtime" : mode;

  return (
    <section className="preview-workspace">
      <nav className="preview-mode-tabs" aria-label="Preview mode">
        <ModeButton active={effectiveMode === "runtime"} icon={<PanelTop size={14} />} label="Runtime" onClick={() => selectMode("runtime")} />
        {hasMedia ? (
          <ModeButton
            active={effectiveMode === "generated"}
            icon={<Sparkles size={14} />}
            label="Generated"
            onClick={() => selectMode("generated")}
            badge={hasActiveMedia}
          />
        ) : null}
        <ModeButton active={effectiveMode === "browser"} icon={<Code2 size={14} />} label="Browser" onClick={() => selectMode("browser")} />
        <ModeButton active={effectiveMode === "responsive"} icon={<MonitorSmartphone size={14} />} label="Responsive" onClick={() => selectMode("responsive")} />
        {model.developerMode ? <ModeButton active={effectiveMode === "diagnostics"} icon={<Bug size={14} />} label="Diagnostics" onClick={() => selectMode("diagnostics")} /> : null}
      </nav>
      <div className="preview-mode-content">
        {effectiveMode === "runtime" ? (
          <PreviewPanel
            task={model.workspaceTask}
            context={model.preview}
            activation={model.previewActivation}
            activationLoading={model.previewActivationLoading}
            activationError={model.previewActivationError}
            runtimeHealth={model.runtimeHealth}
            isActing={model.isActing}
            developerMode={model.developerMode}
            workspaceGeneration={model.workspaceGeneration}
            showVersionActions={model.mode === "project"}
            onStart={model.startPreview}
            onStop={model.stopPreview}
            onReview={model.reviewTask}
            onAccept={model.acceptVersion}
            onDiscard={model.discardVersion}
          />
        ) : effectiveMode === "generated" ? (
          <WorkspaceOutputsPanel
            scopeKey={scopeKey}
            jobs={model.mediaJobs}
            loading={model.mediaJobsLoading}
            onOpenStream={model.openWorkspaceFileStream}
            onCancel={model.cancelMediaJob}
          />
        ) : effectiveMode === "browser" ? (
          <BrowserPanel model={model} runtimeUrl={runtimeUrl} />
        ) : effectiveMode === "responsive" ? (
          <ResponsivePreview url={runtimeUrl} />
        ) : (
          <Diagnostics model={model} />
        )}
      </div>
    </section>
  );
}

function ModeButton({ active, icon, label, onClick, badge = false }: { active: boolean; icon: ReactNode; label: string; onClick(): void; badge?: boolean }) {
  return (
    <button type="button" aria-pressed={active} onClick={onClick}>
      {icon}{label}
      {badge ? <i className="preview-mode-badge" aria-hidden="true" /> : null}
    </button>
  );
}

function ResponsivePreview({ url }: { url: string | null }) {
  const [viewport, setViewport] = useState<"mobile" | "tablet" | "desktop">("mobile");
  const dimensions = viewport === "mobile" ? [390, 844] : viewport === "tablet" ? [768, 1024] : [1280, 720];
  return (
    <div className="responsive-preview">
      <div className="responsive-controls" role="group" aria-label="Responsive viewport">
        {(["mobile", "tablet", "desktop"] as const).map((item) => <button type="button" aria-pressed={viewport === item} key={item} onClick={() => setViewport(item)}>{item}</button>)}
        <span>{dimensions[0]} x {dimensions[1]}</span>
      </div>
      <div className="responsive-stage">
        {url ? <iframe src={url} title="Responsive Runtime preview" sandbox="allow-forms allow-scripts" style={{ width: dimensions[0], aspectRatio: `${dimensions[0]} / ${dimensions[1]}` }} /> : <span>Start Runtime Preview to inspect responsive layouts.</span>}
      </div>
    </div>
  );
}

function Diagnostics({ model }: { model: WorkspaceModel }) {
  return (
    <div className="preview-diagnostics">
      <section><h3>Runtime</h3><pre>{JSON.stringify(model.runtimeHealth, null, 2)}</pre></section>
      <section><h3>Browser accessibility snapshot</h3><pre>{model.browserSnapshot?.aria_snapshot ?? "No Browser snapshot"}</pre></section>
    </div>
  );
}
