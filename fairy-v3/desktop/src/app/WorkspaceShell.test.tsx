import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WorkspaceShell } from "./WorkspaceShell";
import type { Conversation, ObsidianSource, ObsidianVaultItemContent, Project, Task, Version, WorkspaceFile } from "../core/client";
import type { WorkspaceModel } from "./workspaceModel";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

describe("WorkspaceShell", () => {
  it("prefetches only the intended Conversation from history hover and focus", () => {
    const project = projectFixture();
    const chat = {
      ...projectConversationFixture(project),
      id: "019f566f-f8b4-7000-8000-000000000122",
      project_id: null,
      workspace_type: "chat_scratch" as const,
      title: "Warm chat",
    };
    const model = workspaceModel();
    model.mode = "chat";
    model.chatConversations = [chat];
    model.selectedChatConversation = chat;
    model.prefetchConversation = vi.fn(async () => undefined);
    render(<WorkspaceShell model={model} />);

    const row = screen.getByRole("button", { name: "Warm chat" });
    fireEvent.mouseEnter(row);
    fireEvent.focus(row);

    expect(model.prefetchConversation).toHaveBeenCalledTimes(2);
    expect(model.prefetchConversation).toHaveBeenNthCalledWith(1, chat);
    expect(model.prefetchConversation).toHaveBeenNthCalledWith(2, chat);
  });

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

  it("opens an active Preview before resolution and preserves a manual tab choice", () => {
    const project = {
      ...projectFixture(),
      active_preview_id: "019f566f-f8b4-7000-8000-000000000131",
    };
    const conversation = projectConversationFixture(project);
    const model = workspaceModel();
    model.projects = [project];
    model.selectedProject = project;
    model.selectedConversation = conversation;
    model.selectedTask = workspaceTask();
    model.workspaceTask = model.selectedTask;
    model.workspaceActivePreviewId = project.active_preview_id;
    model.workspaceFiles = [{
      path: "src/main.ts",
      byte_length: 22,
      content_hash: "main-hash",
      kind: "source",
      language: "typescript",
      imports: [],
    }];
    const { rerender } = render(<WorkspaceShell model={model} />);

    expect(screen.getByRole("tab", { name: "Preview" })).toHaveAttribute("aria-selected", "true");
    fireEvent.click(screen.getByRole("tab", { name: /Files/ }));
    rerender(<WorkspaceShell model={{ ...model, previewActivationLoading: true }} />);

    expect(screen.getByRole("tab", { name: /Files/ })).toHaveAttribute("aria-selected", "true");
  });

  it("resizes the workspace inspector with the keyboard and persists the width", () => {
    const model = workspaceModel();
    model.selectedTask = workspaceTask();
    model.workspaceTask = model.selectedTask;
    const { container } = render(<WorkspaceShell model={model} />);
    const shell = container.querySelector<HTMLElement>(".workspace-main")!;

    const separator = screen.getByRole("separator", { name: "Resize workspace inspector" });
    expect(separator).toHaveAttribute("aria-valuemin", "360");
    const maximum = Number(separator.getAttribute("aria-valuemax"));
    expect(maximum).toBeGreaterThanOrEqual(360);
    expect(maximum).toBeLessThanOrEqual(Math.round(window.innerWidth * 0.75));

    fireEvent.keyDown(separator, {
      key: "Home",
    });

    expect(shell.style.getPropertyValue("--inspector-width")).toBe("360px");
    expect(window.localStorage.getItem("fairy.workspace.inspector-width")).toBe("360");
    expect(separator).toHaveAttribute("aria-valuenow", "360");

    fireEvent.keyDown(separator, { key: "End" });
    expect(shell.style.getPropertyValue("--inspector-width")).toBe(`${maximum}px`);
    expect(separator).toHaveAttribute("aria-valuenow", String(maximum));

    fireEvent.keyDown(separator, { key: "ArrowRight" });
    expect(separator).toHaveAttribute("aria-valuenow", String(maximum - 24));
  });

  it("links Inspector tabs to panels and supports roving keyboard focus", () => {
    const model = workspaceModel();
    model.selectedTask = workspaceTask();
    model.workspaceTask = model.selectedTask;
    render(<WorkspaceShell model={model} />);

    const preview = screen.getByRole("tab", { name: "Preview" });
    const files = screen.getByRole("tab", { name: /Files/ });
    const obsidian = screen.getByRole("tab", { name: "Obsidian" });
    expect(preview).toHaveAttribute("tabindex", "0");
    expect(files).toHaveAttribute("tabindex", "-1");
    expect(obsidian).toHaveAttribute("tabindex", "-1");
    expect(preview).toHaveAttribute("aria-controls");
    expect(screen.getByRole("tabpanel")).toHaveAttribute(
      "aria-labelledby",
      preview.id,
    );

    preview.focus();
    fireEvent.keyDown(preview, { key: "ArrowRight" });
    expect(files).toHaveFocus();
    expect(files).toHaveAttribute("aria-selected", "true");
    expect(files).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("tabpanel")).toHaveAttribute(
      "aria-labelledby",
      files.id,
    );

    fireEvent.keyDown(files, { key: "End" });
    expect(obsidian).toHaveFocus();
    fireEvent.keyDown(obsidian, { key: "ArrowRight" });
    expect(preview).toHaveFocus();
    expect(preview).toHaveAttribute("aria-selected", "true");
  });

  it("keeps the chat Inspector mounted and restores its active tab and focus", () => {
    const project = projectFixture();
    const chat = {
      ...projectConversationFixture(project),
      project_id: null,
      workspace_type: "chat_scratch" as const,
      title: "Collapsible chat",
    };
    const task = { ...workspaceTask(), conversation_id: chat.id, project_id: null };
    const model = workspaceModel();
    model.mode = "chat";
    model.selectedChatConversation = chat;
    model.workspaceTask = task;
    model.workspaceFiles = [workspaceFile("notes/fairy.md")];
    const { container } = render(<WorkspaceShell model={model} />);

    fireEvent.click(screen.getByRole("tab", { name: /Files/ }));
    const activePanel = screen.getByRole("tabpanel");
    activePanel.scrollTop = 47;
    fireEvent.click(screen.getByRole("button", { name: "Collapse workspace inspector" }));

    const inspector = screen.getByLabelText("Workspace inspector");
    expect(inspector).toHaveAttribute("hidden");
    expect(inspector).toHaveAttribute("inert");
    expect(activePanel).toBeInTheDocument();
    expect(activePanel.scrollTop).toBe(47);
    expect(container.querySelector(".unified-workspace-chat")).toHaveAttribute(
      "data-inspector-collapsed",
      "true",
    );
    expect(window.localStorage.getItem("fairy.workspace.chat-inspector-collapsed")).toBe("true");
    const restore = screen.getByRole("button", { name: "Restore workspace inspector" });
    expect(restore).toHaveFocus();

    fireEvent.keyDown(restore, { key: "Enter" });
    fireEvent.click(restore);

    expect(inspector).not.toHaveAttribute("hidden");
    expect(screen.getByRole("tab", { name: /Files/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("button", { name: "Collapse workspace inspector" })).toHaveFocus();
    expect(activePanel.scrollTop).toBe(47);
    expect(window.localStorage.getItem("fairy.workspace.chat-inspector-collapsed")).toBe("false");
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
      imports: [],
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

  it("uses the sidebar gear only to open the internal settings view", () => {
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
    const source = obsidianSourceFixture(project.id);
    const model = {
      ...workspaceModel(),
      projects: [project],
      selectedProject: project,
      selectedConversation: thread,
      projectConversations: [thread],
      workspaceTask: workspaceTask(),
      obsidianSources: [source],
      obsidianSourceProjections: [{
        sourceId: source.id,
        sourceRevision: source.revision,
        itemCount: source.item_count,
        loading: false,
        error: null,
      }],
      workspaceFiles: [{
        path: "docs/architecture.md",
        byte_length: 2048,
        content_hash: "a".repeat(64),
        kind: "text",
        language: "markdown",
        imports: [],
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

  it("builds an isolated Conversation Workspace graph for an ordinary chat", () => {
    const project = projectFixture();
    const chat = {
      ...projectConversationFixture(project),
      project_id: null,
      workspace_type: "chat_scratch" as const,
      title: "Three-file website",
    };
    const task = { ...workspaceTask(), conversation_id: chat.id };
    const model = {
      ...workspaceModel(),
      mode: "chat" as const,
      selectedProject: project,
      selectedChatConversation: chat,
      workspaceTask: task,
      allTasks: [task],
      workspaceFiles: [
        workspaceFile("index.html", ["styles.css", "main.js"]),
        workspaceFile("styles.css"),
        workspaceFile("main.js"),
      ],
      knowledgeGraph: {
        project_id: project.id,
        source_version_id: null,
        source_revision: 1,
        watermark: "a".repeat(64),
        nodes: [{
          id: `project:${project.id}`,
          project_id: project.id,
          kind: "project" as const,
          title: project.name,
          relative_path: null,
          content_hash: null,
          byte_length: null,
          language: null,
          revision: 1,
          conversation_id: null,
          task_id: null,
          source_id: null,
          revision_id: null,
        }, {
          id: "file:core.db",
          project_id: project.id,
          kind: "file" as const,
          title: "core.db",
          relative_path: "core.db",
          content_hash: "b".repeat(64),
          byte_length: 4096,
          language: null,
          revision: 1,
          conversation_id: null,
          task_id: null,
          source_id: null,
          revision_id: null,
        }],
        edges: [],
      },
    };
    render(<WorkspaceShell model={model} />);

    fireEvent.click(screen.getByRole("tab", { name: "Obsidian" }));
    const knowledge = screen.getByLabelText("Obsidian conversation knowledge");
    expect(within(knowledge).getByRole("heading", { name: chat.title })).toBeVisible();
    expect(knowledge).toHaveTextContent("3 current Workspace files");
    expect(within(knowledge).queryByText("core.db")).not.toBeInTheDocument();
    expect(knowledge).not.toHaveTextContent(project.name);
    fireEvent.click(within(knowledge).getByRole("button", { name: "Graph" }));
    expect(within(knowledge).getByLabelText("Conversation graph with 6 nodes and 7 links")).toBeVisible();
    fireEvent.click(within(knowledge).getByRole("button", { name: "Links" }));
    expect(within(knowledge).getAllByText("index.html").length).toBeGreaterThan(0);
    expect(within(knowledge).getAllByText("styles.css")).toHaveLength(2);
    expect(within(knowledge).getAllByText("main.js")).toHaveLength(2);
    fireEvent.click(within(knowledge).getByRole("button", { name: "Sync" }));
    expect(within(knowledge).getByText("Vault connections are project-scoped")).toBeVisible();
  });

  it("isolates a selected Vault source from workspace files in Graph", () => {
    const project = projectFixture();
    const thread = projectConversationFixture(project);
    const source = obsidianSourceFixture(project.id);
    const graph = {
      project_id: project.id,
      source_version_id: null,
      source_revision: 2,
      watermark: "c".repeat(64),
      nodes: [
        graphNode(`project:${project.id}`, project.id, "project", project.name, null),
        graphNode("file:core.db", project.id, "file", "core.db", null),
        graphNode(`knowledge-source:${source.id}`, project.id, "obsidian", source.display_name, source.id),
        graphNode("knowledge-revision:note", project.id, "note", "Architecture", source.id),
      ],
      edges: [
        { id: "edge-workspace", source_id: `project:${project.id}`, target_id: "file:core.db", relation: "contains" as const },
        { id: "edge-source", source_id: `project:${project.id}`, target_id: `knowledge-source:${source.id}`, relation: "contains" as const },
        { id: "edge-note", source_id: `knowledge-source:${source.id}`, target_id: "knowledge-revision:note", relation: "contains" as const },
      ],
    } satisfies NonNullable<WorkspaceModel["knowledgeGraph"]>;
    const model = {
      ...workspaceModel(),
      selectedProject: project,
      selectedConversation: thread,
      projectConversations: [thread],
      workspaceTask: workspaceTask(),
      knowledgeGraph: graph,
      obsidianSources: [source],
    };
    render(<WorkspaceShell model={model} />);

    fireEvent.click(screen.getByRole("tab", { name: "Obsidian" }));
    const knowledge = screen.getByLabelText("Obsidian project knowledge");
    fireEvent.click(within(knowledge).getByRole("button", { name: "Graph" }));
    expect(within(knowledge).getByLabelText("Project graph with 4 nodes and 3 links")).toBeVisible();
    fireEvent.change(within(knowledge).getByRole("combobox", { name: "Filter knowledge source" }), {
      target: { value: source.id },
    });
    expect(within(knowledge).getByLabelText("Project graph with 3 nodes and 2 links")).toBeVisible();
  });

  it("reads an indexed Vault note inside the Obsidian tab", async () => {
    const project = projectFixture();
    const thread = projectConversationFixture(project);
    const source = obsidianSourceFixture(project.id);
    const model = {
      ...workspaceModel(),
      projects: [project],
      selectedProject: project,
      selectedConversation: thread,
      projectConversations: [thread],
      workspaceTask: workspaceTask(),
      obsidianSources: [source],
      obsidianSourceProjections: [{
        sourceId: source.id,
        sourceRevision: source.revision,
        itemCount: 1,
        loading: false,
        error: null,
      }],
      obsidianItems: [{
        source_id: source.id,
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
    expect(model.readObsidianItem).toHaveBeenCalledWith(model.obsidianItems[0], expect.any(AbortSignal));
  });

  it("filters multiple Vault sources and discards a late note after the project changes", async () => {
    const firstProject = projectFixture();
    const secondProject = {
      ...projectFixture(),
      id: "019f566f-f8b4-7000-8000-000000000151",
      workspace_id: "019f566f-f8b4-7000-8000-000000000152",
      name: "Second project",
    };
    const firstSource = obsidianSourceFixture(firstProject.id);
    const secondSource = {
      ...obsidianSourceFixture(firstProject.id),
      id: "019f566f-f8b4-7000-8000-000000000153",
      display_name: "Research Vault",
    };
    const firstItem = {
      source_id: firstSource.id,
      relative_path: "Notes/Architecture.md",
      title: "Architecture",
      kind: "markdown",
      content_hash: "d".repeat(64),
      byte_length: 96,
      links: [],
      modified_at: "2026-07-12T00:00:00Z",
    };
    const secondItem = {
      ...firstItem,
      source_id: secondSource.id,
      relative_path: "Research/Evidence.md",
      title: "Evidence",
      content_hash: "e".repeat(64),
    };
    let resolveLate!: (value: ObsidianVaultItemContent) => void;
    const readObsidianItem = vi.fn((_item: typeof firstItem, _signal?: AbortSignal) => new Promise<ObsidianVaultItemContent>((resolve) => {
      resolveLate = resolve;
    }));
    const firstConversation = projectConversationFixture(firstProject);
    const model = {
      ...workspaceModel(),
      projects: [firstProject, secondProject],
      selectedProject: firstProject,
      selectedConversation: firstConversation,
      projectConversations: [firstConversation],
      workspaceTask: workspaceTask(),
      obsidianSources: [firstSource, secondSource],
      obsidianItems: [firstItem, secondItem],
      obsidianSourceProjections: [firstSource, secondSource].map((source) => ({
        sourceId: source.id,
        sourceRevision: source.revision,
        itemCount: 1,
        loading: false,
        error: null,
      })),
      readObsidianItem,
    };
    const { rerender } = render(<WorkspaceShell model={model} />);

    fireEvent.click(screen.getByRole("tab", { name: "Obsidian" }));
    const knowledge = screen.getByLabelText("Obsidian project knowledge");
    fireEvent.click(within(knowledge).getByRole("button", { name: "Notes" }));
    expect(within(knowledge).getByRole("button", { name: /Architecture/ })).toBeVisible();
    expect(within(knowledge).getByRole("button", { name: /Evidence/ })).toBeVisible();
    fireEvent.change(within(knowledge).getByRole("combobox", { name: "Filter knowledge source" }), {
      target: { value: secondSource.id },
    });
    expect(within(knowledge).queryByRole("button", { name: /Architecture/ })).not.toBeInTheDocument();
    expect(within(knowledge).getByRole("button", { name: /Evidence/ })).toBeVisible();

    fireEvent.change(within(knowledge).getByRole("combobox", { name: "Filter knowledge source" }), {
      target: { value: firstSource.id },
    });
    fireEvent.click(within(knowledge).getByRole("button", { name: /Architecture/ }));
    const requestSignal = readObsidianItem.mock.calls[0][1] as AbortSignal;
    rerender(<WorkspaceShell model={{
      ...model,
      selectedProject: secondProject,
      obsidianSources: [],
      obsidianItems: [],
      obsidianSourceProjections: [],
    }} />);

    await waitFor(() => expect(requestSignal.aborted).toBe(true));
    resolveLate({
      source_id: firstSource.id,
      relative_path: firstItem.relative_path,
      title: "Late project note",
      kind: "markdown",
      content_hash: firstItem.content_hash,
      content: "This content belongs to the previous project.",
    });
    await Promise.resolve();
    expect(screen.queryByRole("heading", { name: "Late project note" })).not.toBeInTheDocument();
  });

  it("requires an explicit local Vault folder scope before connecting", async () => {
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
        workspace_id: project.workspace_id,
        version_id: "019f566f-f8b4-7000-8000-000000000143",
        path: "Notes/seed.md",
        byte_length: 12,
        content_hash: "c".repeat(64),
        kind: "text",
        language: "markdown",
        imports: [],
      }],
      selectObsidianVault: vi.fn(async () => ({
        local_path_token: "019f566f-f8b4-7000-8000-000000000142",
        display_name: "Project Vault",
        available_directories: ["Notes", "References"],
      })),
    };
    render(<WorkspaceShell model={model} />);

    fireEvent.click(screen.getByRole("tab", { name: "Obsidian" }));
    const knowledge = screen.getByLabelText("Obsidian project knowledge");
    fireEvent.click(within(knowledge).getByRole("button", { name: "Sync" }));
    fireEvent.click(within(knowledge).getByRole("button", { name: "Add Vault" }));

    expect(await within(knowledge).findByText("Project Vault")).toBeVisible();
    const submit = within(knowledge).getByRole("button", { name: "Confirm connection" });
    expect(submit).toBeDisabled();
    fireEvent.click(within(knowledge).getByRole("checkbox", { name: "Notes" }));
    expect(submit).toBeEnabled();
    fireEvent.click(submit);

    await waitFor(() => expect(model.connectObsidianVault).toHaveBeenCalledWith(
      expect.objectContaining({
        local_path_token: "019f566f-f8b4-7000-8000-000000000142",
      }),
      {
        readScope: "selected_directories",
        allowedDirectories: ["Notes"],
        wholeVaultConfirmed: false,
        managedDirectory: "Fairy",
      },
    ));
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
    connectionState: "ready",
    historyLoading: false,
    conversationContentState: "ready",
    projectContentState: "ready",
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
    realtimeTranscript: [],
    providers: [],
    providerHealth: [],
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
    workspaceActivePreviewId: null,
    selectedVersion: null,
    preview: null,
    previewActivation: null,
    previewActivationLoading: false,
    previewActivationError: null,
    runtimeHealth: null,
    browserHealth: null,
    browserSession: null,
    browserSnapshot: null,
    browserLoading: false,
    browserError: null,
    workspaceFiles: [],
    workspaceGeneration: 0,
    knowledgeOverview: null,
    knowledgeItems: [],
    knowledgeGraph: null,
    knowledgeLoading: false,
    knowledgeError: null,
    obsidianHealth: null,
    obsidianSources: [],
    obsidianItems: [],
    obsidianSourceProjections: [],
    obsidianLoading: false,
    obsidianError: null,
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
    listDocuments: vi.fn(async () => []),
    searchDocuments: vi.fn(async () => []),
    deleteDocument: vi.fn(async () => undefined),
    searchMemory: vi.fn(async () => []),
    forgetMemory: vi.fn(async () => undefined),
    setDeveloperMode: vi.fn(),
    selectModel: vi.fn(async () => undefined),
    refreshModelCatalog: vi.fn(async () => undefined),
    selectProject: vi.fn(),
    selectConversation: vi.fn(),
    selectChatConversation: vi.fn(),
    prefetchConversation: vi.fn(async () => undefined),
    selectTask: vi.fn(),
    createProject: vi.fn(async () => undefined),
    importProject: vi.fn(async () => undefined),
    selectProjectFolder: vi.fn(async () => "C:\\Projects\\selected"),
    selectObsidianVault: vi.fn(async () => null),
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
    pauseChatTurn: vi.fn(async () => undefined),
    resumeChatTurn: vi.fn(async () => undefined),
    steerChatTurn: vi.fn(async () => undefined),
    retryChatTurn: vi.fn(async () => undefined),
    retryPendingChatMessage: vi.fn(async () => undefined),
    deletePendingChatMessage: vi.fn(),
    takePendingChatMessageForEdit: vi.fn(() => null),
    copyMessage: vi.fn(async () => undefined),
    openMessageLink: vi.fn(async () => undefined),
    readWorkspaceFile: vi.fn(async () => {
      throw new Error("not used");
    }),
    readWorkspaceSource: vi.fn(async () => {
      throw new Error("source unavailable");
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
    pauseProjectTurn: vi.fn(async () => undefined),
    resumeProjectTurn: vi.fn(async () => undefined),
    steerProjectTurn: vi.fn(async () => undefined),
    decideApproval: vi.fn(async () => undefined),
    startPreview: vi.fn(async () => undefined),
    stopPreview: vi.fn(async () => undefined),
    startBrowser: vi.fn(async () => undefined),
    stopBrowser: vi.fn(async () => undefined),
    navigateBrowser: vi.fn(async () => undefined),
    openBrowserTab: vi.fn(async () => undefined),
    selectBrowserTab: vi.fn(async () => undefined),
    closeBrowserTab: vi.fn(async () => undefined),
    executeBrowserAction: vi.fn(async () => undefined),
    refreshBrowser: vi.fn(async () => undefined),
    setBrowserSurfaceActive: vi.fn(),
    reviewTask: vi.fn(async () => undefined),
    acceptVersion: vi.fn(async () => undefined),
    discardVersion: vi.fn(async () => undefined),
    retryWorkspace: vi.fn(async () => undefined),
    openSettings: vi.fn(async () => undefined),
  };
}

function obsidianSourceFixture(projectId: string): ObsidianSource {
  return {
    id: "019f566f-f8b4-7000-8000-000000000141",
    project_id: projectId,
    display_name: "Official Obsidian Vault",
    vault_display_path: "Project Vault",
    read_scope: "selected_directories",
    allowed_directories: ["Notes"],
    managed_directory: "Fairy",
    mode: "read_only",
    status: "ready",
    revision: 2,
    item_count: 1,
    last_synced_at: "2026-07-12T00:00:00Z",
    created_at: "2026-07-12T00:00:00Z",
    updated_at: "2026-07-12T00:00:00Z",
  };
}

function graphNode(
  id: string,
  projectId: string,
  kind: NonNullable<WorkspaceModel["knowledgeGraph"]>["nodes"][number]["kind"],
  title: string,
  sourceId: string | null,
): NonNullable<WorkspaceModel["knowledgeGraph"]>["nodes"][number] {
  return {
    id,
    project_id: projectId,
    kind,
    title,
    relative_path: null,
    content_hash: null,
    byte_length: null,
    language: null,
    revision: 1,
    conversation_id: null,
    task_id: null,
    source_id: sourceId,
    revision_id: null,
  };
}

function workspaceFile(path: string, imports: string[] = []): WorkspaceFile {
  return {
    path,
    byte_length: 128,
    content_hash: "f".repeat(64),
    kind: "source",
    language: path.split(".").at(-1) ?? null,
    imports,
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
