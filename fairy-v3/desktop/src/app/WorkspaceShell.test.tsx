import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WorkspaceShell } from "./WorkspaceShell";
import type { Conversation, Project, Task, Version, WorkspaceFile } from "../core/client";
import type { WorkspaceModel } from "./workspaceModel";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

describe("WorkspaceShell", () => {
  it("keeps context, task timeline, workspace inspector, and composer visible", () => {
    const model = workspaceModel();
    model.selectedTask = workspaceTask();
    model.workspaceTask = model.selectedTask;
    render(<WorkspaceShell model={model} />);

    expect(screen.getByLabelText("History navigation")).toHaveTextContent("Fairy");
    expect(screen.getByRole("banner")).toHaveTextContent("Core ready");
    expect(screen.getByRole("heading", { name: "Task Timeline" })).toBeVisible();
    fireEvent.click(screen.getByRole("tab", { name: "Preview" }));
    expect(screen.getByRole("heading", { name: "Preview" })).toBeVisible();
    expect(screen.getByLabelText("Message Fairy")).toBeVisible();
  });

  it("keeps an empty workspace inspector collapsed", () => {
    render(<WorkspaceShell model={workspaceModel()} />);

    expect(screen.queryByLabelText("Workspace inspector")).not.toBeInTheDocument();
  });

  it("shows the same Files inspector in ordinary chat", async () => {
    const file: WorkspaceFile = {
      path: "src/main.ts",
      byte_length: 22,
      content_hash: "main-hash",
      kind: "source",
      language: "typescript",
    };
    const model = workspaceModel();
    model.mode = "chat";
    model.workspaceTask = workspaceTask();
    model.workspaceFiles = [file];
    const previousVersion: Version = {
      id: "019f566f-f8b4-7000-8000-000000000014",
      project_id: null,
      workspace_id: workspaceTask().workspace_id,
      source_conversation_id: workspaceTask().conversation_id,
      source_task_id: workspaceTask().id,
      parent_version_id: null,
      project_root: "C:/Fairy/versions/previous",
      visibility: "chat_draft",
      created_at: "2026-07-13T00:00:00Z",
    };
    model.versions = [previousVersion];
    model.readWorkspaceFile = vi.fn(async () => ({
      file,
      media_type: "text/plain",
      text: "console.log('Fairy');",
      stream_required: false,
    }));
    model.presentWorkspaceFile = vi.fn(async () => ({
      job: {
        id: "019f566f-f8b4-7000-8000-000000000031",
        workspace_id: workspaceTask().workspace_id,
        version_id: workspaceTask().target_version_id!,
        file_set_id: "019f566f-f8b4-7000-8000-000000000032",
        source_path: file.path,
        source_hash: file.content_hash.padEnd(64, "0").slice(0, 64),
        cache_key: "a".repeat(64),
        requested_mode: "auto",
        renderer_pack_id: null,
        renderer_pack_version: null,
        status: "ready",
        progress: 100,
        error_code: null,
        public_summary: "Ready",
        created_at: "2026-07-14T00:00:00Z",
        updated_at: "2026-07-14T00:00:00Z",
      },
      presentation: {
        id: "019f566f-f8b4-7000-8000-000000000033",
        workspace_id: workspaceTask().workspace_id,
        version_id: workspaceTask().target_version_id!,
        file_set_id: "019f566f-f8b4-7000-8000-000000000032",
        source_path: file.path,
        source_hash: file.content_hash.padEnd(64, "0").slice(0, 64),
        renderer: "browser-native",
        fidelity: "native",
        status: "ready",
        capabilities: ["search", "select", "copy"],
        assets: [],
        created_at: "2026-07-14T00:00:00Z",
      },
    }));
    model.compareWorkspaceFile = vi.fn(async () => ({
      workspace_id: workspaceTask().workspace_id,
      left_version_id: previousVersion.id,
      right_version_id: workspaceTask().target_version_id!,
      items: [{
        path: file.path,
        status: "modified",
        left_hash: "a".repeat(64),
        right_hash: "b".repeat(64),
        left_byte_length: 18,
        right_byte_length: 22,
        text_diff: "-console.log('Old');\n+console.log('Fairy');",
        diff_truncated: false,
      }],
      truncated: false,
    }));

    render(<WorkspaceShell model={model} />);
    fireEvent.click(screen.getByRole("tab", { name: /Files/ }));
    fireEvent.click(screen.getByRole("button", { name: "main.ts" }));

    await waitFor(() => expect(screen.getByText("console.log('Fairy');")).toBeVisible());
    expect(screen.queryByText("Use this version")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Compare selected file" }));
    await waitFor(() => expect(screen.getByText(/console\.log\('Old'\)/)).toBeVisible());
    expect(model.compareWorkspaceFile).toHaveBeenCalledWith(
      previousVersion.id,
      workspaceTask().target_version_id,
      file.path,
    );

    fireEvent.click(screen.getByRole("button", { name: "Rename file" }));
    fireEvent.change(screen.getByLabelText("Workspace path"), {
      target: { value: "src/bootstrap.ts" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Rename" }));
    await waitFor(() => expect(model.renameWorkspaceFile).toHaveBeenCalledWith(file, "src/bootstrap.ts"));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Delete file" }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("next Workspace Version");
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(model.deleteWorkspaceFile).toHaveBeenCalledWith(file));
  });

  it("renders the offline state without sample project truth and retries Core", () => {
    const model = { ...workspaceModel(), state: "offline" as const, statusLabel: "Core offline" };
    render(<WorkspaceShell model={model} />);

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

    await waitFor(() => expect(screen.getByLabelText("Folder path")).toHaveValue("C:\\Projects\\selected"));
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
      workspace_id: "019f566f-f8b4-7000-8000-000000000001",
      workspace_type: "chat_scratch",
      base_version_id: null,
      active_draft_version_id: null,
      active_task_id: null,
      active_preview_id: null,
      title: "Research notes",
      pinned_at: null,
      deleted_at: null,
      deleted_by_project_at: null,
      purged_at: null,
      revision: 3,
      created_at: "2026-07-12T00:00:00Z",
      updated_at: "2026-07-12T00:00:00Z",
    };
    const model = { ...workspaceModel(), chatConversations: [chat] };
    render(<WorkspaceShell model={model} />);

    fireEvent.contextMenu(screen.getByTitle("Research notes"));
    expect(screen.getByRole("menuitem", { name: "Rename" })).toBeVisible();
    expect(screen.getByRole("menuitem", { name: "Move to project" })).toBeVisible();
    fireEvent.click(screen.getByRole("menuitem", { name: "Pin" }));

    expect(model.setConversationPinned).toHaveBeenCalledWith(chat, true);
  });

  it("uses an in-app confirmation for destructive chat actions", async () => {
    const chat: Conversation = {
      id: "019f566f-f8b4-7000-8000-000000000001",
      project_id: null,
      workspace_id: "019f566f-f8b4-7000-8000-000000000001",
      workspace_type: "chat_scratch",
      base_version_id: null,
      active_draft_version_id: null,
      active_task_id: null,
      active_preview_id: null,
      title: "Research notes",
      pinned_at: null,
      deleted_at: null,
      deleted_by_project_at: null,
      purged_at: null,
      revision: 3,
      created_at: "2026-07-12T00:00:00Z",
      updated_at: "2026-07-12T00:00:00Z",
    };
    const model = { ...workspaceModel(), chatConversations: [chat] };
    render(<WorkspaceShell model={model} />);

    fireEvent.contextMenu(screen.getByTitle("Research notes"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("synchronization tombstone");
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(model.deleteConversation).toHaveBeenCalledWith(chat));
  });

  it("renders projects as folders with chats only and shares project menu actions", async () => {
    const project: Project = {
      id: "019f566f-f8b4-7000-8000-000000000020",
      name: "Launch site",
      residency: "local_only",
      workspace_id: "019f566f-f8b4-7000-8000-000000000020",
      active_version_id: null,
      active_preview_id: null,
      revision: 0,
      pinned_at: null,
      archived_at: null,
      deleted_at: null,
      purged_at: null,
      metadata_revision: 2,
      created_at: "2026-07-12T00:00:00Z",
      updated_at: "2026-07-12T00:00:00Z",
    };
    const thread: Conversation = {
      id: "019f566f-f8b4-7000-8000-000000000021",
      project_id: project.id,
      workspace_id: project.workspace_id,
      workspace_type: "project_chat",
      base_version_id: null,
      active_draft_version_id: null,
      active_task_id: null,
      active_preview_id: null,
      title: "Homepage direction",
      pinned_at: null,
      deleted_at: null,
      deleted_by_project_at: null,
      purged_at: null,
      revision: 1,
      created_at: "2026-07-12T00:00:00Z",
      updated_at: "2026-07-12T00:00:00Z",
    };
    const model = {
      ...workspaceModel(),
      projects: [project],
      projectConversations: [thread],
      allTasks: [{ ...workspaceTask(), display_title: "Internal Task must stay hidden" }],
    };
    render(<WorkspaceShell model={model} />);

    fireEvent.click(screen.getByRole("button", { name: "Expand Launch site" }));
    expect(screen.getByTitle("Homepage direction")).toBeInTheDocument();
    expect(screen.queryByText("Internal Task must stay hidden")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "New chat in Launch site" }));
    expect(model.createProjectConversation).toHaveBeenCalledWith(project);

    fireEvent.contextMenu(screen.getByTitle("Launch site"));
    expect(screen.getByRole("menuitem", { name: "New chat" })).toBeVisible();
    expect(screen.getByRole("menuitem", { name: "Archive" })).toBeVisible();
    fireEvent.click(screen.getByRole("menuitem", { name: "Archive" }));
    expect(screen.getByRole("dialog", { name: "Archive project" })).toHaveTextContent("1 chat");
    fireEvent.click(screen.getByRole("button", { name: "Archive" }));

    await waitFor(() => expect(model.archiveProject).toHaveBeenCalledWith(project));
  });

  it("allows the selected project folder to collapse independently of selection", () => {
    const project = projectFixture();
    const thread = projectConversationFixture(project);
    const model = {
      ...workspaceModel(),
      projects: [project],
      projectConversations: [thread],
      selectedProject: project,
      selectedConversation: thread,
    };
    render(<WorkspaceShell model={model} />);

    fireEvent.click(screen.getByRole("button", { name: `Expand ${project.name}` }));
    expect(screen.getByTitle(thread.title)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: `Collapse ${project.name}` }));

    expect(screen.queryByTitle(thread.title)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: `Expand ${project.name}` }))
      .toHaveAttribute("aria-expanded", "false");
  });

  it("opens a project overview with its chats and new chat action", () => {
    const project = projectFixture();
    const thread = projectConversationFixture(project);
    const model = {
      ...workspaceModel(),
      projects: [project],
      selectedProject: project,
      selectedConversation: null,
      projectConversations: [thread],
    };
    render(<WorkspaceShell model={model} />);

    expect(screen.getByRole("heading", { name: project.name })).toBeVisible();
    const overview = screen.getByRole("region", { name: project.name });
    fireEvent.click(within(overview).getByRole("button", { name: "New chat" }));
    expect(model.createProjectConversation).toHaveBeenCalledWith(project);
    fireEvent.click(within(screen.getByLabelText("Project chats")).getByRole("button"));
    expect(model.selectConversation).toHaveBeenCalledWith(thread.id);
  });

  it("opens project knowledge from the top-level Obsidian inspector tab", () => {
    const project = projectFixture();
    const thread = projectConversationFixture(project);
    const model = {
      ...workspaceModel(),
      projects: [project],
      selectedProject: project,
      selectedConversation: thread,
      projectConversations: [thread],
      workspaceTask: workspaceTask(),
      workspaceFiles: [{
        path: "docs/architecture.md",
        byte_length: 2048,
        content_hash: "a".repeat(64),
        kind: "text",
        language: "markdown",
      }],
    };
    render(<WorkspaceShell model={model} />);

    fireEvent.click(screen.getByRole("tab", { name: "Obsidian" }));
    const knowledge = screen.getByLabelText("Obsidian project knowledge");
    expect(knowledge).toHaveTextContent(project.name);
    expect(knowledge).toHaveTextContent("Fairy index ready");
    fireEvent.click(within(knowledge).getByRole("button", { name: "Graph" }));
    expect(within(knowledge).getByLabelText(/Project graph with/)).toBeVisible();
    fireEvent.click(within(knowledge).getByRole("button", { name: "Sync" }));
    expect(within(knowledge).getByText("Official Obsidian Vault")).toBeVisible();
  });

  it("reads an indexed Vault note inside the Obsidian tab", async () => {
    const project = projectFixture();
    const thread = projectConversationFixture(project);
    const model = {
      ...workspaceModel(),
      projects: [project],
      selectedProject: project,
      selectedConversation: thread,
      projectConversations: [thread],
      workspaceTask: workspaceTask(),
      obsidianItems: [{
        source_id: "019f566f-f8b4-7000-8000-000000000141",
        relative_path: "Notes/Architecture.md",
        title: "Architecture",
        kind: "markdown",
        content_hash: "b".repeat(64),
        byte_length: 96,
        links: ["Project Plan"],
        modified_at: "2026-07-12T00:00:00Z",
      }],
    };
    render(<WorkspaceShell model={model} />);

    fireEvent.click(screen.getByRole("tab", { name: "Obsidian" }));
    const knowledge = screen.getByLabelText("Obsidian project knowledge");
    fireEvent.click(within(knowledge).getByRole("button", { name: "Notes" }));
    fireEvent.click(within(knowledge).getByRole("button", { name: /Architecture/ }));

    expect(await within(knowledge).findByRole("heading", { name: "Test note" })).toBeVisible();
    expect(model.readObsidianItem).toHaveBeenCalledWith(model.obsidianItems[0]);
  });

  it("presents PROJECT_BUSY without exposing an internal error code", () => {
    const model = {
      ...workspaceModel(),
      actionError: "Project has an active Preview",
      actionErrorCode: "PROJECT_BUSY",
    };
    render(<WorkspaceShell model={model} />);

    expect(screen.getByRole("alert")).toHaveTextContent("Project is busy");
    expect(screen.getByRole("alert")).toHaveTextContent("Finish or cancel the active Turn");
    expect(screen.getByRole("alert")).not.toHaveTextContent("PROJECT_BUSY");
  });
});

function projectFixture(): Project {
  return {
    id: "019f566f-f8b4-7000-8000-000000000120",
    name: "Project overview",
    residency: "local_only",
    workspace_id: "019f566f-f8b4-7000-8000-000000000120",
    active_version_id: null,
    active_preview_id: null,
    revision: 0,
    pinned_at: null,
    archived_at: null,
    deleted_at: null,
    purged_at: null,
    metadata_revision: 0,
    created_at: "2026-07-12T00:00:00Z",
    updated_at: "2026-07-12T00:00:00Z",
  };
}

function projectConversationFixture(project: Project): Conversation {
  return {
    id: "019f566f-f8b4-7000-8000-000000000121",
    project_id: project.id,
    workspace_id: project.workspace_id,
    workspace_type: "project_chat",
    base_version_id: null,
    active_draft_version_id: null,
    active_task_id: null,
    active_preview_id: null,
    title: "Implementation thread",
    pinned_at: null,
    deleted_at: null,
    deleted_by_project_at: null,
    purged_at: null,
    revision: 0,
    created_at: "2026-07-12T00:00:00Z",
    updated_at: "2026-07-12T00:00:00Z",
  };
}

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
    openRouterStatus: { configured: false, account_id: null },
    skills: [],
    mcpServers: [],
    selectedProfileId: null,
    modelCatalog: null,
    modelSelection: null,
    modelSelectionLoading: false,
    modelSelectionRefreshing: false,
    modelSelectionBlockReason: "Connect OpenRouter in Model settings before sending.",
    visionAvailable: false,
    selectedProject: null,
    selectedConversation: null,
    selectedChatConversation: null,
    selectedTask: null,
    workspaceTask: null,
    selectedVersion: null,
    preview: null,
    runtimeHealth: null,
    workspaceFiles: [],
    workspaceGeneration: 0,
    knowledgeOverview: null,
    knowledgeItems: [],
    knowledgeGraph: null,
    knowledgeLoading: false,
    obsidianHealth: null,
    obsidianSources: [],
    obsidianItems: [],
    obsidianLoading: false,
    mediaJobs: [],
    assetSets: [],
    workspaceFilesLoading: false,
    mediaJobsLoading: false,
    capabilities: null,
    chatTurn: null,
    turnTraces: {},
    turnTraceStates: {},
    chatStreamedText: "",
    chatPendingUserMessage: null,
    chatBusy: false,
    chatError: null,
    projectTurn: null,
    projectTrace: null,
    projectTraceState: null,
    projectBusy: false,
    projectError: null,
    petTaskId: null,
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
    selectModel: vi.fn(async () => undefined),
    refreshModelCatalog: vi.fn(async () => undefined),
    configureOpenRouter: vi.fn(async () => undefined),
    deleteOpenRouter: vi.fn(async () => undefined),
    selectProject: vi.fn(),
    selectConversation: vi.fn(),
    selectChatConversation: vi.fn(),
    selectTask: vi.fn(),
    createProject: vi.fn(async () => undefined),
    importProject: vi.fn(async () => undefined),
    selectProjectFolder: vi.fn(async () => "C:\\Projects\\selected"),
    connectObsidianVault: vi.fn(async () => undefined),
    syncObsidianSource: vi.fn(async () => undefined),
    readObsidianItem: vi.fn(async (item) => ({
      source_id: item.source_id,
      relative_path: item.relative_path,
      title: item.title,
      kind: item.kind,
      content_hash: item.content_hash,
      content: "# Test note",
    })),
    createChatConversation: vi.fn(async () => undefined),
    createPetChatConversation: vi.fn(async () => undefined),
    createProjectConversation: vi.fn(async () => undefined),
    renameProject: vi.fn(async () => undefined),
    setProjectPinned: vi.fn(async () => undefined),
    archiveProject: vi.fn(async () => undefined),
    deleteProject: vi.fn(async () => undefined),
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
    sendPetMessage: vi.fn(async () => undefined),
    cancelPetTurn: vi.fn(async () => undefined),
    cancelChatTurn: vi.fn(async () => undefined),
    retryChatTurn: vi.fn(async () => undefined),
    retryPendingChatMessage: vi.fn(async () => undefined),
    deletePendingChatMessage: vi.fn(),
    takePendingChatMessageForEdit: vi.fn(() => null),
    copyMessage: vi.fn(async () => undefined),
    openMessageLink: vi.fn(async () => undefined),
    readWorkspaceFile: vi.fn(async () => {
      throw new Error("not used");
    }),
    openWorkspaceFileStream: vi.fn(async () => {
      throw new Error("not used");
    }),
    presentWorkspaceFile: vi.fn(async () => {
      throw new Error("not used");
    }),
    compareWorkspaceFile: vi.fn(async () => {
      throw new Error("not used");
    }),
    resolveWorkspaceFileSet: vi.fn(async (path: string) => ({
      id: "019f566f-f8b4-7000-8000-000000000041",
      workspace_id: workspaceTask().workspace_id,
      version_id: workspaceTask().target_version_id as string,
      kind: "single",
      primary_path: path,
      parser_version: "1.0.0",
      manifest_hash: "a".repeat(64),
      members: [],
      missing_dependencies: [],
      blocked_dependencies: [],
    })),
    listFileAnnotations: vi.fn(async () => ({ document: null })),
    updateFileAnnotations: vi.fn(async () => {
      throw new Error("not used");
    }),
    createTextSelection: vi.fn(async () => {
      throw new Error("not used");
    }),
    createSceneSelection: vi.fn(async () => {
      throw new Error("not used");
    }),
    revealWorkspaceFile: vi.fn(async () => undefined),
    refreshWorkspaceFiles: vi.fn(async () => undefined),
    uploadWorkspaceFiles: vi.fn(async () => undefined),
    renameWorkspaceFile: vi.fn(async () => undefined),
    deleteWorkspaceFile: vi.fn(async () => undefined),
    exportWorkspace: vi.fn(async () => {
      throw new Error("not used");
    }),
    cancelMediaJob: vi.fn(async () => undefined),
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

function workspaceTask(): Task {
  return {
    id: "019f566f-f8b4-7000-8000-000000000010",
    project_id: null,
    workspace_id: "019f566f-f8b4-7000-8000-000000000011",
    conversation_id: "019f566f-f8b4-7000-8000-000000000012",
    user_request: "Build a workspace",
    operation_mode: "continue_current_chat_draft",
    base_version_id: null,
    execution_target: "local",
    target_version_id: "019f566f-f8b4-7000-8000-000000000013",
    memory_snapshot_id: null,
    memory_snapshot_hash: null,
    status: "executing",
    display_title: "Build a workspace",
    pinned_at: null,
    metadata_revision: 0,
    created_at: "2026-07-13T00:00:00Z",
    updated_at: "2026-07-13T00:00:00Z",
  };
}
