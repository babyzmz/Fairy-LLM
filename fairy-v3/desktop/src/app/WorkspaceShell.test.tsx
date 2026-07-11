import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WorkspaceShell } from "./WorkspaceShell";
import type { WorkspaceModel } from "./workspaceModel";

afterEach(cleanup);

describe("WorkspaceShell", () => {
  it("keeps context, task timeline, preview, and composer visible", () => {
    render(<WorkspaceShell model={workspaceModel()} />);

    expect(screen.getByRole("banner")).toHaveTextContent("FAIRY");
    expect(screen.getByRole("banner")).toHaveTextContent("Core ready");
    expect(screen.getByRole("heading", { name: "Task Timeline" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Preview" })).toBeVisible();
    expect(screen.getByLabelText("Message Fairy")).toBeVisible();
  });

  it("renders the offline state without sample project truth", () => {
    render(
      <WorkspaceShell
        model={{ ...workspaceModel(), state: "offline", statusLabel: "Core offline" }}
      />,
    );

    expect(screen.getByRole("heading", { name: "Core offline" })).toBeVisible();
    expect(screen.queryByText("Homepage revision")).not.toBeInTheDocument();
  });

  it("routes the segmented mode controls through the workspace model", () => {
    const model = workspaceModel();
    render(<WorkspaceShell model={model} />);

    fireEvent.click(screen.getByRole("tab", { name: "Chat" }));
    fireEvent.click(screen.getByRole("button", { name: "Conversations" }));

    expect(model.setMode).toHaveBeenNthCalledWith(1, "chat");
    expect(model.setMode).toHaveBeenNthCalledWith(2, "chat");
  });
});

function workspaceModel(): WorkspaceModel {
  return {
    state: "ready",
    mode: "project",
    statusLabel: "Core ready",
    errorMessage: null,
    actionError: null,
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
    chatConversations: [],
    tasks: [],
    versions: [],
    approvals: [],
    chatApprovals: [],
    events: [],
    presenceEvents: [],
    messages: [],
    providers: [],
    providerHealth: [],
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
    setDeveloperMode: vi.fn(),
    selectProfile: vi.fn(),
    selectProject: vi.fn(),
    selectConversation: vi.fn(),
    selectChatConversation: vi.fn(),
    selectTask: vi.fn(),
    createProject: vi.fn(async () => undefined),
    importProject: vi.fn(async () => undefined),
    createChatConversation: vi.fn(async () => undefined),
    createTask: vi.fn(async () => undefined),
    sendChatMessage: vi.fn(async () => undefined),
    sendProjectMessage: vi.fn(async () => undefined),
    cancelChatTurn: vi.fn(async () => undefined),
    retryChatTurn: vi.fn(async () => undefined),
    cancelProjectTurn: vi.fn(async () => undefined),
    decideApproval: vi.fn(async () => undefined),
    startPreview: vi.fn(async () => undefined),
    stopPreview: vi.fn(async () => undefined),
    reviewTask: vi.fn(async () => undefined),
    acceptVersion: vi.fn(async () => undefined),
    discardVersion: vi.fn(async () => undefined),
  };
}
