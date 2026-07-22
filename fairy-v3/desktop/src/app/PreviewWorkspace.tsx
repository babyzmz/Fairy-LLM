import { Bug, Code2, MonitorSmartphone, PanelTop } from "lucide-react";
import { type ReactNode, useMemo, useState } from "react";

import { BrowserPanel } from "./BrowserPanel";
import { isSafePreviewUrl, PreviewPanel } from "./PreviewPanel";
import type { WorkspaceModel } from "./workspaceTypes";

type PreviewMode = "runtime" | "browser" | "responsive" | "diagnostics";

export function PreviewWorkspace({ model }: { model: WorkspaceModel }) {
  const [mode, setMode] = useState<PreviewMode>("runtime");
  const preview = model.preview?.preview ?? null;
  const runtimeUrl = useMemo(() => {
    if (!preview?.url || !isSafePreviewUrl(preview.url, preview.execution_target)) return null;
    return preview.url;
  }, [preview]);

  return (
    <section className="preview-workspace">
      <nav className="preview-mode-tabs" aria-label="Preview mode">
        <ModeButton active={mode === "runtime"} icon={<PanelTop size={14} />} label="Runtime" onClick={() => setMode("runtime")} />
        <ModeButton active={mode === "browser"} icon={<Code2 size={14} />} label="Browser" onClick={() => setMode("browser")} />
        <ModeButton active={mode === "responsive"} icon={<MonitorSmartphone size={14} />} label="Responsive" onClick={() => setMode("responsive")} />
        {model.developerMode ? <ModeButton active={mode === "diagnostics"} icon={<Bug size={14} />} label="Diagnostics" onClick={() => setMode("diagnostics")} /> : null}
      </nav>
      <div className="preview-mode-content">
        {mode === "runtime" ? (
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
        ) : mode === "browser" ? (
          <BrowserPanel model={model} runtimeUrl={runtimeUrl} />
        ) : mode === "responsive" ? (
          <ResponsivePreview url={runtimeUrl} />
        ) : (
          <Diagnostics model={model} />
        )}
      </div>
    </section>
  );
}

function ModeButton({ active, icon, label, onClick }: { active: boolean; icon: ReactNode; label: string; onClick(): void }) {
  return <button type="button" aria-pressed={active} onClick={onClick}>{icon}{label}</button>;
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
