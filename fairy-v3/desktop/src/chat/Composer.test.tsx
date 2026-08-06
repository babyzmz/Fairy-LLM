import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { PendingImageAttachment } from "../perception/CaptureControl";
import { Composer } from "./Composer";

afterEach(cleanup);

describe("Composer", () => {
  it("clears a submitted draft before Core finishes accepting it", async () => {
    const user = userEvent.setup();
    const submission = deferred<void>();
    const onSubmit = vi.fn(() => submission.promise);
    renderComposer(onSubmit);

    const input = screen.getByLabelText("Message Fairy");
    await user.type(input, "Send immediately");
    await user.click(screen.getByRole("button", { name: "Send message" }));

    expect(onSubmit).toHaveBeenCalledWith("Send immediately", [], []);
    expect(input).toHaveValue("");

    submission.resolve();
    await waitFor(() => expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled());
  });

  it("restores the submitted draft when Core rejects it", async () => {
    const user = userEvent.setup();
    const submission = deferred<void>();
    renderComposer(() => submission.promise);

    const input = screen.getByLabelText("Message Fairy");
    await user.type(input, "Keep this draft");
    await user.click(screen.getByRole("button", { name: "Send message" }));
    expect(input).toHaveValue("");

    submission.reject(new Error("Core unavailable"));
    await waitFor(() => expect(input).toHaveValue("Keep this draft"));
  });

  it("does not overwrite a newer draft when the submitted request fails", async () => {
    const user = userEvent.setup();
    const submission = deferred<void>();
    renderComposer(() => submission.promise);

    const input = screen.getByLabelText("Message Fairy");
    await user.type(input, "First message");
    await user.click(screen.getByRole("button", { name: "Send message" }));
    await user.type(input, "Next message");

    submission.reject(new Error("Core unavailable"));
    await waitFor(() => expect(input).toHaveValue("Next message"));
  });

  it("switches an active Workflow Composer to task-update controls", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn(async () => undefined);
    const onPauseWorkflow = vi.fn(async () => undefined);
    const onStop = vi.fn(async () => undefined);
    render(
      <Composer
        disabled={false}
        isBusy
        visionAvailable={false}
        modelCatalog={null}
        modelSelection={null}
        workflowSummary={workflowSummary("running")}
        onSubmit={onSubmit}
        onStop={onStop}
        onPauseWorkflow={onPauseWorkflow}
        onResumeWorkflow={vi.fn(async () => undefined)}
        onSelectModel={vi.fn(async () => undefined)}
        onOpenModelSettings={vi.fn(async () => undefined)}
      />,
    );

    expect(screen.getByText("Update current task")).toBeVisible();
    expect(screen.getByRole("button", { name: "Attach documents" })).toBeDisabled();
    await user.type(screen.getByLabelText("Update the current task"), "Use the newer sources");
    await user.click(screen.getByRole("button", { name: "Update current task" }));
    await user.click(screen.getByRole("button", { name: "Pause task" }));

    expect(onSubmit).toHaveBeenCalledWith("Use the newer sources", [], []);
    expect(onPauseWorkflow).toHaveBeenCalledTimes(1);
    expect(onStop).not.toHaveBeenCalled();
  });

  it("offers resume while a Workflow is paused", async () => {
    const user = userEvent.setup();
    const onResumeWorkflow = vi.fn(async () => undefined);
    render(
      <Composer
        disabled={false}
        isBusy={false}
        visionAvailable={false}
        modelCatalog={null}
        modelSelection={null}
        workflowSummary={workflowSummary("paused")}
        onSubmit={vi.fn(async () => undefined)}
        onStop={vi.fn(async () => undefined)}
        onPauseWorkflow={vi.fn(async () => undefined)}
        onResumeWorkflow={onResumeWorkflow}
        onSelectModel={vi.fn(async () => undefined)}
        onOpenModelSettings={vi.fn(async () => undefined)}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Resume task" }));

    expect(onResumeWorkflow).toHaveBeenCalledTimes(1);
  });
});

function renderComposer(
  onSubmit: (
    value: string,
    files: File[],
    images: PendingImageAttachment[],
  ) => Promise<void>,
) {
  return render(
    <Composer
      disabled={false}
      isBusy={false}
      visionAvailable={false}
      modelCatalog={null}
      modelSelection={null}
      onSubmit={onSubmit}
      onStop={vi.fn(async () => undefined)}
      onSelectModel={vi.fn(async () => undefined)}
      onOpenModelSettings={vi.fn(async () => undefined)}
    />,
  );
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function workflowSummary(status: "running" | "paused") {
  return {
    run_id: "019f566f-f8b4-7000-8000-000000000150",
    status,
    budget_tier: "normal" as const,
    active_plan_revision: 2,
    current_phase: "Responding",
    public_summary: "Fairy is working",
    completed_nodes: 0,
    total_nodes: 1,
    model_rounds_used: 1,
    max_model_rounds: 12,
    tool_invocations_used: 0,
    max_tool_invocations: 32,
    pause_requested: status === "paused",
    updated_at: "2026-07-12T00:00:00Z",
  };
}
