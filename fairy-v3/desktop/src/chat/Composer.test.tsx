import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { PendingImageAttachment } from "../perception/CaptureControl";
import { Composer } from "./Composer";

afterEach(cleanup);

describe("Composer", () => {
  it("keeps an attached screenshot but blocks sending after selecting a text-only model", async () => {
    const onSubmit = vi.fn(async () => undefined);
    render(<Composer disabled={false} isBusy={false} visionAvailable={false}
      modelCatalog={null} modelSelection={null} onSubmit={onSubmit}
      onStop={vi.fn()} onSelectModel={vi.fn()} onOpenModelSettings={vi.fn()}
      draft={{ id: "draft-a", value: "Read this", files: [], images: [{ kind: "display", source_id: "1", source_label: "Test display", media_type: "image/png", png_base64: "AA==", width: 1, height: 1, byte_length: 1, content_hash: "0".repeat(64), captured_at_ms: 0, persistence: "ephemeral" }] }} />);
    await waitFor(() => expect(screen.getByLabelText("Message Fairy")).toHaveValue("Read this"));
    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
    expect(screen.getByText(/choose a model with vision/i)).toBeVisible();
  });
  it("reports a rejected submission instead of silently restoring it", async () => {
    const user = userEvent.setup();
    renderComposer(async () => { throw new Error("Core unavailable"); });
    await user.type(screen.getByLabelText("Message Fairy"), "Keep me");
    await user.click(screen.getByRole("button", { name: "Send message" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not.*send|could not.*submit/i);
    expect(screen.getByLabelText("Message Fairy")).toHaveValue("Keep me");
  });

  it("rejects an oversized attachment batch without silently dropping documents", async () => {
    const user = userEvent.setup();
    renderComposer(vi.fn(async () => undefined));
    await user.upload(screen.getByLabelText("Attach documents", { selector: "input" }),
      Array.from({ length: 11 }, (_, i) => new File(["x"], `doc${i}.txt`, { type: "text/plain" })));
    expect(screen.getByRole("alert")).toHaveTextContent(/10 documents/i);
    expect(screen.queryByLabelText("Pending attachments")).not.toBeInTheDocument();
  });

  it("does not turn Enter inside an open schedule editor into immediate sending", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn(async () => undefined);
    const view = renderComposer(onSubmit);
    view.rerender(<Composer disabled={false} isBusy={false} visionAvailable={false}
      modelCatalog={null} modelSelection={null} onSubmit={onSubmit}
      onSchedule={async () => undefined} onStop={async () => undefined}
      onSelectModel={async () => undefined} onOpenModelSettings={async () => undefined} />);
    await user.type(screen.getByLabelText("Message Fairy"), "Tomorrow only");
    await user.click(screen.getByRole("button", { name: "Schedule message" }));
    await user.click(screen.getByRole("menuitem", { name: /Run later/ }));
    fireEvent.submit(screen.getByLabelText("Message Fairy").closest("form")!);
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Message Fairy")).toHaveValue("Tomorrow only");
  });

  it("keeps IME confirmation Enter from submitting", () => {
    const onSubmit = vi.fn(async () => undefined);
    renderComposer(onSubmit);
    const input = screen.getByLabelText("Message Fairy");
    fireEvent.change(input, { target: { value: "中文" } });
    fireEvent.keyDown(input, { key: "Enter", keyCode: 229 });
    expect(onSubmit).not.toHaveBeenCalled();
  });

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

  it("creates a future schedule and clears the accepted draft", async () => {
    const user = userEvent.setup();
    const onSchedule = vi.fn(async () => undefined);
    render(
      <Composer
        disabled={false}
        isBusy={false}
        visionAvailable={false}
        modelCatalog={null}
        modelSelection={null}
        onSubmit={vi.fn(async () => undefined)}
        onSchedule={onSchedule}
        onStop={vi.fn(async () => undefined)}
        onSelectModel={vi.fn(async () => undefined)}
        onOpenModelSettings={vi.fn(async () => undefined)}
      />,
    );

    const input = screen.getByLabelText("Message Fairy");
    await user.type(input, "Summarize tomorrow's workspace changes");
    await user.click(screen.getByRole("button", { name: "Schedule message" }));
    await user.click(screen.getByRole("menuitem", { name: /Run later/u }));
    await user.click(screen.getByRole("button", { name: "Create schedule" }));

    await waitFor(() => expect(onSchedule).toHaveBeenCalledTimes(1));
    expect(onSchedule).toHaveBeenCalledWith(
      "Summarize tomorrow's workspace changes",
      expect.objectContaining({ trigger_kind: "once" }),
    );
    expect(input).toHaveValue("");
  });

  it("blocks temporary documents from scheduled execution", async () => {
    const user = userEvent.setup();
    const onSchedule = vi.fn(async () => undefined);
    render(
      <Composer
        disabled={false}
        isBusy={false}
        visionAvailable={false}
        modelCatalog={null}
        modelSelection={null}
        onSubmit={vi.fn(async () => undefined)}
        onSchedule={onSchedule}
        onStop={vi.fn(async () => undefined)}
        onSelectModel={vi.fn(async () => undefined)}
        onOpenModelSettings={vi.fn(async () => undefined)}
      />,
    );

    await user.type(screen.getByLabelText("Message Fairy"), "Review this later");
    await user.upload(
      screen.getByLabelText("Attach documents", { selector: "input" }),
      new File(["draft"], "draft.md", { type: "text/markdown" }),
    );
    await user.click(screen.getByRole("button", { name: "Schedule message" }));
    await user.click(screen.getByRole("menuitem", { name: /Run later/u }));

    expect(screen.getByRole("alert")).toHaveTextContent("temporary attachments or screenshots");
    expect(screen.queryByLabelText("Schedule task")).not.toBeInTheDocument();
    expect(onSchedule).not.toHaveBeenCalled();
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
