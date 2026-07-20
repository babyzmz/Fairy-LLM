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
