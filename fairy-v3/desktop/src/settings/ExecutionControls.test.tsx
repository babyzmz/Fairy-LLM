import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { CapabilityManifest, ExecutionSettings } from "../core/client";
import { ExecutionControls } from "./ExecutionControls";

describe("ExecutionControls", () => {
  it("renders Core-owned profiles and generated capability toggles", () => {
    const onProfileChange = vi.fn(async () => undefined);
    const onCapabilityChange = vi.fn(async () => undefined);
    render(
      <ExecutionControls
        settings={settings}
        manifest={manifest}
        disabled={false}
        onProfileChange={onProfileChange}
        onCapabilityChange={onCapabilityChange}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Execution controls" }));

    expect(screen.getByRole("radio", { name: "Standard" })).toBeChecked();
    fireEvent.click(screen.getByRole("radio", { name: "Autonomous" }));
    expect(onProfileChange).toHaveBeenCalledWith("autonomous");

    expect(screen.getByText("Search public web or news sources.")).toBeVisible();
    fireEvent.click(screen.getByRole("checkbox", { name: "web.search" }));
    expect(onCapabilityChange).toHaveBeenCalledWith("web.search", false);

    expect(screen.getByText("Sandbox unavailable")).toBeVisible();
    expect(screen.getByRole("checkbox", { name: "run.sandboxed" })).toBeDisabled();
  });
});

const settings: ExecutionSettings = {
  profile: "standard",
  capability_overrides: {},
  revision: 4,
  updated_at: "2026-07-12T00:00:00Z",
};

const manifest: CapabilityManifest = {
  profile: "standard",
  operations: { "web.search": true, "run.sandboxed": false },
  sandbox_healthy: false,
  command_metadata: [
    {
      name: "web.search",
      side_effect: "read",
      risk_level: "low",
      approval_policy: "never",
      profiles: ["observe", "standard", "autonomous"],
      requires_sandbox: false,
      idempotent: true,
      concurrency_policy: "parallel_read",
      concurrency_resource_keys: [],
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
      concurrency_policy: "serial",
      concurrency_resource_keys: [],
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
