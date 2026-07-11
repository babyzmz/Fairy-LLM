import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
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
});

function workspaceModel(): WorkspaceModel {
  return {
    state: "ready",
    statusLabel: "Core ready",
    errorMessage: null,
    actionError: null,
    isActing: false,
    projects: [],
    conversations: [],
    tasks: [],
    versions: [],
    approvals: [],
    events: [],
    selectedProject: null,
    selectedConversation: null,
    selectedTask: null,
    selectedVersion: null,
    preview: null,
    runtimeHealth: null,
    capabilities: null,
    selectProject: vi.fn(),
    selectConversation: vi.fn(),
    selectTask: vi.fn(),
    createProject: vi.fn(async () => undefined),
    importProject: vi.fn(async () => undefined),
    createTask: vi.fn(async () => undefined),
    decideApproval: vi.fn(async () => undefined),
    startPreview: vi.fn(async () => undefined),
    stopPreview: vi.fn(async () => undefined),
    acceptVersion: vi.fn(async () => undefined),
    discardVersion: vi.fn(async () => undefined),
  };
}
