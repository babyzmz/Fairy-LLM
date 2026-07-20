import {
  Boxes,
  FolderInput,
  FolderOpen,
  FolderPlus,
  MessageSquarePlus,
  RefreshCw,
  WifiOff,
} from "lucide-react";

import "./project-workspace-states.css";
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
  statusLabel: WorkspaceModel["statusLabel"];
  onRetry(): Promise<void>;
}

export function EmptyWorkspace({
  state,
  statusLabel,
  onRetry,
  ...setup
}: EmptyWorkspaceProps) {
  if (state === "loading")
    return (
      <section className="workspace-state" aria-label="Loading workspace">
        <span className="state-pulse" />
        <h1>Opening Fairy</h1>
        <p>{statusLabel === "Core starting" ? "Connecting to Core" : "Loading workspace data"}</p>
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
  const permissionBlocked =
    code === "CAPABILITY_NOT_AVAILABLE" ||
    (model.permissionProfile === "observe" && model.projectError !== null);
  const title =
    code === "VERSION_CONFLICT"
      ? "Version conflict preserved"
      : code === "PROJECT_BUSY"
        ? "Project is busy"
      : code === "WORKER_INTERRUPTED"
        ? "Worker interrupted"
        : permissionBlocked
          ? "Permission prevents this action"
        : "Workspace request failed";
  const detail =
    code === "VERSION_CONFLICT"
      ? "The candidate version remains separate. Active Version was not overwritten."
      : code === "PROJECT_BUSY"
        ? "Finish or cancel the active Turn, Preview, or Runtime before continuing."
      : permissionBlocked
        ? "Project Tasks require Standard or Autonomous permissions. Observe remains read-only."
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

export function ProjectOverview({ model }: { model: WorkspaceModel }) {
  const project = model.selectedProject;
  if (project === null) return null;
  const conversations = model.projectConversations.filter(
    (conversation) => conversation.project_id === project.id,
  );
  return (
    <section className="project-overview" aria-labelledby="project-overview-heading">
      <header>
        <div>
          <span className="eyebrow">Project</span>
          <h1 id="project-overview-heading">{project.name}</h1>
          <p>{conversations.length} chat{conversations.length === 1 ? "" : "s"}</p>
        </div>
        <button
          className="primary-command"
          type="button"
          disabled={model.isActing}
          onClick={() => void model.createProjectConversation(project)}
        >
          <MessageSquarePlus size={16} /> New chat
        </button>
      </header>
      <div className="project-overview-threads" aria-label="Project chats">
        {conversations.length === 0 ? (
          <p className="project-overview-empty">No chats</p>
        ) : conversations.map((conversation) => (
          <button
            key={conversation.id}
            type="button"
            onClick={() => model.selectConversation(conversation.id)}
          >
            <MessageSquarePlus size={15} />
            <span>
              <strong>{conversation.title}</strong>
              <small>{formatOverviewDate(conversation.updated_at)}</small>
            </span>
          </button>
        ))}
      </div>
    </section>
  );
}

function formatOverviewDate(value: string) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" })
    .format(new Date(value));
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
