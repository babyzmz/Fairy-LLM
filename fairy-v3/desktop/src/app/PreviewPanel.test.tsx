import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { PreviewContext, Task } from "../core/client";
import { PreviewPanel } from "./PreviewPanel";

const id = {
  project: "0198f4de-0114-7000-8000-000000000001",
  conversation: "0198f4de-0114-7000-8000-000000000002",
  task: "0198f4de-0114-7000-8000-000000000003",
  version: "0198f4de-0114-7000-8000-000000000004",
  runtime: "0198f4de-0114-7000-8000-000000000005",
  preview: "0198f4de-0114-7000-8000-000000000006",
};
const timestamp = "2026-07-11T00:00:00Z";

afterEach(cleanup);

describe("PreviewPanel", () => {
  it("embeds only the Core loopback URL in a sandboxed iframe", async () => {
    const stop = vi.fn(async () => undefined);
    renderPanel(readyContext(), stop);

    const frame = screen.getByTitle("Task preview");
    expect(frame).toHaveAttribute(
      "src",
      `http://127.0.0.1:43125/${id.preview}/`,
    );
    expect(frame).toHaveAttribute("sandbox", "allow-forms allow-scripts");
    expect(frame.getAttribute("sandbox")).not.toContain("allow-same-origin");

    await userEvent.click(screen.getByRole("button", { name: "Stop preview" }));
    expect(stop).toHaveBeenCalledTimes(1);
  });

  it("keeps the ready preview visible while applying and reloads at a verified Workspace generation", () => {
    const context = readyContext();
    const { rerender } = render(panel(context, undefined, undefined, true, 3));
    const previousFrame = screen.getByTitle("Task preview");
    expect(screen.getByText("Applying verified update")).toBeVisible();

    rerender(panel(context, undefined, undefined, false, 4));
    expect(screen.getByTitle("Task preview")).not.toBe(previousFrame);
    expect(screen.queryByText("Applying verified update")).not.toBeInTheDocument();
  });

  it("blocks a forged local URL and can restart an interrupted preview", async () => {
    const forged = readyContext();
    forged.preview.url = "http://localhost:43125/preview/";
    const { rerender } = renderPanel(forged);

    expect(screen.queryByTitle("Task preview")).not.toBeInTheDocument();
    expect(screen.getByText("Preview URL blocked")).toBeVisible();

    const interrupted = readyContext();
    interrupted.preview.status = "interrupted";
    interrupted.preview.health = "interrupted";
    interrupted.preview.error_code = "WORKER_INTERRUPTED";
    const restart = vi.fn(async () => undefined);
    rerender(panel(interrupted, undefined, restart));
    expect(screen.getByText("Preview interrupted")).toBeVisible();
    expect(screen.getByText("WORKER_INTERRUPTED")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Restart preview" }));
    expect(restart).toHaveBeenCalledOnce();
  });

  it("starts an executable Preview and enables ready Version decisions", async () => {
    const start = vi.fn(async () => undefined);
    const readyTask = { ...task(), status: "ready" as const };
    const context = { ...readyContext(), task: readyTask };
    const accept = vi.fn(async () => undefined);
    const discard = vi.fn(async () => undefined);
    const { rerender } = render(
      <PreviewPanel
        task={{ ...task(), status: "executing" }}
        context={null}
        runtimeHealth={{
          executor: {
            available: true,
            executor: "rust_local_worker",
            version: "1",
            error_code: null,
            diagnostics: [],
          },
          runtime: null,
          preview: null,
        }}
        isActing={false}
        onStart={start}
        onStop={vi.fn(async () => undefined)}
        onReview={vi.fn(async () => undefined)}
        onAccept={accept}
        onDiscard={discard}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Start preview" }));
    expect(start).toHaveBeenCalledTimes(1);

    rerender(
      <PreviewPanel
        task={readyTask}
        context={context}
        runtimeHealth={null}
        isActing={false}
        onStart={start}
        onStop={vi.fn(async () => undefined)}
        onReview={vi.fn(async () => undefined)}
        onAccept={accept}
        onDiscard={discard}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Use this version" }));
    await userEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(accept).toHaveBeenCalledTimes(1);
    expect(discard).toHaveBeenCalledTimes(1);

    rerender(
      <PreviewPanel
        task={{ ...readyTask, status: "accepted" }}
        context={context}
        runtimeHealth={null}
        isActing={false}
        onStart={start}
        onStop={vi.fn(async () => undefined)}
        onReview={vi.fn(async () => undefined)}
        onAccept={accept}
        onDiscard={discard}
      />,
    );
    expect(screen.getByText("Version active")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Use this version" })).not.toBeInTheDocument();
  });

  it("runs governed Review before Version decisions become available", async () => {
    const review = vi.fn(async () => undefined);
    const context = readyContext();
    render(
      <PreviewPanel
        task={context.task}
        context={context}
        runtimeHealth={null}
        isActing={false}
        onStart={vi.fn(async () => undefined)}
        onStop={vi.fn(async () => undefined)}
        onReview={review}
        onAccept={vi.fn(async () => undefined)}
        onDiscard={vi.fn(async () => undefined)}
      />,
    );

    expect(screen.getByRole("button", { name: "Use this version" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Review" }));
    expect(review).toHaveBeenCalledTimes(1);
  });

  it("shows the typed failure and allows the failed candidate to be discarded", async () => {
    const discard = vi.fn(async () => undefined);
    render(
      <PreviewPanel
        task={{ ...task(), status: "failed" }}
        context={null}
        runtimeHealth={{
          executor: {
            available: true,
            executor: "rust_local_worker",
            version: "1",
            error_code: null,
            diagnostics: [],
          },
          runtime: { ...readyContext().runtime, status: "failed", health: "unhealthy", error_code: "SCOPE_MISMATCH" },
          preview: null,
        }}
        isActing={false}
        onStart={vi.fn(async () => undefined)}
        onStop={vi.fn(async () => undefined)}
        onReview={vi.fn(async () => undefined)}
        onAccept={vi.fn(async () => undefined)}
        onDiscard={discard}
      />,
    );

    expect(screen.getByText("Task failed")).toBeVisible();
    expect(screen.getByText("SCOPE_MISMATCH")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(discard).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Use this version" })).toBeDisabled();
  });
});

function renderPanel(context: PreviewContext, stop = vi.fn(async () => undefined)) {
  return render(panel(context, stop));
}

function panel(
  context: PreviewContext,
  stop = vi.fn(async () => undefined),
  start = vi.fn(async () => undefined),
  isActing = false,
  workspaceGeneration = 0,
) {
  return (
    <PreviewPanel
      task={context.task}
      context={context}
      runtimeHealth={null}
      isActing={isActing}
      workspaceGeneration={workspaceGeneration}
      onStart={start}
      onStop={stop}
      onReview={vi.fn(async () => undefined)}
      onAccept={vi.fn(async () => undefined)}
      onDiscard={vi.fn(async () => undefined)}
    />
  );
}

function readyContext(): PreviewContext {
  return {
    task: task(),
    runtime: {
      id: id.runtime,
      project_id: id.project,
      workspace_id: id.project,
      conversation_id: id.conversation,
      task_id: id.task,
      version_id: id.version,
      project_root: "C:/Fairy/version",
      execution_target: "local",
    kind: "static_site",
    graph: {
      public_service_id: "app",
      services: [
        {
          service_id: "app",
          adapter: "static",
          cwd: ".",
          readiness_path: "/",
          depends_on: [],
        },
      ],
    },
      executor: "rust_local_worker",
      executor_handle: `static:${id.preview}`,
      port: 43125,
      status: "running",
      health: "healthy",
      error_code: null,
      idempotency_key: "preview:runtime",
      revision: 2,
      created_at: timestamp,
      updated_at: timestamp,
    },
    preview: {
      id: id.preview,
      project_id: id.project,
      workspace_id: id.project,
      conversation_id: id.conversation,
      task_id: id.task,
      version_id: id.version,
      runtime_id: id.runtime,
      project_root: "C:/Fairy/version",
      execution_target: "local",
      url: `http://127.0.0.1:43125/${id.preview}/`,
      visibility: "chat_draft",
      status: "ready",
      health: "healthy",
      error_code: null,
      idempotency_key: "preview:start",
      revision: 2,
      created_at: timestamp,
      updated_at: timestamp,
    },
  };
}

function task(): Task {
  return {
    id: id.task,
    project_id: id.project,
    workspace_id: id.project,
    conversation_id: id.conversation,
    user_request: "Build preview",
    operation_mode: "continue_current_chat_draft",
    base_version_id: id.version,
    execution_target: "local",
    target_version_id: id.version,
    memory_snapshot_id: null,
    memory_snapshot_hash: null,
    status: "previewing",
    display_title: "Build preview",
    pinned_at: null,
    metadata_revision: 0,
    created_at: timestamp,
    updated_at: timestamp,
  };
}
