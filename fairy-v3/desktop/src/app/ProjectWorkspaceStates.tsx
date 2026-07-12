import {
  Boxes,
  FolderInput,
  FolderOpen,
  FolderPlus,
  RefreshCw,
  WifiOff,
} from "lucide-react";

import type { WorkspaceModel } from "./workspaceModel";

interface ProjectSetupProps {
  projectName: string;
  importPath: string;
  isActing: boolean;
  onProjectName(value: string): void;
  onImportPath(value: string): void;
  onSelectFolder(): Promise<void>;
  onCreate(): Promise<void>;
  onImport(): Promise<void>;
}

interface EmptyWorkspaceProps extends ProjectSetupProps {
  state: WorkspaceModel["state"];
  onRetry(): Promise<void>;
}

export function EmptyWorkspace({
  state,
  onRetry,
  ...setup
}: EmptyWorkspaceProps) {
  if (state === "loading")
    return (
      <section className="workspace-state" aria-label="Loading workspace">
        <span className="state-pulse" />
        <h1>Opening Fairy</h1>
        <p>Connecting to Core</p>
      </section>
    );
  if (state === "offline")
    return (
      <section
        className="workspace-state workspace-state-offline"
        aria-label="Core offline"
      >
        <WifiOff size={25} />
        <h1>Core offline</h1>
        <p>Local workspace data is unavailable</p>
        <button
          className="secondary-command"
          type="button"
          onClick={() => void onRetry()}
        >
          <RefreshCw size={15} /> Retry Core
        </button>
      </section>
    );
  return (
    <section
      className="workspace-state workspace-create"
      aria-label="Create or import project"
    >
      <div className="create-heading">
        <Boxes size={25} />
        <h1>Projects</h1>
      </div>
      <ProjectSetup {...setup} />
    </section>
  );
}

export function RecoveryNotice({ model }: { model: WorkspaceModel }) {
  const code = model.actionErrorCode;
  const title =
    code === "VERSION_CONFLICT"
      ? "Version conflict preserved"
      : code === "WORKER_INTERRUPTED"
        ? "Worker interrupted"
        : "Workspace request failed";
  const detail =
    code === "VERSION_CONFLICT"
      ? "The candidate version remains separate. Active Version was not overwritten."
      : (model.actionError ?? model.errorMessage ?? model.projectError);
  return (
    <div className="workspace-error recovery-notice" role="alert">
      <div>
        <strong>{title}</strong>
        <span>{detail}</span>
      </div>
      <button
        className="icon-button"
        type="button"
        aria-label="Retry workspace"
        title="Retry workspace"
        onClick={() => void model.retryWorkspace()}
      >
        <RefreshCw size={15} />
      </button>
    </div>
  );
}

export function ProjectSetup({
  projectName,
  importPath,
  isActing,
  onProjectName,
  onImportPath,
  onSelectFolder,
  onCreate,
  onImport,
}: ProjectSetupProps) {
  return (
    <div className="project-setup">
      <div className="create-controls">
        <label>
          <span>Project name</span>
          <input
            value={projectName}
            onChange={(event) => onProjectName(event.target.value)}
            placeholder="New project"
          />
        </label>
        <button
          className="primary-command"
          type="button"
          disabled={!projectName.trim() || isActing}
          onClick={() => void onCreate()}
        >
          <FolderPlus size={15} /> Create project
        </button>
      </div>
      <div className="create-controls import-controls">
        <div className="folder-field">
          <label htmlFor="project-folder-path">Folder path</label>
          <div>
            <input
              id="project-folder-path"
              value={importPath}
              onChange={(event) => onImportPath(event.target.value)}
              placeholder="C:\\Projects\\example"
            />
            <button
              className="icon-button"
              type="button"
              aria-label="Choose project folder"
              title="Choose project folder"
              disabled={isActing}
              onClick={() => void onSelectFolder()}
            >
              <FolderOpen size={16} />
            </button>
          </div>
        </div>
        <button
          className="secondary-command"
          type="button"
          disabled={!importPath.trim() || isActing}
          onClick={() => void onImport()}
        >
          <FolderInput size={15} /> Import folder
        </button>
      </div>
    </div>
  );
}

export function folderName(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).at(-1) ?? "Imported project";
}
