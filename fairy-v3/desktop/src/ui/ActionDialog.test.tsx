import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ActionDialog } from "./ActionDialog";

describe("ActionDialog", () => {
  it("requires a non-empty normalized value before confirming", () => {
    const confirm = vi.fn();
    render(
      <ActionDialog
        open
        title="Rename chat"
        description="Choose a durable title."
        confirmLabel="Rename"
        inputLabel="Title"
        initialValue=" Existing "
        onCancel={vi.fn()}
        onConfirm={confirm}
      />,
    );

    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "  Updated  " } });
    fireEvent.click(screen.getByRole("button", { name: "Rename" }));

    expect(confirm).toHaveBeenCalledWith("Updated");
  });

  it("defaults focus to cancellation for destructive actions", () => {
    render(
      <ActionDialog
        open
        destructive
        title="Delete chat"
        description="This removes it from history."
        confirmLabel="Delete"
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
    expect(screen.getByRole("alertdialog")).toBeVisible();
  });
});
