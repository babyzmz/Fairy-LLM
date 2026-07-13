import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
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
  Project,
  ProviderHealth,
  ProviderProfile,
  Task,
  Version,
} from "../core/client";
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
  id: "openrouter-free",
  display_name: "OpenRouter Free",
  kind: "openai_compatible",
  base_url: "https://openrouter.ai/api/v1",
  model_id: "openrouter/free",
  capabilities: ["text", "tools"],
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
  it("renders durable workspace data and user-visible events only", async () => {
    const health = vi.fn(async () => ({
      status: "ok",
      service: "fairy-core",
      protocol: "core-service-v1",
    }));
    const client = createClient(health, [project]);

    render(<App client={client} />);

    expect(screen.getByRole("banner")).toHaveTextContent("Core starting");
    await waitFor(() =>
      expect(screen.getByLabelText("History navigation")).toHaveTextContent("Atlas Console"),
    );
    expect(await screen.findByText("Scope resolved")).toBeVisible();
    expect(screen.queryByText("Developer diagnostic")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Task Timeline" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Preview" })).toBeVisible();
    expect(screen.getByLabelText("Workspace status")).toHaveTextContent("Core ready");
    expect(screen.getByLabelText("Workspace status")).toHaveTextContent("standard");
    expect(document.body).not.toHaveTextContent("NORTHSTAR");
    expect(document.body).not.toHaveTextContent("$12");
    expect(document.body.textContent).not.toMatch(/[璺鈥]/u);
    expect(health).toHaveBeenCalledTimes(1);
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
      expect.objectContaining({ task_id: ID.scratchTask, profile_id: provider.id }),
    );
    expect(listMessages).toHaveBeenCalledWith({
      conversation_id: ID.scratchConversation,
      limit: 100,
    });
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
    await userEvent.type(composer, "Keep this draft");
    await userEvent.click(screen.getByRole("button", { name: "Send message" }));

    expect(await screen.findByText("Permission prevents this action")).toBeVisible();
    expect(composer).toHaveValue("Keep this draft");
    expect(createTask).not.toHaveBeenCalled();
  });

});

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
  } = {},
): WorkspaceClient {
  return {
    desktop: { openSettings: async () => undefined },
    health,
    projects: {
      list: async () => ({ items: projects, next_cursor: null }),
      create: async () => ({ project, initial_version: version }),
      import: async () => ({ project, initial_version: version }),
      selectFolder: async () => "C:\\Projects\\selected",
    },
    conversations: {
      list: async () => ({
        items: [
          ...(projects.length > 0 ? [conversation] : []),
          ...(options.scratch ? [scratchConversation] : []),
        ],
        next_cursor: null,
      }),
      create: async () => scratchConversation,
      update: async () => scratchConversation,
      delete: async () => scratchConversation,
      moveToProject: async () => ({
        source_conversation: scratchConversation,
        destination_conversation: conversation,
        imported_count: 0,
      }),
    },
    tasks: {
      list: async () => ({ items: [task], next_cursor: null }),
      create: options.createTask ?? (async () => { throw new Error("not used"); }),
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
      openRouterStatus: async () => ({ configured: true, model_id: provider.model_id }),
      configureOpenRouter: async (input) => ({ configured: true, model_id: input.model_id }),
      deleteOpenRouter: async () => ({ configured: false, model_id: null }),
    },
    skills: {
      list: async () => ({ items: [] }),
    },
    mcp: {
      servers: {
        list: async () => ({ items: [] }),
        configure: async () => { throw new Error("not used"); },
        discover: async () => { throw new Error("not used"); },
        accept: async () => { throw new Error("not used"); },
        setEnabled: async () => { throw new Error("not used"); },
        delete: async () => { throw new Error("not used"); },
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
      import: async () => ({} as never),
      list: async () => ({ items: [] }),
      search: async () => ({ items: [] }),
      delete: async () => ({} as never),
    },
    memory: {
      search: async () => ({ items: [] }),
      forget: async () => ({} as never),
    },
    assistant: {
      turns: {
        create: options.createTurn ?? (async () => { throw new Error("not used"); }),
        get: async () => completedTurn,
        start: options.runTurn ?? (async () => { throw new Error("not used"); }),
        cancel: async () => completedTurn,
        retry: async () => completedTurn,
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
      execute: async () => ({} as never),
    },
    events: {
      subscribe: () => visibleEvents(),
    },
  };
}

function capabilityManifest(
  profile: ExecutionSettings["profile"],
  sandboxHealthy: boolean,
): CapabilityManifest {
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
