import {
  Boxes,
  Cloud,
  FolderInput,
  FolderOpen,
  FolderPlus,
  Play,
  RefreshCw,
  ShieldCheck,
  WifiOff,
  X,
} from "lucide-react";
import { useState } from "react";

import { ChatWorkspace } from "../chat/ChatWorkspace";
import { Composer } from "../chat/Composer";
import { ProviderSettings } from "../settings/ProviderSettings";
import { ExecutionControls } from "../settings/ExecutionControls";
import { ExtensionSettings } from "../settings/ExtensionSettings";
import { KnowledgeSettings } from "../settings/KnowledgeSettings";
import { HistorySidebar } from "./HistorySidebar";
import { PreviewPanel } from "./PreviewPanel";
import { TaskTimeline } from "./TaskTimeline";
import type { WorkspaceModel } from "./workspaceModel";
import "./workspace.css";
import "./project-manager.css";

interface WorkspaceShellProps {
  model: WorkspaceModel;
}

export function WorkspaceShell({ model }: WorkspaceShellProps) {
  const [projectName, setProjectName] = useState("");
  const [importPath, setImportPath] = useState("");
  const [projectManagerOpen, setProjectManagerOpen] = useState(false);
  const providerAvailable = selectedProviderAvailable(model);
  const visionAvailable =
    model.providers
      .find((provider) => provider.id === model.selectedProfileId)
      ?.capabilities.includes("vision") ?? false;

  return (
    <div className="workspace-shell" data-workspace-state={model.state}>
      <HistorySidebar model={model} onCreateProject={() => setProjectManagerOpen(true)} />

      <div className="workspace-body">
        <ContextBar model={model} />
        <div className="mode-bar">
          <span className="mode-label">{model.mode === "chat" ? "Chat" : "Project"}</span>
          <div className="mode-controls">
            {model.mode === "project" ? (
              <div className="project-control">
                <button
                  className={`icon-button ${projectManagerOpen ? "active" : ""}`}
                  type="button"
                  aria-label="Manage projects"
                  title="Manage projects"
                  aria-expanded={projectManagerOpen}
                  onClick={() => setProjectManagerOpen((current) => !current)}
                >
                  <FolderPlus size={16} />
                </button>
                {projectManagerOpen ? (
                  <aside className="project-manager" aria-label="Project manager">
                    <header>
                      <div>
                        <span className="eyebrow">Workspace</span>
                        <h2>Projects</h2>
                      </div>
                      <button
                        className="icon-button"
                        type="button"
                        aria-label="Close project manager"
                        title="Close project manager"
                        onClick={() => setProjectManagerOpen(false)}
                      >
                        <X size={16} />
                      </button>
                    </header>
                    <ProjectSetup
                      projectName={projectName}
                      importPath={importPath}
                      isActing={model.isActing}
                      onProjectName={setProjectName}
                      onImportPath={setImportPath}
                      onSelectFolder={async () => {
                        const selected = await model.selectProjectFolder();
                        if (selected !== null) setImportPath(selected);
                      }}
                      onCreate={async () => {
                        const name = projectName.trim();
                        if (!name) return;
                        await model.createProject(name);
                        setProjectName("");
                        setProjectManagerOpen(false);
                      }}
                      onImport={async () => {
                        const path = importPath.trim();
                        const name = projectName.trim() || folderName(path);
                        if (!path || !name) return;
                        await model.importProject(name, path);
                        setProjectName("");
                        setImportPath("");
                        setProjectManagerOpen(false);
                      }}
                    />
                  </aside>
                ) : null}
              </div>
            ) : null}
            {model.mode === "project" ? (
              <ProviderSettings
                providers={model.providers}
                health={model.providerHealth}
                selectedProfileId={model.selectedProfileId}
                developerMode={model.developerMode}
                openRouterConfigured={model.openRouterStatus?.configured}
                openRouterModelId={model.openRouterStatus?.model_id}
                busy={model.isActing}
                onProfileChange={model.selectProfile}
                onDeveloperModeChange={model.setDeveloperMode}
                onConfigureOpenRouter={model.configureOpenRouter}
                onDeleteOpenRouter={model.deleteOpenRouter}
              />
            ) : null}
            <ExtensionSettings
              skills={model.skills}
              servers={model.mcpServers}
              disabled={model.state === "offline" || model.isActing}
              discoveryAvailable={model.selectedTask !== null || model.chatTurn !== null}
              onConfigure={model.configureMcpServer}
              onDiscover={model.discoverMcpServer}
              onAccept={model.acceptMcpServer}
              onEnabledChange={model.setMcpServerEnabled}
              onDelete={model.deleteMcpServer}
            />
            <KnowledgeSettings
              available={model.selectedTask !== null || model.chatTurn !== null}
              disabled={model.state === "offline" || model.isActing}
              onListDocuments={model.listDocuments}
              onSearchDocuments={model.searchDocuments}
              onDeleteDocument={model.deleteDocument}
              onSearchMemory={model.searchMemory}
              onForgetMemory={model.forgetMemory}
            />
            <ExecutionControls
              settings={model.permissionSettings}
              manifest={model.capabilities}
              disabled={model.state === "offline" || model.isActing}
              onProfileChange={model.setPermissionProfile}
              onCapabilityChange={model.setCapabilityEnabled}
            />
          </div>
        </div>

        {model.actionError || model.errorMessage || model.projectError ? (
          <RecoveryNotice model={model} />
        ) : null}

        {model.state === "loading" || model.state === "offline" ? (
          <EmptyWorkspace
            state={model.state}
            projectName={projectName}
            importPath={importPath}
            isActing={model.isActing}
            onProjectName={setProjectName}
            onImportPath={setImportPath}
            onSelectFolder={async () => undefined}
            onCreate={async () => undefined}
            onImport={async () => undefined}
            onRetry={model.retryWorkspace}
          />
        ) : model.mode === "chat" ? (
          <ChatWorkspace
            conversationAvailable={model.selectedChatConversation !== null}
            messages={model.messages}
            events={model.chatEvents}
            streamedText={model.chatStreamedText}
            pendingUserMessage={model.chatPendingUserMessage}
            turn={model.chatTurn}
            approvals={model.chatApprovals}
            providers={model.providers}
            providerHealth={model.providerHealth}
            selectedProfileId={model.selectedProfileId}
            isBusy={model.chatBusy}
            isActing={model.isActing}
            offline={false}
            developerMode={model.developerMode}
            openRouterConfigured={model.openRouterStatus?.configured ?? false}
            openRouterModelId={model.openRouterStatus?.model_id ?? null}
            error={model.chatError}
            slashCommands={model.capabilities?.slash_commands ?? []}
            onProfileChange={model.selectProfile}
            onDeveloperModeChange={model.setDeveloperMode}
            onConfigureOpenRouter={model.configureOpenRouter}
            onDeleteOpenRouter={model.deleteOpenRouter}
            onNewConversation={model.createChatConversation}
            onSwitchProject={() => model.setMode("project")}
            onPermissionChange={model.setPermissionProfile}
            onSend={model.sendChatMessage}
            onCancel={model.cancelChatTurn}
            onRetry={model.retryChatTurn}
            onRetryPending={model.retryPendingChatMessage}
            onDeletePending={model.deletePendingChatMessage}
            onTakePendingForEdit={model.takePendingChatMessageForEdit}
            onCopyMessage={model.copyMessage}
            onOpenMessageLink={model.openMessageLink}
            onDecision={model.decideApproval}
          />
        ) : model.state === "ready" ? (
          <section className="project-workspace" aria-label="Project workspace">
            <main className="workspace-main">
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
                onReview={model.reviewTask}
                onAccept={model.acceptVersion}
                onDiscard={model.discardVersion}
              />
            </main>
            <Composer
              disabled={
                model.selectedConversation === null ||
                !providerAvailable ||
                model.isActing
              }
              isBusy={model.projectBusy}
              visionAvailable={visionAvailable}
              onSubmit={model.sendProjectMessage}
              onStop={model.cancelProjectTurn}
            />
          </section>
        ) : (
          <EmptyWorkspace
            state={model.state}
            projectName={projectName}
            importPath={importPath}
            isActing={model.isActing}
            onProjectName={setProjectName}
            onImportPath={setImportPath}
            onSelectFolder={async () => {
              const selected = await model.selectProjectFolder();
              if (selected !== null) setImportPath(selected);
            }}
            onCreate={async () => {
              const name = projectName.trim();
              if (!name) return;
              try {
                await model.createProject(name);
                setProjectName("");
              } catch {
                // The model exposes the durable action error.
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
                // The model exposes the durable action error.
              }
            }}
            onRetry={model.retryWorkspace}
          />
        )}
      </div>
    </div>
  );
}

function ContextBar({ model }: { model: WorkspaceModel }) {
  const path = model.mode === "chat"
    ? model.selectedChatConversation?.title ?? "New chat"
    : [
        model.selectedProject?.name,
        model.selectedConversation?.title,
        model.selectedTask?.display_title,
      ].filter(Boolean).join(" / ") || "Projects";
  return (
    <header className="context-bar" role="banner">
      <div className="context-identity">
        <span className="context-path" title={path}>{path}</span>
      </div>
      <div className="telemetry-strip" aria-label="Workspace status">
        <span className="telemetry-item">
          <Play size={14} /> {model.selectedTask?.execution_target ?? "local"}
        </span>
        <span className="telemetry-item">
          <Cloud size={14} /> {model.selectedProject?.residency === "synced" ? "SYNCED" : "LOCAL ONLY"}
        </span>
        <span className="telemetry-item">
          <ShieldCheck size={14} /> {model.permissionProfile ?? "unavailable"}
        </span>
        <span
          className={`telemetry-item ${model.state === "offline" ? "offline" : "online"}`}
        >
          {model.state === "offline" ? <WifiOff size={14} /> : <Cloud size={14} />}
          {model.statusLabel}
        </span>
      </div>
    </header>
  );
}

interface EmptyWorkspaceProps {
  state: WorkspaceModel["state"];
  projectName: string;
  importPath: string;
  isActing: boolean;
  onProjectName(value: string): void;
  onImportPath(value: string): void;
  onSelectFolder(): Promise<void>;
  onCreate(): Promise<void>;
  onImport(): Promise<void>;
  onRetry(): Promise<void>;
}

function EmptyWorkspace({
  state,
  projectName,
  importPath,
  isActing,
  onProjectName,
  onImportPath,
  onSelectFolder,
  onCreate,
  onImport,
  onRetry,
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
        <button className="secondary-command" type="button" onClick={() => void onRetry()}>
          <RefreshCw size={15} /> Retry Core
        </button>
      </section>
    );
  }
  return (
    <section className="workspace-state workspace-create" aria-label="Create or import project">
      <div className="create-heading">
        <Boxes size={25} />
        <h1>Projects</h1>
      </div>
      <ProjectSetup
        projectName={projectName}
        importPath={importPath}
        isActing={isActing}
        onProjectName={onProjectName}
        onImportPath={onImportPath}
        onSelectFolder={onSelectFolder}
        onCreate={onCreate}
        onImport={onImport}
      />
    </section>
  );
}

function RecoveryNotice({ model }: { model: WorkspaceModel }) {
  const code = model.actionErrorCode;
  const title = code === "VERSION_CONFLICT"
    ? "Version conflict preserved"
    : code === "WORKER_INTERRUPTED"
      ? "Worker interrupted"
      : "Workspace request failed";
  const detail = code === "VERSION_CONFLICT"
    ? "The candidate version remains separate. Active Version was not overwritten."
    : model.actionError ?? model.errorMessage ?? model.projectError;
  return (
    <div className="workspace-error recovery-notice" role="alert">
      <div><strong>{title}</strong><span>{detail}</span></div>
      <button className="icon-button" type="button" aria-label="Retry workspace" title="Retry workspace" onClick={() => void model.retryWorkspace()}><RefreshCw size={15} /></button>
    </div>
  );
}

function ProjectSetup({
  projectName,
  importPath,
  isActing,
  onProjectName,
  onImportPath,
  onSelectFolder,
  onCreate,
  onImport,
}: Omit<EmptyWorkspaceProps, "state" | "onRetry">) {
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

function selectedProviderAvailable(model: WorkspaceModel): boolean {
  const provider = model.providers.find((item) => item.id === model.selectedProfileId);
  const health = model.providerHealth.find(
    (item) => item.profile_id === model.selectedProfileId,
  );
  return (
    provider !== undefined &&
    provider.enabled &&
    (!provider.credential_required || provider.credential_configured) &&
    health?.status !== "unavailable"
  );
}

function folderName(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).at(-1) ?? "Imported project";
}
