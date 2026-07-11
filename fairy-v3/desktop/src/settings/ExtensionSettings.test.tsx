import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { McpServer, Skill } from "../core/client";
import { ExtensionSettings } from "./ExtensionSettings";

describe("ExtensionSettings", () => {
  it("shows governed skill provenance and conservative MCP review defaults", () => {
    const onAccept = vi.fn(async () => undefined);
    render(
      <ExtensionSettings
        skills={[skill]}
        servers={[server]}
        disabled={false}
        discoveryAvailable={true}
        onConfigure={vi.fn(async () => undefined)}
        onDiscover={vi.fn(async () => undefined)}
        onAccept={onAccept}
        onEnabledChange={vi.fn(async () => undefined)}
        onDelete={vi.fn(async () => undefined)}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Extensions" }));
    expect(screen.getByText("Fairy Docs")).toBeVisible();
    expect(screen.getByText("Fairy Labs")).toBeVisible();

    fireEvent.click(screen.getByRole("tab", { name: /MCP/u }));
    expect(screen.getByText("Credential configured")).toBeVisible();
    expect(document.body.textContent).not.toContain("secret-token");
    expect(screen.getByLabelText("Effect")).toHaveValue("execute");
    expect(screen.getByLabelText("Risk")).toHaveValue("high");
    expect(screen.getByLabelText("Approval")).toHaveValue("always");

    fireEvent.click(screen.getByRole("button", { name: "Accept reviewed schema" }));
    expect(onAccept).toHaveBeenCalledWith("docs", [
      expect.objectContaining({
        name: "search",
        side_effect: "execute",
        risk_level: "high",
        approval_policy: "always",
        profiles: ["standard", "autonomous"],
        idempotent: false,
      }),
    ]);
  });
});

const skill: Skill = {
  name: "Fairy Docs",
  version: "1.0.0",
  description: "Work with governed project documents.",
  tool_name: "skill.fairy-docs",
  required_capabilities: ["document.search"],
  compatible_mcp_servers: ["docs"],
  provenance: {
    source: "fairy://skills/docs",
    publisher: "Fairy Labs",
    license: "Apache-2.0",
  },
  content_sha256: "a".repeat(64),
  available: true,
};

const server: McpServer = {
  server_id: "docs",
  display_name: "Document server",
  transport: "streamable_http",
  command: null,
  arguments: [],
  endpoint: "https://mcp.example.test/mcp",
  credential_configured: true,
  environment_names: [],
  enabled: false,
  status: "review_required",
  revision: 2,
  accepted_schema_digest: null,
  pending_schema_digest: "pending-digest",
  accepted_tools: [],
  pending_tools: [
    {
      name: "search",
      title: "Search",
      description: "Search governed documents.",
      input_schema: { type: "object" },
      output_schema: null,
      schema_digest: "tool-digest",
      imported_name: "mcp.docs.search",
    },
  ],
  policies: [],
  last_error_code: null,
  created_at: "2026-07-12T00:00:00Z",
  updated_at: "2026-07-12T00:00:00Z",
};
