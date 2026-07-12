import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CapabilityManifest, CoreMethodName } from "../core/client";
import type { InvokeFunction } from "../core/tauriTransport";
import { SettingsApp } from "./SettingsApp";
import { SettingsClient, type DesktopPreferences } from "./client";

afterEach(() => {
  cleanup();
  localStorage.clear();
});

describe("SettingsApp", () => {
  it("loads all nine categories and persists ordinary preferences with a revision fence", async () => {
    const invoke = settingsInvoke();
    render(<SettingsApp client={new SettingsClient(invoke as unknown as InvokeFunction)} />);

    expect(await screen.findByRole("heading", { name: "General" })).toBeVisible();
    expect(screen.getByRole("navigation", { name: "Settings categories" }).querySelectorAll("button")).toHaveLength(9);

    await userEvent.click(screen.getByRole("button", { name: /Advanced/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Developer mode" }));

    await vi.waitFor(() => expect(invoke).toHaveBeenCalledWith(
      "desktop_preferences_update",
      expect.objectContaining({
        input: expect.objectContaining({ expected_revision: 0 }),
      }),
    ));
    const update = invoke.mock.calls.find(([command]) => command === "desktop_preferences_update")?.[1] as { input: { preferences: DesktopPreferences } };
    expect(update.input.preferences.developer_mode).toBe(true);
    expect(localStorage.getItem("fairy.workspace.developer")).toBe("true");
  });

  it("updates Core permissions only through settings_rpc", async () => {
    const invoke = settingsInvoke();
    render(<SettingsApp client={new SettingsClient(invoke as unknown as InvokeFunction)} />);
    await screen.findByRole("heading", { name: "General" });

    await userEvent.click(screen.getByRole("button", { name: /Execution permissions/ }));
    await userEvent.click(screen.getByRole("radio", { name: "Autonomous" }));

    await vi.waitFor(() => expect(rpcRequest(invoke, "permissions.update")).toBeDefined());
    expect(rpcRequest(invoke, "permissions.update")?.params).toEqual(expect.objectContaining({
      expected_revision: 7,
      profile: "autonomous",
    }));
    expect(invoke.mock.calls.some(([command]) => command === "core_rpc")).toBe(false);
  });

  it("searches the one-level settings navigation", async () => {
    render(<SettingsApp client={new SettingsClient(settingsInvoke() as unknown as InvokeFunction)} />);
    await screen.findByRole("heading", { name: "General" });

    await userEvent.type(screen.getByRole("textbox", { name: "Search settings" }), "speech");
    expect(screen.getByRole("button", { name: /Voice/ })).toBeVisible();
    expect(screen.queryByRole("button", { name: /Models/ })).not.toBeInTheDocument();
  });
});

function settingsInvoke() {
  let preferences = defaultPreferences();
  return vi.fn(async (command: string, args?: Record<string, unknown>) => {
    if (command === "desktop_preferences_get") return preferences;
    if (command === "desktop_preferences_update") {
      const input = args?.input as { preferences: DesktopPreferences };
      preferences = { ...input.preferences, revision: input.preferences.revision + 1 };
      return preferences;
    }
    if (command === "provider_openrouter_status") return { configured: true, model_id: "tencent/hy3:free" };
    if (command === "settings_rpc") {
      const request = args?.request as { id: number; method: CoreMethodName; params: Record<string, unknown> };
      return { jsonrpc: "2.0", id: request.id, result: resultFor(request.method, request.params) };
    }
    throw new Error(`Unexpected command: ${command}`);
  });
}

function resultFor(method: CoreMethodName, params: Record<string, unknown>) {
  switch (method) {
    case "providers.list": return { items: [{ id: "openrouter", display_name: "OpenRouter", kind: "openai_compatible", base_url: "https://openrouter.ai/api/v1", model_id: "tencent/hy3:free", capabilities: ["text", "tools"], credential_required: true, credential_configured: true, enabled: true, timeout_seconds: 60, fallback_profile_id: null }], next_cursor: null };
    case "providers.health": return { items: [{ profile_id: "openrouter", status: "available", error_code: null, diagnostics: [] }], next_cursor: null };
    case "permissions.get": return { profile: "standard", capability_overrides: {}, revision: 7, updated_at: "2026-07-12T00:00:00Z" };
    case "permissions.update": return { profile: params.profile, capability_overrides: params.capability_overrides ?? {}, revision: 8, updated_at: "2026-07-12T00:00:01Z" };
    case "capabilities.get": return manifest;
    case "skills.list": return { items: [], next_cursor: null };
    case "mcp.servers.list": return { items: [], next_cursor: null };
    default: return {};
  }
}

function rpcRequest(invoke: ReturnType<typeof settingsInvoke>, method: string) {
  return invoke.mock.calls
    .filter(([command]) => command === "settings_rpc")
    .map(([, args]) => (args as { request: { method: string; params: Record<string, unknown> } }).request)
    .find((request) => request.method === method);
}

function defaultPreferences(): DesktopPreferences {
  return {
    schema_version: 1,
    revision: 0,
    language: "system",
    launch_at_startup: false,
    minimize_to_tray: true,
    theme: "system",
    reduced_motion: false,
    compact_density: false,
    selected_profile_id: "openrouter",
    voice_auto_play_chat: false,
    voice_auto_play_pet: true,
    voice_volume_percent: 80,
    voice_rate_percent: 100,
    permission_cloud_profile: "standard",
    memory_enabled: true,
    memory_retention_days: 90,
    analytics_enabled: false,
    pet_enabled: true,
    pet_always_on_top: true,
    pet_muted: false,
    developer_mode: false,
  };
}

const manifest: CapabilityManifest = {
  profile: "standard",
  operations: { "web.search": true },
  sandbox_healthy: true,
  command_metadata: [{
    name: "web.search",
    side_effect: "read",
    risk_level: "low",
    approval_policy: "never",
    profiles: ["observe", "standard", "autonomous"],
    requires_sandbox: false,
    idempotent: true,
    model_visible: true,
    description: "Search public sources.",
    input_schema: { type: "object" },
    definition_digest: "web-search-v3",
    required_extensions: [],
    required_operations: [],
    source: "builtin",
  }],
  slash_commands: [],
  schema_version: 3,
};
