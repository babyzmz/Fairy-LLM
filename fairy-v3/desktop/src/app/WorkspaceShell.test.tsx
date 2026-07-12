import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WorkspaceShell } from "./WorkspaceShell";
import type { Conversation } from "../core/client";
import type { WorkspaceModel } from "./workspaceModel";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

describe("WorkspaceShell", () => {
  it("keeps context, task timeline, preview, and composer visible", () => {
    render(<WorkspaceShell model={workspaceModel()} />);

    expect(screen.getByLabelText("History navigation")).toHaveTextContent("Fairy");
    expect(screen.getByRole("banner")).toHaveTextContent("Core ready");
    expect(screen.getByRole("heading", { name: "Task Timeline" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Preview" })).toBeVisible();
    expect(screen.getByLabelText("Message Fairy")).toBeVisible();
  });

  it("renders the offline state without sample project truth and retries Core", () => {
    const model = { ...workspaceModel(), state: "offline" as const, statusLabel: "Core offline" };
    render(
      <WorkspaceShell model={model} />,
    );

    expect(screen.getByRole("heading", { name: "Core offline" })).toBeVisible();
    expect(screen.queryByText("Homepage revision")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry Core" }));
    expect(model.retryWorkspace).toHaveBeenCalledOnce();
  });

  it("preserves a conflicted candidate instead of implying an overwrite", () => {
    const model = {
      ...workspaceModel(),
      actionError: "stale project revision",
      actionErrorCode: "VERSION_CONFLICT",
    };
    render(<WorkspaceShell model={model} />);

    expect(screen.getByRole("alert")).toHaveTextContent("Version conflict preserved");
    expect(screen.getByRole("alert")).toHaveTextContent("Active Version was not overwritten");
  });

  it("uses fixed expandable history groups instead of segmented mode controls", () => {
    const model = workspaceModel();
    render(<WorkspaceShell model={model} />);

    expect(screen.queryByRole("tab", { name: "Chat" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Chats" }));
    fireEvent.click(screen.getByRole("button", { name: "New chat" }));

    expect(model.createChatConversation).toHaveBeenCalledOnce();
  });

  it("keeps project creation available and fills the native folder selection", async () => {
    const model = workspaceModel();
    render(<WorkspaceShell model={model} />);

    fireEvent.click(screen.getByRole("button", { name: "New project" }));
    fireEvent.click(screen.getByRole("button", { name: "Choose project folder" }));

    await waitFor(() =>
      expect(screen.getByLabelText("Folder path")).toHaveValue("C:\\Projects\\selected"),
    );
    expect(model.selectProjectFolder).toHaveBeenCalledTimes(1);
  });

  it("uses the sidebar gear only to open the independent settings window", () => {
    const model = workspaceModel();
    render(<WorkspaceShell model={model} />);

    fireEvent.click(screen.getByRole("button", { name: "Open settings" }));
    expect(model.openSettings).toHaveBeenCalledOnce();
    expect(screen.queryByRole("button", { name: "Execution controls" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Provider settings" })).not.toBeInTheDocument();
  });

  it("opens chat actions from a right click and pins through revisioned Core actions", () => {
    const chat: Conversation = {
      id: "019f566f-f8b4-7000-8000-000000000001",
      project_id: null,
      workspace_type: "chat_scratch",
      base_version_id: null,
      active_draft_version_id: null,
      active_task_id: null,
      active_preview_id: null,
      title: "Research notes",
      pinned_at: null,
      deleted_at: null,
      revision: 3,
      created_at: "2026-07-12T00:00:00Z",
      updated_at: "2026-07-12T00:00:00Z",
    };
    const model = { ...workspaceModel(), chatConversations: [chat] };
    render(<WorkspaceShell model={model} />);

    fireEvent.contextMenu(
      screen.getByTitle("Research notes"),
    );
    expect(screen.getByRole("menuitem", { name: "Rename" })).toBeVisible();
    expect(screen.getByRole("menuitem", { name: "Move to project" })).toBeVisible();
    fireEvent.click(screen.getByRole("menuitem", { name: "Pin" }));

    expect(model.setConversationPinned).toHaveBeenCalledWith(chat, true);
  });
});

function workspaceModel(): WorkspaceModel {
  return {
    state: "ready",
    mode: "project",
    statusLabel: "Core ready",
    errorMessage: null,
    actionError: null,
    actionErrorCode: null,
    isActing: false,
    permissionProfile: "standard",
    permissionSettings: {
      profile: "standard",
      capability_overrides: {},
      revision: 0,
      updated_at: "2026-07-12T00:00:00Z",
    },
    developerMode: false,
    projects: [],
    conversations: [],
    projectConversations: [],
    chatConversations: [],
    tasks: [],
    allTasks: [],
    versions: [],
    approvals: [],
    chatApprovals: [],
    events: [],
    chatEvents: [],
    presenceEvents: [],
    messages: [],
    providers: [],
    providerHealth: [],
    openRouterStatus: { configured: false, model_id: null },
    skills: [],
    mcpServers: [],
    selectedProfileId: null,
    selectedProject: null,
    selectedConversation: null,
    selectedChatConversation: null,
    selectedTask: null,
    selectedVersion: null,
    preview: null,
    runtimeHealth: null,
    capabilities: null,
    chatTurn: null,
    chatStreamedText: "",
    chatPendingUserMessage: null,
    chatBusy: false,
    chatError: null,
    projectTurn: null,
    projectBusy: false,
    projectError: null,
    setMode: vi.fn(),
    setPermissionProfile: vi.fn(async () => undefined),
    setCapabilityEnabled: vi.fn(async () => undefined),
    configureMcpServer: vi.fn(async () => undefined),
    discoverMcpServer: vi.fn(async () => undefined),
    acceptMcpServer: vi.fn(async () => undefined),
    setMcpServerEnabled: vi.fn(async () => undefined),
    deleteMcpServer: vi.fn(async () => undefined),
    listDocuments: vi.fn(async () => []),
    searchDocuments: vi.fn(async () => []),
    deleteDocument: vi.fn(async () => undefined),
    searchMemory: vi.fn(async () => []),
    forgetMemory: vi.fn(async () => undefined),
    setDeveloperMode: vi.fn(),
    selectProfile: vi.fn(),
    configureOpenRouter: vi.fn(async () => undefined),
    deleteOpenRouter: vi.fn(async () => undefined),
    selectProject: vi.fn(),
    selectConversation: vi.fn(),
    selectChatConversation: vi.fn(),
    selectTask: vi.fn(),
    createProject: vi.fn(async () => undefined),
    importProject: vi.fn(async () => undefined),
    selectProjectFolder: vi.fn(async () => "C:\\Projects\\selected"),
    createChatConversation: vi.fn(async () => undefined),
    renameConversation: vi.fn(async () => undefined),
    setConversationPinned: vi.fn(async () => undefined),
    deleteConversation: vi.fn(async () => undefined),
    moveConversationToProject: vi.fn(async () => undefined),
    renameTask: vi.fn(async () => undefined),
    setTaskPinned: vi.fn(async () => undefined),
    archiveTask: vi.fn(async () => undefined),
    createTask: vi.fn(async () => undefined),
    sendChatMessage: vi.fn(async () => undefined),
    sendProjectMessage: vi.fn(async () => undefined),
    cancelChatTurn: vi.fn(async () => undefined),
    retryChatTurn: vi.fn(async () => undefined),
    retryPendingChatMessage: vi.fn(async () => undefined),
    deletePendingChatMessage: vi.fn(),
    takePendingChatMessageForEdit: vi.fn(() => null),
    copyMessage: vi.fn(async () => undefined),
    openMessageLink: vi.fn(async () => undefined),
    cancelProjectTurn: vi.fn(async () => undefined),
    decideApproval: vi.fn(async () => undefined),
    startPreview: vi.fn(async () => undefined),
    stopPreview: vi.fn(async () => undefined),
    reviewTask: vi.fn(async () => undefined),
    acceptVersion: vi.fn(async () => undefined),
    discardVersion: vi.fn(async () => undefined),
    retryWorkspace: vi.fn(async () => undefined),
    openSettings: vi.fn(async () => undefined),
  };
}
