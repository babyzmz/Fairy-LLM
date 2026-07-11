import {
  Bell,
  Boxes,
  Cloud,
  Code2,
  FolderInput,
  FolderPlus,
  History,
  MessageSquareText,
  Mic,
  Play,
  Send,
  Settings,
  ShieldCheck,
  Sparkles,
  WifiOff,
} from "lucide-react";
import { useState } from "react";

import { PreviewPanel } from "./PreviewPanel";
import { TaskTimeline } from "./TaskTimeline";
import type { WorkspaceModel } from "./workspaceModel";
import "./workspace.css";

interface WorkspaceShellProps {
  model: WorkspaceModel;
}

export function WorkspaceShell({ model }: WorkspaceShellProps) {
  const [message, setMessage] = useState("");
  const [projectName, setProjectName] = useState("");
  const [importPath, setImportPath] = useState("");

  const submitTask = async () => {
    const request = message.trim();
    if (!request) return;
    try {
      await model.createTask(request);
      setMessage("");
    } catch {
      // The model exposes the durable action error in the shell.
    }
  };

  return (
    <div className="workspace-shell" data-workspace-state={model.state}>
      <nav className="primary-rail" aria-label="Primary navigation">
        <button className="brand-button" aria-label="Fairy home" title="Fairy home">
          <Sparkles size={21} />
        </button>
        <div className="rail-actions">
          <button
            className="rail-button active"
            aria-label="Project workspace"
            title="Project workspace"
          >
            <Boxes size={19} />
          </button>
          <button className="rail-button" aria-label="Conversations" title="Conversations">
            <MessageSquareText size={19} />
          </button>
          <button className="rail-button" aria-label="Task history" title="Task history">
            <History size={19} />
          </button>
        </div>
        <div className="rail-actions rail-bottom">
          <button className="rail-button" aria-label="Notifications" title="Notifications">
            <Bell size={19} />
          </button>
          <button className="rail-button" aria-label="Settings" title="Settings">
            <Settings size={19} />
          </button>
        </div>
      </nav>

      <div className="workspace-body">
        <header className="context-bar" role="banner">
          <div className="context-identity">
            <span className="fairy-wordmark">FAIRY</span>
            <label className="context-select">
              <span className="sr-only">Select project</span>
              <select
                aria-label="Select project"
                value={model.selectedProject?.id ?? ""}
                disabled={model.projects.length === 0}
                onChange={(event) => model.selectProject(event.target.value)}
              >
                {model.projects.length === 0 ? <option value="">No project</option> : null}
                {model.projects.map((project) => (
                  <option key={project.id} value={project.id}>
                    {project.name}
                  </option>
                ))}
              </select>
            </label>
            <span className="context-separator">/</span>
            <label className="context-select context-conversation">
              <span className="sr-only">Select conversation</span>
              <select
                aria-label="Select conversation"
                value={model.selectedConversation?.id ?? ""}
                disabled={model.conversations.length === 0}
                onChange={(event) => model.selectConversation(event.target.value)}
              >
                {model.conversations.length === 0 ? (
                  <option value="">No conversation</option>
                ) : null}
                {model.conversations.map((conversation, index) => (
                  <option key={conversation.id} value={conversation.id}>
                    Conversation {index + 1}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="telemetry-strip" aria-label="Workspace telemetry">
            <span className="telemetry-item">
              <Code2 size={14} /> {versionLabel(model)}
            </span>
            <span className="telemetry-item">
              <Play size={14} /> {model.selectedTask?.execution_target ?? "local"}
            </span>
            <span className="telemetry-item">
              <ShieldCheck size={14} /> {model.capabilities?.profile ?? "standard"}
            </span>
            <span
              className={`telemetry-item ${model.state === "offline" ? "offline" : "online"}`}
            >
              {model.state === "offline" ? <WifiOff size={14} /> : <Cloud size={14} />}
              {model.statusLabel}
            </span>
          </div>
        </header>

        {model.actionError || model.errorMessage ? (
          <div className="workspace-error" role="alert">
            {model.actionError ?? model.errorMessage}
          </div>
        ) : null}

        <main className="workspace-main">
          {model.state === "ready" ? (
            <>
              <TaskTimeline
                task={model.selectedTask}
                tasks={model.tasks}
                events={model.events}
                approvals={model.approvals}
                isActing={model.isActing}
                onSelectTask={model.selectTask}
                onDecision={model.decideApproval}
              />
              <PreviewPanel
                task={model.selectedTask}
                context={model.preview}
                runtimeHealth={model.runtimeHealth}
                isActing={model.isActing}
                onStart={model.startPreview}
                onStop={model.stopPreview}
                onAccept={model.acceptVersion}
                onDiscard={model.discardVersion}
              />
            </>
          ) : (
            <EmptyWorkspace
              state={model.state}
              projectName={projectName}
              importPath={importPath}
              isActing={model.isActing}
              onProjectName={setProjectName}
              onImportPath={setImportPath}
              onCreate={async () => {
                const name = projectName.trim();
                if (!name) return;
                try {
                  await model.createProject(name);
                  setProjectName("");
                } catch {
                  // The model exposes the action error.
                }
              }}
              onImport={async () => {
                const path = importPath.trim();
                const name = projectName.trim() || folderName(path);
                if (!path || !name) return;
                try {
                  await model.importProject(name, path);
                  setProjectName("");
                  setImportPath("");
                } catch {
                  // The model exposes the action error.
                }
              }}
            />
          )}
        </main>

        <form
          className="composer"
          onSubmit={(event) => {
            event.preventDefault();
            void submitTask();
          }}
        >
          <button
            className="icon-button"
            type="button"
            aria-label="Voice input"
            title="Voice input"
            disabled={model.state !== "ready"}
          >
            <Mic size={17} />
          </button>
          <label className="composer-field">
            <span className="sr-only">Message Fairy</span>
            <input
              aria-label="Message Fairy"
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              placeholder="Describe the next change"
              disabled={model.state !== "ready" || model.isActing}
            />
          </label>
          <button
            className="send-button"
            type="submit"
            aria-label="Send message"
            title="Send message"
            disabled={!message.trim() || model.state !== "ready" || model.isActing}
          >
            <Send size={17} />
          </button>
        </form>
      </div>
    </div>
  );
}

interface EmptyWorkspaceProps {
  state: WorkspaceModel["state"];
  projectName: string;
  importPath: string;
  isActing: boolean;
  onProjectName(value: string): void;
  onImportPath(value: string): void;
  onCreate(): Promise<void>;
  onImport(): Promise<void>;
}

function EmptyWorkspace({
  state,
  projectName,
  importPath,
  isActing,
  onProjectName,
  onImportPath,
  onCreate,
  onImport,
}: EmptyWorkspaceProps) {
  if (state === "loading") {
    return (
      <section className="workspace-state" aria-label="Loading workspace">
        <span className="state-pulse" />
        <h1>Opening Fairy</h1>
        <p>Connecting to Core</p>
      </section>
    );
  }
  if (state === "offline") {
    return (
      <section className="workspace-state workspace-state-offline" aria-label="Core offline">
        <WifiOff size={25} />
        <h1>Core offline</h1>
        <p>Local workspace data is unavailable</p>
      </section>
    );
  }
  return (
    <section className="workspace-state workspace-create" aria-label="Create or import project">
      <div className="create-heading">
        <Boxes size={25} />
        <h1>Projects</h1>
      </div>
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
        <label>
          <span>Folder path</span>
          <input
            value={importPath}
            onChange={(event) => onImportPath(event.target.value)}
            placeholder="C:\\Projects\\example"
          />
        </label>
        <button
          className="secondary-command"
          type="button"
          disabled={!importPath.trim() || isActing}
          onClick={() => void onImport()}
        >
          <FolderInput size={15} /> Import folder
        </button>
      </div>
    </section>
  );
}

function versionLabel(model: WorkspaceModel): string {
  if (model.selectedVersion === null) return "no version";
  return model.selectedVersion.visibility.replaceAll("_", " ");
}

function folderName(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).at(-1) ?? "Imported project";
}
