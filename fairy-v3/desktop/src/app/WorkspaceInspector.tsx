import { Eye, Files, Network } from "lucide-react";
import { type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent, useEffect, useRef, useState } from "react";

import type { WorkspaceModel } from "./workspaceModel";
import { PreviewWorkspace } from "./PreviewWorkspace";
import { WorkspaceFilesPanel } from "./WorkspaceFilesPanel";
import { ObsidianPanel } from "./ObsidianPanel";
import "./workspace-inspector.css";

export function WorkspaceInspector({ model }: { model: WorkspaceModel }) {
  const hasFiles = model.workspaceFiles.length > 0;
  const previewReady = model.preview?.preview.status === "ready";
  const previewStarting = model.previewActivationLoading ||
    ["ready", "starting", "waiting_for_slot"].includes(model.previewActivation?.outcome ?? "");
  const activePreviewId = model.workspaceActivePreviewId;
  const hasMedia = model.mediaJobs.length > 0;
  const hasActiveMedia = model.mediaJobs.some(
    (job) => !["completed", "failed", "cancelled", "interrupted"].includes(job.status),
  );
  // Preview now also hosts generated media, so any media makes it available.
  const hasPreview = previewReady || previewStarting || activePreviewId !== null || hasMedia;
  const [tab, setTab] = useState<"preview" | "files" | "obsidian">(
    hasPreview ? "preview" : "files",
  );
  const manuallySelectedTab = useRef(false);
  const scopeKey = [
    model.workspaceTask?.conversation_id ?? "no-conversation",
    model.workspaceTask?.id ?? "no-task",
    model.workspaceTask?.workspace_id ?? "no-workspace",
    model.workspaceTask?.target_version_id ?? "no-version",
  ].join(":");
  const recommendedTab = hasPreview ? "preview" : hasFiles ? "files" : "preview";

  useEffect(() => {
    manuallySelectedTab.current = false;
    setTab(recommendedTab);
  }, [scopeKey]);

  useEffect(() => {
    // Active generation now lives inside Preview, so pull attention there.
    if (hasActiveMedia) {
      manuallySelectedTab.current = false;
      setTab("preview");
    } else if (!manuallySelectedTab.current) {
      setTab(recommendedTab);
    }
  }, [hasActiveMedia, recommendedTab]);

  useEffect(() => {
    const parent = document.querySelector<HTMLElement>(".unified-workspace-chat, .workspace-main");
    if (parent === null) return;
    const raw = localStorage.getItem("fairy.workspace.inspector-width");
    // With no saved width, leave the CSS percentage default in place instead of
    // forcing the minimum pixel width.
    if (raw === null || raw.trim() === "") return;
    const saved = Number(raw);
    if (!Number.isFinite(saved)) return;
    parent.style.setProperty("--inspector-width", `${clampInspectorWidth(saved)}px`);
  }, []);

  const hasKnowledgeScope = model.mode === "project"
    ? model.selectedProject !== null
    : model.selectedChatConversation !== null;
  if (model.workspaceTask === null && !hasFiles && !hasMedia && !hasKnowledgeScope) return null;

  return (
    <aside className="workspace-inspector" aria-label="Workspace inspector">
      <div
        className="workspace-inspector-resizer"
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize workspace inspector"
        tabIndex={0}
        onKeyDown={resizeWithKeyboard}
        onPointerDown={startResize}
      />
      <div className="workspace-inspector-tabs" role="tablist" aria-label="Workspace view">
        <button type="button" role="tab" aria-selected={tab === "preview"} onClick={() => selectTab("preview")}>
          <Eye size={14} /> Preview
        </button>
        <button type="button" role="tab" aria-selected={tab === "files"} onClick={() => selectTab("files")}>
          <Files size={14} /> Files
          {hasFiles ? <span>{model.workspaceFiles.length}</span> : null}
        </button>
        <button type="button" role="tab" aria-selected={tab === "obsidian"} onClick={() => selectTab("obsidian")}>
          <Network size={14} /> Obsidian
        </button>
      </div>
      <div className="workspace-inspector-content">
        {tab === "preview" ? (
          <PreviewWorkspace model={model} />
        ) : tab === "files" ? (
          <WorkspaceFilesPanel
            scopeKey={scopeKey}
            files={model.workspaceFiles}
            assetSets={model.assetSets}
            versions={model.versions}
            currentVersionId={model.workspaceTask?.target_version_id ?? model.selectedVersion?.id ?? null}
            loading={model.workspaceFilesLoading}
            onRead={model.readWorkspaceFile}
            onOpenStream={model.openWorkspaceFileStream}
            onPresent={model.presentWorkspaceFile}
            onCompare={model.compareWorkspaceFile}
            onResolveFileSet={model.resolveWorkspaceFileSet}
            onListAnnotations={model.listFileAnnotations}
            onUpdateAnnotations={model.updateFileAnnotations}
            onCreateTextSelection={model.createTextSelection}
            onCreateSceneSelection={model.createSceneSelection}
            onReveal={model.revealWorkspaceFile}
            onRefresh={model.refreshWorkspaceFiles}
            onUpload={model.uploadWorkspaceFiles}
            onRename={model.renameWorkspaceFile}
            onDelete={model.deleteWorkspaceFile}
            onExport={model.exportWorkspace}
          />
        ) : (
          <ObsidianPanel model={model} onOpenFiles={() => selectTab("files")} />
        )}
      </div>
    </aside>
  );

  function selectTab(next: "preview" | "files" | "obsidian") {
    manuallySelectedTab.current = true;
    setTab(next);
  }
}

function startResize(event: ReactPointerEvent<HTMLDivElement>) {
  event.currentTarget.setPointerCapture(event.pointerId);
  const parent = event.currentTarget.closest<HTMLElement>(".unified-workspace-chat, .workspace-main");
  if (parent === null) return;
  const move = (pointer: PointerEvent) => {
    const width = clampInspectorWidth(window.innerWidth - pointer.clientX);
    parent.style.setProperty("--inspector-width", `${width}px`);
    localStorage.setItem("fairy.workspace.inspector-width", String(width));
  };
  const finish = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", finish);
    window.removeEventListener("pointercancel", finish);
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", finish, { once: true });
  window.addEventListener("pointercancel", finish, { once: true });
}

function resizeWithKeyboard(event: ReactKeyboardEvent<HTMLDivElement>) {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  const parent = event.currentTarget.closest<HTMLElement>(".unified-workspace-chat, .workspace-main");
  if (parent === null) return;
  event.preventDefault();
  const current = Number.parseFloat(getComputedStyle(parent).getPropertyValue("--inspector-width"))
    || Math.round(window.innerWidth * 0.42);
  const next = event.key === "Home"
    ? 360
    : event.key === "End"
      ? Math.round(window.innerWidth * 0.75)
      : current + (event.key === "ArrowLeft" ? 24 : -24);
  const width = clampInspectorWidth(next);
  parent.style.setProperty("--inspector-width", `${width}px`);
  localStorage.setItem("fairy.workspace.inspector-width", String(width));
}

function clampInspectorWidth(value: number): number {
  return Math.max(360, Math.min(Math.round(window.innerWidth * 0.75), Math.round(value)));
}
