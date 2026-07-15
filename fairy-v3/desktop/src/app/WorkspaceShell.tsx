import { X } from "lucide-react";
import { useState } from "react";

import { ChatWorkspace } from "../chat/ChatWorkspace";
import { Composer } from "../chat/Composer";
import { HistorySidebar } from "./HistorySidebar";
import { ContextBar } from "./ContextBar";
import { WorkspaceInspector } from "./WorkspaceInspector";
import { EmptyWorkspace, folderName, ProjectSetup, RecoveryNotice } from "./ProjectWorkspaceStates";
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
  const providerAvailable = model.modelSelectionBlockReason === null;

  return (
    <div className="workspace-shell" data-workspace-state={model.state}>
      <HistorySidebar model={model} onCreateProject={() => setProjectManagerOpen(true)} />

      <div className="workspace-body">
        <ContextBar model={model} />
        {projectManagerOpen ? (
          <div className="workspace-project-manager">
            <aside className="project-manager" aria-label="Project manager">
              <header>
                <div><span className="eyebrow">Workspace</span><h2>Projects</h2></div>
                <button className="icon-button" type="button" aria-label="Close project manager" title="Close project manager" onClick={() => setProjectManagerOpen(false)}><X size={16} /></button>
              </header>
              <ProjectSetup
                projectName={projectName}
                importPath={importPath}
                isActing={model.isActing}
                onProjectName={setProjectName}
                onImportPath={setImportPath}
                onSelectFolder={async () => { const selected = await model.selectProjectFolder(); if (selected !== null) setImportPath(selected); }}
                onCreate={async () => { const name = projectName.trim(); if (!name) return; await model.createProject(name); setProjectName(""); setProjectManagerOpen(false); }}
                onImport={async () => { const path = importPath.trim(); const name = projectName.trim() || folderName(path); if (!path || !name) return; await model.importProject(name, path); setProjectName(""); setImportPath(""); setProjectManagerOpen(false); }}
              />
            </aside>
          </div>
        ) : null}

        <div className="workspace-notice-slot">
          {model.actionError || model.errorMessage || model.projectError ? (
            <RecoveryNotice model={model} />
          ) : null}
        </div>

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
          <div className="unified-workspace unified-workspace-chat">
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
              modelCatalog={model.modelCatalog}
              modelSelection={model.modelSelection}
              modelSelectionLoading={model.modelSelectionLoading}
              modelSelectionBlockReason={model.modelSelectionBlockReason}
              visionAvailable={model.visionAvailable}
              isBusy={model.chatBusy}
              isActing={model.isActing}
              offline={false}
              developerMode={model.developerMode}
              error={model.chatError}
              slashCommands={model.capabilities?.slash_commands ?? []}
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
              onSelectModel={model.selectModel}
              onOpenModelSettings={model.openSettings}
            />
            <WorkspaceInspector model={model} />
          </div>
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
              <WorkspaceInspector model={model} />
            </main>
            <Composer
              disabled={
                model.selectedConversation === null ||
                !providerAvailable ||
                model.isActing
              }
              isBusy={model.projectBusy}
              visionAvailable={model.visionAvailable}
              modelCatalog={model.modelCatalog}
              modelSelection={model.modelSelection}
              modelSelectionDisabled={model.modelSelectionLoading}
              submissionBlockedReason={model.modelSelectionBlockReason}
              onSubmit={model.sendProjectMessage}
              onStop={model.cancelProjectTurn}
              onSelectModel={model.selectModel}
              onOpenModelSettings={model.openSettings}
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
