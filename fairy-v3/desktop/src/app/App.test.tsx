import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  Approval,
  AssistantTurn,
  CapabilityManifest,
  Conversation,
  EventEnvelope,
  ExecutionSettings,
  Message,
  ModelCatalogPage,
  ModelSelectionPreference,
  Project,
  ProviderHealth,
  ProviderProfile,
  Task,
  Version,
} from "../core/client";
import { CoreRpcError, type InvokeFunction } from "../core/tauriTransport";
import { SettingsClient, type DesktopPreferences } from "../settings/client";
import { App } from "./App";
import type { WorkspaceClient } from "./workspaceModel";

const ID = {
  project: "0198f4de-0114-7000-8000-000000000001",
  conversation: "0198f4de-0114-7000-8000-000000000002",
  task: "0198f4de-0114-7000-8000-000000000003",
  version: "0198f4de-0114-7000-8000-000000000004",
  event: "0198f4de-0114-7000-8000-000000000005",
  scratchConversation: "0198f4de-0114-7000-8000-000000000010",
  scratchTask: "0198f4de-0114-7000-8000-000000000011",
  turn: "0198f4de-0114-7000-8000-000000000012",
  message: "0198f4de-0114-7000-8000-000000000013",
};
const timestamp = "2026-07-11T00:00:00Z";

const project: Project = {
  id: ID.project,
  workspace_id: ID.project,
  name: "Atlas Console",
  residency: "local_only",
  active_version_id: ID.version,
  active_preview_id: null,
  revision: 1,
  pinned_at: null,
  archived_at: null,
  deleted_at: null,
  purged_at: null,
  metadata_revision: 0,
  created_at: timestamp,
  updated_at: timestamp,
};
const conversation: Conversation = {
  id: ID.conversation,
  project_id: ID.project,
  workspace_id: ID.project,
  workspace_type: "project_chat",
  base_version_id: ID.version,
  active_draft_version_id: null,
  active_task_id: ID.task,
  active_preview_id: null,
  title: "Project conversation",
  pinned_at: null,
  deleted_at: null,
  deleted_by_project_at: null,
  purged_at: null,
  revision: 0,
  created_at: timestamp,
  updated_at: timestamp,
};
const task: Task = {
  id: ID.task,
  project_id: ID.project,
  workspace_id: ID.project,
  conversation_id: ID.conversation,
  user_request: "Tighten the project overview",
  operation_mode: "continue_current_chat_draft",
  base_version_id: ID.version,
  execution_target: "local",
  target_version_id: ID.version,
  memory_snapshot_id: null,
  memory_snapshot_hash: null,
  status: "executing",
  display_title: "Tighten the project overview",
  pinned_at: null,
  metadata_revision: 0,
  created_at: timestamp,
  updated_at: timestamp,
};
const version: Version = {
  id: ID.version,
  project_id: ID.project,
  workspace_id: ID.project,
  source_conversation_id: ID.conversation,
  source_task_id: ID.task,
  parent_version_id: null,
  project_root: "C:/Fairy/versions/atlas",
  visibility: "chat_draft",
  created_at: timestamp,
};
const scratchConversation: Conversation = {
  id: ID.scratchConversation,
  project_id: null,
  workspace_id: ID.scratchConversation,
  workspace_type: "chat_scratch",
  base_version_id: null,
  active_draft_version_id: null,
  active_task_id: null,
  active_preview_id: null,
  title: "New conversation",
  pinned_at: null,
  deleted_at: null,
  deleted_by_project_at: null,
  purged_at: null,
  revision: 0,
  created_at: timestamp,
  updated_at: timestamp,
};
const scratchMessage: Message = {
  id: ID.message,
  conversation_id: ID.scratchConversation,
  task_id: ID.scratchTask,
  turn_id: ID.turn,
  sequence: 1,
  role: "assistant",
  visibility: "user",
  content: "Scratch chat is durable",
  created_at: timestamp,
};
const provider: ProviderProfile = {
  id: "openrouter-deepseek-v4-pro",
  display_name: "DeepSeek V4 Pro",
  kind: "openai_compatible",
  base_url: "https://openrouter.ai/api/v1",
  model_id: "deepseek/deepseek-v4-pro",
  capabilities: ["text", "tools", "structured_output"],
  credential_required: true,
  credential_configured: true,
  enabled: true,
  timeout_seconds: 60,
  fallback_profile_id: null,
};
const providerHealth: ProviderHealth = {
  profile_id: provider.id,
  status: "available",
  error_code: null,
  diagnostics: [],
};
const modelSelection: ModelSelectionPreference = {
  mode: "auto",
  model_id: null,
  allow_free_fallback: false,
  zero_data_retention: false,
  revision: 0,
  updated_at: timestamp,
};
const modelCatalog: ModelCatalogPage = {
  account: {
    account_id: "openrouter-default",
    provider_kind: "openrouter",
    display_name: "OpenRouter",
    credential_status: "configured",
  },
  items: [{
    model_id: provider.model_id,
    display_name: provider.display_name,
    category: "primary",
    endpoint_kind: "chat",
    description: "Primary model",
    paid: true,
    availability: "available",
    unavailable_reason: null,
    input_modalities: ["text"],
    output_modalities: ["text"],
    context_length: 131072,
    max_output_tokens: 16384,
    supports_tools: true,
    supports_structured_output: true,
    supports_streaming: true,
    supported_resolutions: [],
    supported_aspect_ratios: [],
    prices: [],
  }],
  fetched_at: timestamp,
  expires_at: timestamp,
  stale: false,
  revision: 1,
  last_error_code: null,
};
const completedTurn: AssistantTurn = {
  id: ID.turn,
  task_id: ID.scratchTask,
  conversation_id: ID.scratchConversation,
  profile_id: provider.id,
  status: "completed",
  idempotency_key: "desktop:test",
  scope_digest: "scope",
  memory_snapshot_id: "0198f4de-0114-7000-8000-000000000014",
  memory_snapshot_hash: "memory",
  model_selection: null,
  routing_decision: null,
  budget_approval_run_id: null,
  cancellation_revision: 0,
  usage: {},
  created_at: timestamp,
  updated_at: timestamp,
  started_at: timestamp,
  completed_at: timestamp,
  error_code: null,
};

beforeEach(() => window.localStorage.clear());
afterEach(cleanup);

describe("App", () => {
  it("recovers when the first health check races Core startup", async () => {
    const health = vi
      .fn<WorkspaceClient["health"]>()
      .mockRejectedValueOnce(
        new CoreRpcError({
          code: -32050,
          message: "Fairy Core process was interrupted",
          data: { error_code: "WORKER_INTERRUPTED" },
        }),
      )
      .mockResolvedValue({
        status: "ok",
        service: "fairy-core",
        protocol: "core-service-v1",
      });

    render(<App client={createClient(health, [project])} />);

    expect(screen.getByRole("banner")).toHaveTextContent("Core starting");
    await waitFor(() => expect(screen.getByLabelText("Workspace status")).toHaveTextContent("Core ready"));
    expect(health).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("heading", { name: "Core offline" })).not.toBeInTheDocument();
  });

  it("renders durable workspace data without exposing raw Ledger events", async () => {
    const health = vi.fn(async () => ({
      status: "ok",
      service: "fairy-core",
      protocol: "core-service-v1",
    }));
    const client = createClient(health, [project]);

    render(<App client={client} />);

    expect(screen.getByRole("banner")).toHaveTextContent("Core starting");
    await waitFor(() => expect(screen.getByLabelText("History navigation")).toHaveTextContent("Atlas Console"));
    expect(await screen.findByText("Waiting for durable activity")).toBeVisible();
    expect(screen.queryByText("Scope resolved")).not.toBeInTheDocument();
    expect(screen.queryByText("Developer diagnostic")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Task Timeline" })).toBeVisible();
    expect(screen.getByRole("combobox", { name: "Select task" })).toHaveValue(ID.task);
    await userEvent.click(screen.getByRole("tab", { name: "Preview" }));
    expect(screen.getByRole("heading", { name: "Preview" })).toBeVisible();
    expect(screen.getByLabelText("Workspace status")).toHaveTextContent("Core ready");
    expect(screen.getByLabelText("Workspace status")).toHaveTextContent("standard");
    expect(document.body).not.toHaveTextContent("NORTHSTAR");
    expect(document.body).not.toHaveTextContent("$12");
    expect(document.body.textContent).not.toMatch(/[璺鈥]/u);
    expect(health).toHaveBeenCalledTimes(1);
  });

  it("loads every project page before rendering history navigation", async () => {
    const secondProject: Project = {
      ...project,
      id: "0198f4de-0114-7000-8000-000000000099",
      name: "Second workspace",
      workspace_id: "0198f4de-0114-7000-8000-000000000098",
    };
    const listProjects = vi.fn<WorkspaceClient["projects"]["list"]>(async (request) =>
      request?.cursor === "page-2"
        ? { items: [secondProject], next_cursor: null }
        : { items: [project], next_cursor: "page-2" },
    );
    const client = createClient(
      async () => ({
        status: "ok",
        service: "fairy-core",
        protocol: "core-service-v1",
      }),
      [project, secondProject],
      { listProjects },
    );

    render(<App client={client} />);

    const history = await screen.findByLabelText("History navigation");
    await waitFor(() => expect(history).toHaveTextContent("Second workspace"));
    expect(listProjects).toHaveBeenCalledWith(expect.objectContaining({ cursor: "page-2" }));
  });

  it("opens the workspace while the optional model catalog is still loading", async () => {
    const client = createClient(
      async () => ({
        status: "ok",
        service: "fairy-core",
        protocol: "core-service-v1",
      }),
      [project],
      { listModelCatalog: () => new Promise(() => undefined) },
    );

    render(<App client={client} />);

    expect(await screen.findByRole("heading", { name: "Task Timeline" })).toBeVisible();
    expect(screen.getByLabelText("Workspace status")).toHaveTextContent("Core ready");
  });

  it("keeps the Chat frame visible while an unvisited conversation loads", async () => {
    window.localStorage.setItem("fairy.workspace.mode", "chat");
    const client = createClient(
      async () => ({
        status: "ok",
        service: "fairy-core",
        protocol: "core-service-v1",
      }),
      [project],
      {
        scratch: true,
        listMessages: () => new Promise(() => undefined),
      },
    );

    render(<App client={client} />);

    const history = await screen.findByLabelText("History navigation");
    await waitFor(() => expect(history).toHaveTextContent("New conversation"));
    expect(await screen.findByRole("heading", { name: "Chat" })).toBeVisible();
    expect(screen.getByLabelText("Message Fairy")).toBeDisabled();
    expect(screen.queryByText("Connecting to Fairy Core")).not.toBeInTheDocument();
  });

  it("opens Settings inside the main view and preserves the Composer draft on return", async () => {
    window.localStorage.setItem("fairy.workspace.mode", "chat");
    const settingsClient = new SettingsClient(
      appSettingsInvoke() as unknown as InvokeFunction,
    );
    const client = createClient(
      async () => ({
        status: "ok",
        service: "fairy-core",
        protocol: "core-service-v1",
      }),
      [project],
      { scratch: true },
    );

    render(<App client={client} settingsClient={settingsClient} />);

    const composer = await screen.findByLabelText("Message Fairy");
    await waitFor(() => expect(composer).toBeEnabled());
    await userEvent.type(composer, "Keep this draft while settings are open");
    await userEvent.click(screen.getByRole("button", { name: "Open settings" }));

    expect(await screen.findByRole("heading", { name: "General" })).toBeVisible();
    expect(screen.getByTestId("workspace-view")).toHaveAttribute("hidden");
    await userEvent.click(screen.getByRole("button", { name: "Back to workspace" }));

    expect(screen.getByLabelText("Message Fairy")).toBe(composer);
    expect(composer).toHaveValue("Keep this draft while settings are open");
  });

  it("renders real create and import actions for an empty repository", async () => {
    const client = createClient(
      async () => ({
        status: "ok",
        service: "fairy-core",
        protocol: "core-service-v1",
      }),
      [],
    );

    render(<App client={client} />);

    expect(await screen.findByRole("heading", { name: "Projects" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Create project" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Import folder" })).toBeDisabled();
  });

  it("dispatches approval decisions through the typed client", async () => {
    const decide = vi.fn(async () => ({}) as never);
    const pending: Approval = {
      id: "0198f4de-0114-7000-8000-000000000007",
      task_id: ID.task,
      command_run_id: "0198f4de-0114-7000-8000-000000000008",
      requested_by: "agent",
      reason: "Apply the scoped Changeset",
      changeset_id: "0198f4de-0114-7000-8000-000000000009",
      tool_invocation_id: null,
      decision: "pending",
      decided_by: null,
      created_at: timestamp,
      decided_at: null,
    };
    const client = createClient(
      async () => ({
        status: "ok",
        service: "fairy-core",
        protocol: "core-service-v1",
      }),
      [project],
      { approvals: [pending], decide },
    );
    render(<App client={client} />);

    await userEvent.click(await screen.findByRole("button", { name: "Approve" }));

    expect(decide).toHaveBeenCalledWith({
      approval_id: pending.id,
      approved: true,
    });
  });

  it("keeps the Composer busy while Core resumes an approved Assistant Turn", async () => {
    window.localStorage.setItem("fairy.workspace.mode", "chat");
    const waitingTurn: AssistantTurn = {
      ...completedTurn,
      status: "waiting_for_tool",
      completed_at: null,
    };
    const pending: Approval = {
      id: "0198f4de-0114-7000-8000-000000000020",
      task_id: ID.scratchTask,
      command_run_id: "0198f4de-0114-7000-8000-000000000021",
      requested_by: "agent",
      reason: "Send a desktop notification",
      changeset_id: null,
      tool_invocation_id: "0198f4de-0114-7000-8000-000000000022",
      decision: "pending",
      decided_by: null,
      created_at: timestamp,
      decided_at: null,
    };
    const runTurn = vi.fn(async () => waitingTurn);
    const decide = vi.fn(async () => ({
      approval: {
        ...pending,
        decision: "approved" as const,
        decided_by: "user",
        decided_at: timestamp,
      },
      changeset: null,
      assistant_turn_id: ID.turn,
      resume_requested: true,
    }));
    const client = createClient(
      async () => ({
        status: "ok",
        service: "fairy-core",
        protocol: "core-service-v1",
      }),
      [project],
      {
        scratch: true,
        approvals: [pending],
        decide,
        createTask: async () => ({ task: { id: ID.scratchTask } }) as never,
        createTurn: async () => ({ ...waitingTurn, status: "created" }),
        runTurn,
      },
    );
    render(<App client={client} />);

    const composer = await screen.findByLabelText("Message Fairy");
    await waitFor(() => expect(composer).toBeEnabled());
    await userEvent.type(composer, "Notify me");
    await userEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(runTurn).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("button", { name: "Stop response" })).not.toBeInTheDocument();

    await userEvent.click(await screen.findByRole("button", { name: "Approve" }));

    expect(decide).toHaveBeenCalledTimes(1);
    expect(runTurn).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("button", { name: "Stop response" })).toBeEnabled();
  });

  it("loads durable scratch chat and runs a task-bound assistant turn", async () => {
    window.localStorage.setItem("fairy.workspace.mode", "chat");
    const listMessages = vi.fn(async () => ({ items: [scratchMessage], next_cursor: null }));
    const createTask = vi.fn(async () => ({ task: { id: ID.scratchTask } }) as never);
    const createTurn = vi.fn(async () => ({ ...completedTurn, status: "created" }) as AssistantTurn);
    const runTurn = vi.fn(async () => completedTurn);
    const client = createClient(
      async () => ({
        status: "ok",
        service: "fairy-core",
        protocol: "core-service-v1",
      }),
      [project],
      { scratch: true, createTask, createTurn, runTurn, listMessages },
    );
    render(<App client={client} />);

    expect(await screen.findByRole("heading", { name: "Chat" })).toBeVisible();
    expect(await screen.findByText("Scratch chat is durable")).toBeVisible();
    await userEvent.type(screen.getByLabelText("Message Fairy"), "Check Sydney weather");
    await userEvent.click(screen.getByRole("button", { name: "Send message" }));

    await vi.waitFor(() => expect(runTurn).toHaveBeenCalledWith(ID.turn));
    expect(createTask).toHaveBeenCalledWith(
      expect.objectContaining({
        conversation_id: ID.scratchConversation,
        operation_mode: "answer",
        user_request: "Check Sydney weather",
      }),
    );
    expect(createTurn).toHaveBeenCalledWith(
      expect.objectContaining({
        task_id: ID.scratchTask,
        model_selection: { mode: "auto", model_id: null, revision: 0 },
      }),
    );
    expect(listMessages).toHaveBeenCalledWith({
      conversation_id: ID.scratchConversation,
      limit: 100,
    });
  });

  it("refreshes conversation metadata before a confirmed delete", async () => {
    window.localStorage.setItem("fairy.workspace.mode", "chat");
    const latest = { ...scratchConversation, revision: 7, title: "New conversation" };
    const getConversation = vi.fn(async () => latest);
    const deleteConversation = vi.fn(async () => ({
      ...latest,
      deleted_at: timestamp,
      revision: 8,
    }));
    const client = createClient(
      async () => ({
        status: "ok",
        service: "fairy-core",
        protocol: "core-service-v1",
      }),
      [],
      { scratch: true, getConversation, deleteConversation },
    );
    render(<App client={client} />);

    const history = await screen.findByLabelText("History navigation");
    fireEvent.contextMenu(await within(history).findByTitle("New conversation"));
    await userEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(deleteConversation).toHaveBeenCalledWith({
      conversation_id: ID.scratchConversation,
      expected_revision: 7,
      user_confirmed: true,
    }));
    expect(getConversation).toHaveBeenCalledWith(ID.scratchConversation);
  });

  it("binds project composer requests to the selected project conversation", async () => {
    const projectTurn = {
      ...completedTurn,
      task_id: ID.task,
      conversation_id: ID.conversation,
    };
    const createTask = vi.fn(async () => ({ task: { id: ID.task } }) as never);
    const createTurn = vi.fn(async () => ({ ...projectTurn, status: "created" }) as AssistantTurn);
    const runTurn = vi.fn(async () => projectTurn);
    const client = createClient(
      async () => ({
        status: "ok",
        service: "fairy-core",
        protocol: "core-service-v1",
      }),
      [project],
      { createTask, createTurn, runTurn },
    );
    render(<App client={client} />);

    const composer = await screen.findByLabelText("Message Fairy");
    await waitFor(() => expect(composer).toBeEnabled());
    await userEvent.type(composer, "Implement the approved layout");
    await userEvent.click(screen.getByRole("button", { name: "Send message" }));

    await vi.waitFor(() => expect(runTurn).toHaveBeenCalledWith(ID.turn));
    expect(createTask).toHaveBeenCalledWith(
      expect.objectContaining({
        conversation_id: ID.conversation,
        operation_mode: "continue_current_chat_draft",
        user_request: "Implement the approved layout",
      }),
    );
  });

  it("keeps the project draft and explains Observe permission blocking", async () => {
    const createTask = vi.fn(async () => ({ task: { id: ID.task } }) as never);
    const client = createClient(
      async () => ({
        status: "ok",
        service: "fairy-core",
        protocol: "core-service-v1",
      }),
      [project],
      {
        createTask,
        capabilities: { get: async () => capabilityManifest("observe", false) },
        permissions: {
          get: async () => ({
            profile: "observe",
            capability_overrides: {},
            revision: 0,
            updated_at: "2026-07-11T00:00:00Z",
          }),
          update: async () => {
            throw new Error("not used");
          },
        },
      },
    );
    render(<App client={client} />);

    const composer = await screen.findByLabelText("Message Fairy");
    await waitFor(() => expect(composer).toBeEnabled());
    await userEvent.type(composer, "Keep this draft");
    await userEvent.click(screen.getByRole("button", { name: "Send message" }));

    expect(await screen.findByText("Permission prevents this action")).toBeVisible();
    expect(composer).toHaveValue("Keep this draft");
    expect(createTask).not.toHaveBeenCalled();
  });
});

function appSettingsInvoke() {
  const preferences: DesktopPreferences = {
    schema_version: 7,
    revision: 0,
    language: "system",
    launch_at_startup: false,
    minimize_to_tray: true,
    theme: "system",
    reduced_motion: false,
    compact_density: false,
    selected_profile_id: "openrouter",
    voice_auto_play_chat: false,
    voice_auto_play_pet: true,
    voice_volume_percent: 80,
    voice_rate_percent: 100,
    permission_cloud_profile: "standard",
    analytics_enabled: false,
    realtime_provider: "auto",
    realtime_voice_mode: "native",
    realtime_game_audio_default: false,
    realtime_memory_enabled: true,
    realtime_max_session_minutes: 30,
    trash_auto_purge_30_days: false,
    pet_enabled: true,
    pet_always_on_top: true,
    pet_muted: false,
    pet_size_percent: 100,
    pet_opacity_percent: 92,
    pet_motion_enabled: true,
    pet_particles_enabled: true,
    pet_hover_enabled: true,
    pet_hover_dwell_ms: 250,
    pet_do_not_disturb: false,
    ambient_dialogue_enabled: true,
    ambient_dialogue_voice_enabled: false,
    ambient_generated_dialogue_enabled: false,
    pet_remember_position: true,
    pet_renderer_mode: "auto",
    pet_optics_mode: "standard",
    pet_activation_style: "fluid_response",
    pet_target_fps: 60,
    pet_anchor: null,
    developer_mode: false,
  };
  return vi.fn(async (command: string, args?: Record<string, unknown>) => {
    if (command === "desktop_preferences_get") return preferences;
    if (command === "desktop_preferences_update") return preferences;
    if (command === "settings_rpc") {
      const request = args?.request as { id?: number; method?: string } | undefined;
      if (request?.method === "projects.archived.list" || request?.method === "trash.items.list") {
        return {
          jsonrpc: "2.0",
          id: request.id ?? 1,
          result: { items: [], next_cursor: null },
        };
      }
    }
    throw new Error(`Unexpected settings command: ${command}`);
  });
}

function createClient(
  health: WorkspaceClient["health"],
  projects: Project[],
  options: {
    approvals?: Approval[];
    decide?: WorkspaceClient["approvals"]["decide"];
    scratch?: boolean;
    createTask?: WorkspaceClient["tasks"]["create"];
    createTurn?: WorkspaceClient["assistant"]["turns"]["create"];
    runTurn?: WorkspaceClient["assistant"]["turns"]["start"];
    permissions?: WorkspaceClient["permissions"];
    capabilities?: WorkspaceClient["capabilities"];
    listMessages?: WorkspaceClient["messages"]["list"];
    getConversation?: WorkspaceClient["conversations"]["get"];
    deleteConversation?: WorkspaceClient["conversations"]["delete"];
    listModelCatalog?: WorkspaceClient["models"]["catalog"]["list"];
    listProjects?: WorkspaceClient["projects"]["list"];
  } = {},
): WorkspaceClient {
  return {
    desktop: { openSettings: async () => undefined },
    browser: {
      health: async () => ({ available: false, browser_name: "Microsoft Edge", browser_version: null, error_code: "CAPABILITY_NOT_AVAILABLE", diagnostic: null }),
      profile: async () => ({ id: "fairy-default", kind: "persistent", configured: true, local_only: true, retention_days: 7 }),
      sessions: {
        start: async () => { throw new Error("not used"); },
        get: async () => { throw new Error("not used"); },
        list: async () => ({ items: [] }),
        stop: async () => { throw new Error("not used"); },
        resume: async () => { throw new Error("not used"); },
      },
      tabs: {
        open: async () => { throw new Error("not used"); },
        select: async () => { throw new Error("not used"); },
        close: async () => { throw new Error("not used"); },
      },
      actions: { execute: async () => { throw new Error("not used"); } },
      snapshots: { get: async () => { throw new Error("not used"); } },
    },
    health,
    projects: {
      list: options.listProjects ?? (async () => ({ items: projects, next_cursor: null })),
      create: async () => ({ project, initial_version: version }),
      import: async () => ({ project, initial_version: version }),
      get: async () => project,
      updateMetadata: async () => project,
      archive: async () => project,
      delete: async () => project,
      selectFolder: async () => "C:\\Projects\\selected",
    },
    conversations: {
      list: async () => ({
        items: [...(projects.length > 0 ? [conversation] : []), ...(options.scratch ? [scratchConversation] : [])],
        next_cursor: null,
      }),
      create: async () => scratchConversation,
      get: options.getConversation ?? (async (conversationId) =>
        conversationId === scratchConversation.id ? scratchConversation : conversation),
      update: async () => scratchConversation,
      delete: options.deleteConversation ?? (async () => scratchConversation),
      moveToProject: async () => ({
        source_conversation: scratchConversation,
        destination_conversation: conversation,
        imported_count: 0,
      }),
    },
    tasks: {
      list: async () => ({ items: [task], next_cursor: null }),
      get: async () => task,
      create:
        options.createTask ??
        (async () => {
          throw new Error("not used");
        }),
      review: async () => {
        throw new Error("not used");
      },
      updateMetadata: async () => task,
      archive: async () => task,
    },
    approvals: {
      list: async () => ({ items: options.approvals ?? [], next_cursor: null }),
      decide:
        options.decide ??
        (async () => {
          throw new Error("not used");
        }),
    },
    versions: {
      list: async () => ({ items: [version], next_cursor: null }),
      accept: async () => project,
      discard: async () => task,
    },
    workspaces: {
      get: async (workspaceId) => ({
        id: workspaceId,
        active_version_id: ID.version,
        active_preview_id: null,
        revision: 1,
        max_files: 200,
        max_bytes: 20 * 1024 * 1024,
        created_at: timestamp,
        updated_at: timestamp,
      }),
      listFiles: async (input) => ({
        workspace_id: input.workspace_id,
        version_id: input.version_id ?? ID.version,
        generation: 1,
        source_hash: "workspace-source",
        items: [],
      }),
      readFile: async () => {
        throw new Error("not used");
      },
      openStream: async () => {
        throw new Error("not used");
      },
      mutateFiles: async () => {
        throw new Error("not used");
      },
      export: async () => {
        throw new Error("not used");
      },
    },
    files: {
      present: async () => {
        throw new Error("not used");
      },
      compare: async () => {
        throw new Error("not used");
      },
    },
    fileSets: {
      resolve: async () => {
        throw new Error("not used");
      },
    },
    assetSets: {
      list: async () => ({ items: [] }),
    },
    annotations: {
      list: async () => ({ document: null }),
      update: async () => {
        throw new Error("not used");
      },
    },
    selections: {
      create: async () => {
        throw new Error("not used");
      },
    },
    runtimes: {
      health: async () => ({
        executor: {
          available: false,
          executor: "rust_local_worker",
          version: null,
          error_code: "SANDBOX_UNAVAILABLE",
          diagnostics: [],
        },
        runtime: null,
        preview: null,
      }),
    },
    previews: {
      activate: async () => {
        throw new Error("not used");
      },
      resolve: async () => null,
      start: async () => {
        throw new Error("not used");
      },
      stop: async () => {
        throw new Error("not used");
      },
    },
    capabilities: options.capabilities ?? {
      get: async () => capabilityManifest("standard", false),
    },
    permissions: options.permissions ?? {
      get: async () => ({
        profile: "standard",
        capability_overrides: {},
        revision: 0,
        updated_at: "2026-07-11T00:00:00Z",
      }),
      update: async (input) => ({
        profile: input.profile,
        capability_overrides: input.capability_overrides ?? {},
        revision: input.expected_revision + 1,
        updated_at: "2026-07-11T00:00:01Z",
      }),
    },
    providers: {
      list: async () => ({ items: [provider] }),
      health: async () => ({ items: [providerHealth] }),
      openRouterStatus: async () => ({ configured: true, account_id: "openrouter-default" }),
      configureOpenRouter: async () => ({ configured: true, account_id: "openrouter-default" }),
      deleteOpenRouter: async () => ({ configured: false, account_id: null }),
    },
    models: {
      catalog: {
        list: options.listModelCatalog ?? (async () => modelCatalog),
        refresh: async () => modelCatalog,
      },
      selection: {
        get: async () => modelSelection,
        update: async (input) => ({
          ...modelSelection,
          mode: input.mode,
          model_id: input.model_id,
          revision: input.expected_revision + 1,
        }),
      },
    },
    media: {
      jobs: { list: async () => ({ items: [] }) },
      videos: {
        cancel: async () => {
          throw new Error("not used");
        },
      },
    },
    skills: {
      list: async () => ({ items: [] }),
    },
    mcp: {
      servers: {
        list: async () => ({ items: [] }),
        configure: async () => {
          throw new Error("not used");
        },
        discover: async () => {
          throw new Error("not used");
        },
        accept: async () => {
          throw new Error("not used");
        },
        setEnabled: async () => {
          throw new Error("not used");
        },
        delete: async () => {
          throw new Error("not used");
        },
      },
    },
    messages: {
      list:
        options.listMessages ??
        (async () => ({
          items: options.scratch ? [scratchMessage] : [],
          next_cursor: null,
        })),
    },
    documents: {
      import: async () => ({}) as never,
      list: async () => ({ items: [] }),
      search: async () => ({ items: [] }),
      delete: async () => ({}) as never,
    },
    memory: {
      search: async () => ({ items: [] }),
      forget: async () => ({}) as never,
    },
    knowledge: {
      overview: async (projectId) => ({
        project_id: projectId,
        source_version_id: null,
        source_revision: 0,
        watermark: "0".repeat(64),
        file_count: 0,
        note_count: 0,
        conversation_count: 0,
        relation_count: 0,
        obsidian_connected: false,
        obsidian_health: "not_connected",
      }),
      listItems: async () => ({ items: [], source_revision: 0, watermark: "0".repeat(64) }),
      graph: async (projectId) => ({
        project_id: projectId,
        source_version_id: null,
        source_revision: 0,
        watermark: "0".repeat(64),
        nodes: [],
        edges: [],
      }),
    },
    obsidian: {
      selectVault: async () => null,
      health: async () => ({
        desktop_installed: false,
        cli_available: false,
        minimum_installer_version: "1.12.7",
        status: "not_installed",
        public_summary: "Install Obsidian 1.12.7 or newer to connect a Vault",
      }),
      createSource: async () => ({}) as never,
      listSources: async () => ({ items: [] }),
      listItems: async () => ({ items: [], source_revision: 0 }),
      readItem: async () => ({}) as never,
      sync: async () => ({}) as never,
    },
    assistant: {
      turns: {
        create:
          options.createTurn ??
          (async () => {
            throw new Error("not used");
          }),
        get: async () => completedTurn,
        start:
          options.runTurn ??
          (async () => {
            throw new Error("not used");
          }),
        cancel: async () => completedTurn,
        retry: async () => completedTurn,
        trace: async (turnId) => ({
          id: "0198f4de-0114-7000-8000-000000000071",
          turn_id: turnId,
          conversation_id: completedTurn.conversation_id,
          task_id: completedTurn.task_id,
          legacy: true,
          last_sequence: 0,
          revision: 0,
          created_at: completedTurn.created_at,
          updated_at: completedTurn.updated_at,
          started_at: completedTurn.started_at,
          completed_at: completedTurn.completed_at,
          steps: [],
        }),
      },
    },
    voice: {
      transcribe: async () => {
        throw new Error("not used");
      },
      synthesize: async () => {
        throw new Error("not used");
      },
    },
    systemActions: {
      execute: async () => ({}) as never,
    },
    events: {
      sourceId: () => "test:app",
      state: async () => ({
        ledger_id: "0198f4de-0114-7000-8000-000000000099",
        oldest_cursor: 0,
        latest_cursor: 0,
      }),
      list: async (cursor = 0) => ({ items: [], next_cursor: cursor }),
      subscribe: () => visibleEvents(),
    },
  };
}

function capabilityManifest(profile: ExecutionSettings["profile"], sandboxHealthy: boolean): CapabilityManifest {
  return {
    profile,
    operations: {
      "web.search": true,
      "run.sandboxed": profile === "autonomous" && sandboxHealthy,
    },
    sandbox_healthy: sandboxHealthy,
    command_metadata: [
      {
        name: "web.search",
        side_effect: "read",
        risk_level: "low",
        approval_policy: "never",
        profiles: ["observe", "standard", "autonomous"],
        requires_sandbox: false,
        idempotent: true,
        model_visible: true,
        description: "Search public web or news sources.",
        input_schema: { type: "object" },
        definition_digest: "web-search-v3",
        required_extensions: [],
        required_operations: [],
        source: "builtin",
      },
      {
        name: "run.sandboxed",
        side_effect: "execute",
        risk_level: "high",
        approval_policy: "never",
        profiles: ["autonomous"],
        requires_sandbox: true,
        idempotent: false,
        model_visible: true,
        description: "Run a governed command.",
        input_schema: { type: "object" },
        definition_digest: "run-sandboxed-v3",
        required_extensions: [],
        required_operations: [],
        source: "builtin",
      },
    ],
    slash_commands: [],
    schema_version: 3,
  };
}

async function* visibleEvents(): AsyncIterable<EventEnvelope> {
  yield {
    id: ID.event,
    cursor: 1,
    run_id: null,
    project_id: ID.project,
    conversation_id: ID.conversation,
    task_id: ID.task,
    version_id: ID.version,
    task_sequence: 1,
    event_type: "task.scope_resolved",
    visibility: "user",
    message: "Scope resolved",
    payload: {},
    schema_version: 1,
    created_at: timestamp,
  };
  yield {
    id: "0198f4de-0114-7000-8000-000000000006",
    cursor: 2,
    run_id: null,
    project_id: ID.project,
    conversation_id: ID.conversation,
    task_id: ID.task,
    version_id: ID.version,
    task_sequence: 2,
    event_type: "command.debug",
    visibility: "developer",
    message: "Developer diagnostic",
    payload: {},
    schema_version: 1,
    created_at: timestamp,
  };
}
