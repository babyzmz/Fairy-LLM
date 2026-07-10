import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { WorkspaceShell } from "./WorkspaceShell";

describe("WorkspaceShell", () => {
  it("keeps scope context, task timeline, and preview visible", () => {
    render(
      <WorkspaceShell
        context={{
          project: "Fairy V3",
          conversation: "Homepage revision",
          version: "Current draft",
          executionTarget: "Local",
          permission: "Standard",
          syncStatus: "Synced",
        }}
      />,
    );

    expect(screen.getByRole("banner")).toHaveTextContent("Fairy V3");
    expect(screen.getByRole("banner")).toHaveTextContent("Standard");
    expect(screen.getByRole("heading", { name: "Task Timeline" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Preview" })).toBeVisible();
    expect(screen.getByLabelText("Message Fairy")).toBeVisible();
  });
});
