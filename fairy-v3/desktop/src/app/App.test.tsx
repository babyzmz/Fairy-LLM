import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  Approval,
  Conversation,
  EventEnvelope,
  Project,
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
};
const timestamp = "2026-07-11T00:00:00Z";

const project: Project = {
  id: ID.project,
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
  workspace_type: "project_chat",
  base_version_id: ID.version,
  active_draft_version_id: null,
  active_task_id: ID.task,
  active_preview_id: null,
  created_at: timestamp,
  updated_at: timestamp,
};
const task: Task = {
  id: ID.task,
  project_id: ID.project,
  conversation_id: ID.conversation,
  user_request: "Tighten the project overview",
  operation_mode: "continue_current_chat_draft",
  base_version_id: ID.version,
  execution_target: "local",
  target_version_id: ID.version,
  memory_snapshot_id: null,
  memory_snapshot_hash: null,
  status: "executing",
  created_at: timestamp,
  updated_at: timestamp,
};
const version: Version = {
  id: ID.version,
  project_id: ID.project,
  source_conversation_id: ID.conversation,
  source_task_id: ID.task,
  parent_version_id: null,
  project_root: "C:/Fairy/versions/atlas",
  visibility: "chat_draft",
  created_at: timestamp,
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
    expect(await screen.findByRole("option", { name: "Atlas Console" })).toBeVisible();
    expect(await screen.findByText("Scope resolved")).toBeVisible();
    expect(screen.queryByText("Developer diagnostic")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Task Timeline" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Preview" })).toBeVisible();
    expect(screen.getByLabelText("Workspace telemetry")).toHaveTextContent("Core ready");
    expect(screen.getByLabelText("Workspace telemetry")).toHaveTextContent("standard");
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
      decided_by: "desktop-user",
    });
  });
});

function createClient(
  health: WorkspaceClient["health"],
  projects: Project[],
  options: {
    approvals?: Approval[];
    decide?: WorkspaceClient["approvals"]["decide"];
  } = {},
): WorkspaceClient {
  return {
    health,
    projects: {
      list: async () => ({ items: projects, next_cursor: null }),
      create: async () => ({ project, initial_version: version }),
      import: async () => ({ project, initial_version: version }),
    },
    conversations: {
      list: async () => ({
        items: projects.length > 0 ? [conversation] : [],
        next_cursor: null,
      }),
    },
    tasks: {
      list: async () => ({ items: [task], next_cursor: null }),
      create: async () => {
        throw new Error("not used");
      },
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
    capabilities: {
      get: async () => ({
        profile: "standard",
        operations: {},
        sandbox_healthy: false,
        command_metadata: [],
        schema_version: 1,
      }),
    },
    events: {
      subscribe: () => visibleEvents(),
    },
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
