import { Eye, Files, GalleryVerticalEnd } from "lucide-react";
import { useEffect, useState } from "react";

import type { WorkspaceModel } from "./workspaceModel";
import { PreviewPanel } from "./PreviewPanel";
import { WorkspaceFilesPanel } from "./WorkspaceFilesPanel";
import { WorkspaceOutputsPanel } from "./WorkspaceOutputsPanel";
import "./workspace-inspector.css";

export function WorkspaceInspector({ model }: { model: WorkspaceModel }) {
  const hasFiles = model.workspaceFiles.length > 0;
  const previewReady = model.preview?.preview.status === "ready";
  const hasOutputs = model.mediaJobs.length > 0;
  const hasActiveOutput = model.mediaJobs.some((job) => !["completed", "failed", "cancelled", "interrupted"].includes(job.status));
  const [tab, setTab] = useState<"preview" | "files" | "outputs">(previewReady ? "preview" : "files");
  const scopeKey = [
    model.workspaceTask?.conversation_id ?? "no-conversation",
    model.workspaceTask?.id ?? "no-task",
    model.workspaceTask?.workspace_id ?? "no-workspace",
    model.workspaceTask?.target_version_id ?? "no-version",
  ].join(":");

  useEffect(() => {
    if (hasActiveOutput) setTab("outputs");
    else if (previewReady) setTab("preview");
    else if (hasOutputs) setTab("outputs");
    else if (hasFiles) setTab("files");
  }, [hasActiveOutput, hasFiles, hasOutputs, previewReady, model.workspaceTask?.id]);

  if (model.workspaceTask === null && !hasFiles && !hasOutputs) return null;

  return (
    <aside className="workspace-inspector" aria-label="Workspace inspector">
      <div className="workspace-inspector-tabs" role="tablist" aria-label="Workspace view">
        <button type="button" role="tab" aria-selected={tab === "preview"} onClick={() => setTab("preview")}>
          <Eye size={14} /> Preview
        </button>
        <button type="button" role="tab" aria-selected={tab === "files"} onClick={() => setTab("files")}>
          <Files size={14} /> Files
          {hasFiles ? <span>{model.workspaceFiles.length}</span> : null}
        </button>
        <button type="button" role="tab" aria-selected={tab === "outputs"} onClick={() => setTab("outputs")}>
          <GalleryVerticalEnd size={14} /> Outputs
          {hasOutputs ? <span>{model.mediaJobs.length}</span> : null}
        </button>
      </div>
      <div className="workspace-inspector-content">
        {tab === "preview" ? (
          <PreviewPanel
            task={model.workspaceTask}
            context={model.preview}
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
          <WorkspaceOutputsPanel
            scopeKey={scopeKey}
            jobs={model.mediaJobs}
            loading={model.mediaJobsLoading}
            onOpenStream={model.openWorkspaceFileStream}
            onCancel={model.cancelMediaJob}
          />
        )}
      </div>
    </aside>
  );
}
