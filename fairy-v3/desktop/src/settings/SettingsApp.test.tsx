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

  it("manages one OpenRouter account and model routing policy without arbitrary model ids", async () => {
    const invoke = settingsInvoke();
    render(<SettingsApp client={new SettingsClient(invoke as unknown as InvokeFunction)} />);
    await screen.findByRole("heading", { name: "General" });

    await userEvent.click(screen.getByRole("button", { name: /Models/ }));
    expect(screen.getByText("DeepSeek V4 Pro")).toBeVisible();
    expect(screen.queryByLabelText("Model ID")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: /^Zero data retention routing/ }));

    await vi.waitFor(() => {
      expect(rpcRequest(invoke, "models.selection.update")?.params).toEqual(
        expect.objectContaining({
          mode: "auto",
          model_id: null,
          zero_data_retention: true,
          expected_revision: 0,
        }),
      );
    });
  });

  it("persists the complete Liquid Glass pet settings through the same revision fence", async () => {
    const invoke = settingsInvoke();
    render(<SettingsApp client={new SettingsClient(invoke as unknown as InvokeFunction)} />);
    await screen.findByRole("heading", { name: "General" });
    await userEvent.click(screen.getByRole("button", { name: /^Pet/ }));

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Renderer" }), "compatibility");
    await vi.waitFor(() => {
      expect(screen.getByRole("combobox", { name: "Renderer" })).toHaveValue("compatibility");
    });
    fireEvent.click(screen.getByRole("checkbox", { name: "Do not disturb" }));
    await vi.waitFor(() => {
      expect(screen.getByRole("checkbox", { name: "Do not disturb" })).toBeChecked();
    });
    fireEvent.click(screen.getByRole("checkbox", { name: "Remember position" }));

    await vi.waitFor(() => {
      const updates = invoke.mock.calls.filter(([command]) => command === "desktop_preferences_update");
      expect(updates.length).toBeGreaterThanOrEqual(3);
    });
    const updates = invoke.mock.calls
      .filter(([command]) => command === "desktop_preferences_update")
      .map(([, args]) => (args as { input: { preferences: DesktopPreferences } }).input.preferences);
    expect(updates.some((preferences) => preferences.pet_renderer_mode === "compatibility")).toBe(true);
    expect(updates.some((preferences) => preferences.pet_do_not_disturb)).toBe(true);
    expect(updates.at(-1)?.pet_remember_position).toBe(false);
  });

  it("installs a curated Skill through the governed settings RPC", async () => {
    const invoke = settingsInvoke();
    render(<SettingsApp client={new SettingsClient(invoke as unknown as InvokeFunction)} />);
    await screen.findByRole("heading", { name: "General" });
    await userEvent.click(screen.getByRole("button", { name: /Skills \/ MCP/ }));
    await userEvent.click(screen.getByRole("tab", { name: "Store" }));
    expect(screen.getByText("Taste Skill")).toBeVisible();
    await userEvent.click(screen.getAllByRole("button", { name: "Install" })[0]);
    await vi.waitFor(() => expect(rpcRequest(invoke, "skills.install")?.params).toEqual({
      catalog_id: "design-taste-frontend",
      idempotency_key: "settings:catalog:design-taste-frontend:install",
    }));
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
    if (command === "provider_openrouter_status") return { configured: true, account_id: "openrouter-default" };
    if (command === "settings_rpc") {
      const request = args?.request as { id: number; method: CoreMethodName; params: Record<string, unknown> };
      return { jsonrpc: "2.0", id: request.id, result: resultFor(request.method, request.params) };
    }
    throw new Error(`Unexpected command: ${command}`);
  });
}

function resultFor(method: CoreMethodName, params: Record<string, unknown>) {
  switch (method) {
    case "models.catalog.list":
    case "models.catalog.refresh": return modelCatalog;
    case "models.selection.get": return modelSelection;
    case "models.selection.update": return {
      ...modelSelection,
      ...params,
      revision: Number(params.expected_revision) + 1,
      updated_at: "2026-07-12T00:00:01Z",
    };
    case "permissions.get": return { profile: "standard", capability_overrides: {}, revision: 7, updated_at: "2026-07-12T00:00:00Z" };
    case "permissions.update": return { profile: params.profile, capability_overrides: params.capability_overrides ?? {}, revision: 8, updated_at: "2026-07-12T00:00:01Z" };
    case "capabilities.get": return manifest;
    case "extensions.catalog.list": return { items: [{
      extension_id: "design-taste-frontend",
      kind: "skill",
      name: "Taste Skill",
      description: "Frontend design guidance.",
      publisher: "Leonxlnx",
      version: "2.0.0-experimental.1",
      source: "https://github.com/Leonxlnx/taste-skill",
      license: "MIT",
      experimental: true,
      installed: false,
    }] };
    case "skills.list": return { items: [], next_cursor: null };
    case "mcp.servers.list": return { items: [], next_cursor: null };
    case "tasks.list": return { items: [], next_cursor: null };
    default: return {};
  }
}

const modelSelection = {
  mode: "auto",
  model_id: null,
  allow_free_fallback: false,
  zero_data_retention: false,
  revision: 0,
  updated_at: "2026-07-12T00:00:00Z",
};

const modelCatalog = {
  account: {
    account_id: "openrouter-default",
    provider_kind: "openrouter",
    display_name: "OpenRouter",
    credential_status: "configured",
  },
  items: [{
    model_id: "deepseek/deepseek-v4-pro",
    display_name: "DeepSeek V4 Pro",
    category: "primary",
    endpoint_kind: "chat",
    description: "Primary model",
    paid: true,
    availability: "available",
    unavailable_reason: null,
    input_modalities: ["text"],
    output_modalities: ["text"],
    context_length: 131072,
    max_output_tokens: 16384,
    supports_tools: true,
    supports_structured_output: true,
    supports_streaming: true,
    supported_resolutions: [],
    supported_aspect_ratios: [],
    prices: [],
  }],
  fetched_at: "2026-07-12T00:00:00Z",
  expires_at: "2026-07-12T06:00:00Z",
  stale: false,
  revision: 1,
  last_error_code: null,
};

function rpcRequest(invoke: ReturnType<typeof settingsInvoke>, method: string) {
  return invoke.mock.calls
    .filter(([command]) => command === "settings_rpc")
    .map(([, args]) => (args as { request: { method: string; params: Record<string, unknown> } }).request)
    .find((request) => request.method === method);
}

function defaultPreferences(): DesktopPreferences {
  return {
    schema_version: 2,
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
    pet_size_percent: 100,
    pet_opacity_percent: 92,
    pet_motion_enabled: true,
    pet_particles_enabled: true,
    pet_hover_enabled: true,
    pet_hover_dwell_ms: 250,
    pet_do_not_disturb: false,
    pet_remember_position: true,
    pet_renderer_mode: "auto",
    pet_anchor: null,
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
