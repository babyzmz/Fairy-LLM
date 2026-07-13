import { Eye, Files } from "lucide-react";
import { useEffect, useState } from "react";

import type { WorkspaceModel } from "./workspaceModel";
import { PreviewPanel } from "./PreviewPanel";
import { WorkspaceFilesPanel } from "./WorkspaceFilesPanel";

export function WorkspaceInspector({ model }: { model: WorkspaceModel }) {
  const hasFiles = model.workspaceFiles.length > 0;
  const previewReady = model.preview?.preview.status === "ready";
  const [tab, setTab] = useState<"preview" | "files">(previewReady ? "preview" : "files");

  useEffect(() => {
    if (previewReady) setTab("preview");
    else if (hasFiles) setTab("files");
  }, [hasFiles, previewReady, model.workspaceTask?.id]);

  if (model.workspaceTask === null && !hasFiles) return null;

  return (
    <aside className="workspace-inspector" aria-label="Workspace inspector">
      <div className="workspace-inspector-tabs" role="tablist" aria-label="Workspace view">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "preview"}
          onClick={() => setTab("preview")}
        >
          <Eye size={14} /> Preview
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "files"}
          onClick={() => setTab("files")}
        >
          <Files size={14} /> Files
          {hasFiles ? <span>{model.workspaceFiles.length}</span> : null}
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
            showVersionActions={model.mode === "project"}
            onStart={model.startPreview}
            onStop={model.stopPreview}
            onReview={model.reviewTask}
            onAccept={model.acceptVersion}
            onDiscard={model.discardVersion}
          />
        ) : (
          <WorkspaceFilesPanel
            files={model.workspaceFiles}
            loading={model.workspaceFilesLoading}
            onRead={model.readWorkspaceFile}
            onReveal={model.revealWorkspaceFile}
            onRefresh={model.refreshWorkspaceFiles}
          />
        )}
      </div>
    </aside>
  );
}
