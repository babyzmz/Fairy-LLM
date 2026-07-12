import {
  Boxes,
  Cloud,
  Code2,
  FolderInput,
  FolderOpen,
  FolderPlus,
  MessageSquareText,
  Play,
  Settings,
  ShieldCheck,
  Sparkles,
  WifiOff,
  X,
} from "lucide-react";
import { useState } from "react";

import { ChatWorkspace } from "../chat/ChatWorkspace";
import { Composer } from "../chat/Composer";
import { ProviderSettings } from "../settings/ProviderSettings";
import { ExecutionControls } from "../settings/ExecutionControls";
import { ExtensionSettings } from "../settings/ExtensionSettings";
import { PreviewPanel } from "./PreviewPanel";
import { TaskTimeline } from "./TaskTimeline";
import type { WorkspaceModel, WorkspaceMode } from "./workspaceModel";
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
      <nav className="primary-rail" aria-label="Primary navigation">
        <button
          className="brand-button"
          type="button"
          aria-label="Fairy home"
          title="Fairy home"
          onClick={() => model.setMode("chat")}
        >
          <Sparkles size={21} />
        </button>
        <div className="rail-actions">
          <ModeRailButton
            mode="project"
            current={model.mode}
            label="Project workspace"
            onSelect={model.setMode}
          />
          <ModeRailButton
            mode="chat"
            current={model.mode}
            label="Conversations"
            onSelect={model.setMode}
          />
        </div>
        <div className="rail-actions rail-bottom">
          <button
            className={`rail-button ${model.developerMode ? "active" : ""}`}
            type="button"
            aria-label="Developer mode"
            aria-pressed={model.developerMode}
            title="Developer mode"
            onClick={() => model.setDeveloperMode(!model.developerMode)}
          >
            <Settings size={19} />
          </button>
        </div>
      </nav>

      <div className="workspace-body">
        <ContextBar model={model} />
        <div className="mode-bar">
          <div className="mode-tabs" role="tablist" aria-label="Workspace mode">
            <button
              type="button"
              role="tab"
              aria-selected={model.mode === "chat"}
              className={model.mode === "chat" ? "active" : ""}
              onClick={() => model.setMode("chat")}
            >
              <MessageSquareText size={14} /> Chat
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={model.mode === "project"}
              className={model.mode === "project" ? "active" : ""}
              onClick={() => model.setMode("project")}
            >
              <Boxes size={14} /> Project
            </button>
          </div>
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
          <div className="workspace-error" role="alert">
            {model.actionError ?? model.errorMessage ?? model.projectError}
          </div>
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
          />
        ) : model.mode === "chat" ? (
          <ChatWorkspace
            conversationAvailable={model.selectedChatConversation !== null}
            messages={model.messages}
            streamedText={model.chatStreamedText}
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
          />
        )}
      </div>
    </div>
  );
}

function ContextBar({ model }: { model: WorkspaceModel }) {
  return (
    <header className="context-bar" role="banner">
      <div className="context-identity">
        <span className="fairy-wordmark">FAIRY</span>
        {model.mode === "project" ? (
          <>
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
            <ConversationSelect
              label="Select conversation"
              conversations={model.conversations}
              value={model.selectedConversation?.id ?? ""}
              onChange={model.selectConversation}
            />
          </>
        ) : (
          <ConversationSelect
            label="Select chat conversation"
            conversations={model.chatConversations}
            value={model.selectedChatConversation?.id ?? ""}
            onChange={model.selectChatConversation}
          />
        )}
      </div>
      <div className="telemetry-strip" aria-label="Workspace telemetry">
        <span className="telemetry-item">
          <Code2 size={14} /> {model.mode === "project" ? versionLabel(model) : "scratch"}
        </span>
        <span className="telemetry-item">
          <Play size={14} /> {model.selectedTask?.execution_target ?? "local"}
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

function ConversationSelect({
  label,
  conversations,
  value,
  onChange,
}: {
  label: string;
  conversations: WorkspaceModel["conversations"];
  value: string;
  onChange(value: string): void;
}) {
  return (
    <label className="context-select context-conversation">
      <span className="sr-only">{label}</span>
      <select
        aria-label={label}
        value={value}
        disabled={conversations.length === 0}
        onChange={(event) => onChange(event.target.value)}
      >
        {conversations.length === 0 ? <option value="">No conversation</option> : null}
        {conversations.map((conversation, index) => (
          <option key={conversation.id} value={conversation.id}>
            Conversation {index + 1}
          </option>
        ))}
      </select>
    </label>
  );
}

function ModeRailButton({
  mode,
  current,
  label,
  onSelect,
}: {
  mode: WorkspaceMode;
  current: WorkspaceMode;
  label: string;
  onSelect(mode: WorkspaceMode): void;
}) {
  const Icon = mode === "project" ? Boxes : MessageSquareText;
  return (
    <button
      className={`rail-button ${current === mode ? "active" : ""}`}
      type="button"
      aria-label={label}
      aria-current={current === mode ? "page" : undefined}
      title={label}
      onClick={() => onSelect(mode)}
    >
      <Icon size={19} />
    </button>
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

function ProjectSetup({
  projectName,
  importPath,
  isActing,
  onProjectName,
  onImportPath,
  onSelectFolder,
  onCreate,
  onImport,
}: Omit<EmptyWorkspaceProps, "state">) {
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

function versionLabel(model: WorkspaceModel): string {
  if (model.selectedVersion === null) return "no version";
  return model.selectedVersion.visibility.replaceAll("_", " ");
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
