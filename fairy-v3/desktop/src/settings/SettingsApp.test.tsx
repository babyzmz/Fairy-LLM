import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  CapabilityManifest,
  CoreMethodName,
  KnowledgeSource,
  MemoryProposal,
  ObsidianConnectorHealth,
  Project,
  ProjectArchivedItem,
  Task,
  TrashItem,
} from "../core/client";
import type { InvokeFunction } from "../core/tauriTransport";
import type { PresenceRendererHealth } from "../presence/transport/rendererHealth";
import { SettingsApp } from "./SettingsApp";
import {
  SettingsClient,
  type DesktopPreferences,
  type LocalReadinessReport,
  type VoiceWorkerHealth,
} from "./client";

afterEach(() => {
  cleanup();
  localStorage.clear();
});

describe("SettingsApp", () => {
  it("loads only General data until another category is opened", async () => {
    const invoke = settingsInvoke();
    render(<SettingsApp client={new SettingsClient(invoke as unknown as InvokeFunction)} />);

    expect(await screen.findByRole("checkbox", { name: "Launch at startup" })).toBeInTheDocument();
    expect(screen.getByRole("main", { name: "Fairy settings" }))
      .toHaveAttribute("data-settings-state", "ready");
    expect(rpcRequests(invoke, "projects.archived.list")).toHaveLength(1);
    expect(rpcRequests(invoke, "trash.items.list")).toHaveLength(1);
    expect(rpcRequests(invoke, "models.catalog.list")).toHaveLength(0);
    expect(rpcRequests(invoke, "models.selection.get")).toHaveLength(0);
    expect(rpcRequests(invoke, "extensions.catalog.list")).toHaveLength(0);
    expect(rpcRequests(invoke, "memory.settings.get")).toHaveLength(0);
    expect(invoke.mock.calls.some(([command]) => command === "pet_renderer_get_health")).toBe(false);

    await userEvent.click(screen.getByRole("button", { name: /Models/ }));
    await vi.waitFor(() => {
      expect(rpcRequests(invoke, "models.catalog.list")).toHaveLength(1);
      expect(rpcRequests(invoke, "models.selection.get")).toHaveLength(1);
    });
    expect(rpcRequests(invoke, "extensions.catalog.list")).toHaveLength(0);
    expect(rpcRequests(invoke, "memory.settings.get")).toHaveLength(0);
    expect(invoke.mock.calls.some(([command]) => command === "pet_renderer_get_health")).toBe(false);
  });

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

  it("shows one explicit Companion start action with fail-closed local readiness", async () => {
    const invoke = settingsInvoke();
    render(<SettingsApp client={new SettingsClient(invoke as unknown as InvokeFunction)} />);
    await screen.findByRole("heading", { name: "General" });

    await userEvent.click(screen.getByRole("button", { name: /Voice/ }));

    expect(screen.getByRole("checkbox", { name: /^Fairy voice replies/ })).toBeChecked();
    expect(screen.queryByRole("checkbox", { name: "Auto-play in main chat" })).toBeNull();
    expect(screen.queryByRole("checkbox", { name: "Auto-play pet replies" })).toBeNull();
    expect(screen.getByRole("heading", { name: "Realtime Companion Beta" })).toBeVisible();
    expect(await screen.findByRole("status", { name: "Local readiness status" }))
      .toHaveTextContent("Runtime required");
    expect(screen.queryByRole("checkbox", { name: /^Enable Realtime Beta/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Start Realtime Companion" }));
    expect(invoke.mock.calls.some(([command]) => command === "open_companion_window")).toBe(true);
    await userEvent.click(screen.getByText("Advanced Realtime settings"));
    expect(screen.getByRole("combobox", { name: "Backend" })).toHaveValue("auto");
    expect(screen.getByRole("option", { name: "Local MiniCPM-o 4.5 Beta" })).toBeDisabled();
    expect(screen.getByRole("combobox", { name: "Cloud provider" })).toHaveValue("glm_realtime_flash");
    expect(screen.getByRole("combobox", { name: "Activity profile" })).toHaveValue("auto");
    expect(screen.getByRole("combobox", { name: "Interaction intensity" })).toHaveValue("standard");
    expect(screen.getByRole("combobox", { name: "Voice output" })).toHaveValue("fairy_voice");
    expect(screen.getByText(
      "Local Beta is selectable only after the complete readiness report passes. Cloud Live remains available.",
    )).toBeVisible();
    expect(screen.queryByText(/Local (is )?ready/i)).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /^Allow cloud fallback/ })).not.toBeChecked();
    expect(screen.getByRole("combobox", { name: "Local observation scope" })).toHaveValue("selected_window");
    expect(screen.getByRole("textbox", { name: "Excluded applications" })).toHaveValue("");
    expect(screen.getByRole("checkbox", { name: /^Use application audio by default/ })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: /^Online assistance/ })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: /^Offer companion memory/ })).toBeChecked();
    expect(screen.getByRole("combobox", { name: "Cloud daily limit" })).toHaveValue("180");
  });

  it("starts the stopped voice worker explicitly and waits for a ready result", async () => {
    const invoke = settingsInvoke({ voiceHealth: idleVoiceHealth() });
    render(<SettingsApp client={new SettingsClient(invoke as unknown as InvokeFunction)} />);
    await screen.findByRole("heading", { name: "General" });
    await userEvent.click(screen.getByRole("button", { name: /Voice/ }));

    expect(await screen.findByText("Stopped · ready to start")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Start Fairy voice" }));

    await vi.waitFor(() => {
      expect(invoke.mock.calls.some(([command]) => command === "voice_worker_prepare")).toBe(true);
    });
    expect(await screen.findByText("Ready at 24 kHz")).toBeVisible();
  });

  it("commits normalized Realtime exclusions only after composition finishes", async () => {
    const invoke = settingsInvoke();
    render(<SettingsApp client={new SettingsClient(invoke as unknown as InvokeFunction)} />);
    await screen.findByRole("heading", { name: "General" });
    await userEvent.click(screen.getByRole("button", { name: /Voice/ }));
    await userEvent.click(screen.getByText("Advanced Realtime settings"));
    const input = screen.getByRole("textbox", { name: "Excluded applications" });

    fireEvent.compositionStart(input);
    fireEvent.change(input, { target: { value: " OBS64.EXE, Private-App.Exe " } });
    fireEvent.keyDown(input, { key: "Enter", isComposing: true });
    expect(invoke.mock.calls.some(([command]) => command === "desktop_preferences_update"))
      .toBe(false);

    fireEvent.compositionEnd(input);
    fireEvent.blur(input);

    await vi.waitFor(() => {
      const update = invoke.mock.calls.find(([command]) =>
        command === "desktop_preferences_update"
      )?.[1] as { input: { preferences: DesktopPreferences } } | undefined;
      expect(update?.input.preferences.realtime_excluded_applications).toEqual([
        "obs64.exe",
        "private-app.exe",
      ]);
    });
  });

  it("shows native string errors instead of replacing them with a generic failure", async () => {
    const invoke = settingsInvoke();
    invoke.mockImplementationOnce(async () => defaultPreferences());
    render(<SettingsApp client={new SettingsClient(invoke as unknown as InvokeFunction)} />);

    await screen.findByRole("checkbox", { name: "Launch at startup" });
    invoke.mockRejectedValueOnce("Pet render window is unavailable");
    fireEvent.click(screen.getByRole("checkbox", { name: "Launch at startup" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Pet render window is unavailable");
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

  it("updates durable Memory only through the Core revision fence", async () => {
    const invoke = settingsInvoke();
    render(<SettingsApp client={new SettingsClient(invoke as unknown as InvokeFunction)} />);
    await screen.findByRole("heading", { name: "General" });

    await userEvent.click(screen.getByRole("button", { name: /Knowledge & privacy/ }));
    await userEvent.click(screen.getByRole("tab", { name: "Memory" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Use durable memory" }));

    await vi.waitFor(() => expect(rpcRequest(invoke, "memory.settings.update")?.params)
      .toEqual(expect.objectContaining({
        enabled: false,
        retention_days: 365,
        expected_revision: 0,
      })));
    expect(invoke.mock.calls.some(([command]) => command === "desktop_preferences_update")).toBe(false);
  });

  it("aggregates project knowledge sources and confirms Memory proposals before writing", async () => {
    const project = activeProjectFixture();
    const task = activeTaskFixture(project);
    const source = knowledgeSourceFixture(project);
    const proposal = memoryProposalFixture(task);
    const invoke = settingsInvoke({
      projects: [project],
      tasks: [task],
      knowledgeSources: [source],
      memoryProposals: [proposal],
      obsidianHealth: {
        desktop_installed: true,
        cli_available: true,
        minimum_installer_version: "1.12.7",
        status: "ready",
        public_summary: "Obsidian Desktop and CLI are ready",
      },
    });
    render(<SettingsApp client={new SettingsClient(invoke as unknown as InvokeFunction)} />);
    await screen.findByRole("heading", { name: "General" });

    await userEvent.click(screen.getByRole("button", { name: /Knowledge & privacy/ }));
    expect(screen.getByText("Product notes")).toBeVisible();
    expect(screen.getByText(/Research Vault/)).toBeVisible();

    await userEvent.click(screen.getByRole("tab", { name: /Proposals/ }));
    expect(screen.getByText(proposal.content)).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Accept" }));
    expect(rpcRequest(invoke, "memory.proposals.accept")).toBeUndefined();
    expect(screen.getByRole("dialog")).toHaveTextContent("Existing claims are not silently overwritten");
    await userEvent.click(screen.getByRole("button", { name: "Accept memory" }));

    await vi.waitFor(() => expect(rpcRequest(invoke, "memory.proposals.accept")?.params).toEqual({
      task_id: task.id,
      observation_id: proposal.id,
      user_confirmed: true,
      idempotency_key: `settings:memory-proposal:accept:${proposal.id}:${proposal.source_cursor}`,
    }));
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

  it("confirms credential removal inside the settings surface", async () => {
    const invoke = settingsInvoke();
    render(<SettingsApp client={new SettingsClient(invoke as unknown as InvokeFunction)} />);
    await screen.findByRole("heading", { name: "General" });
    await userEvent.click(screen.getByRole("button", { name: /Models/ }));

    await userEvent.click(screen.getByRole("button", { name: "Remove OpenRouter credential" }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("encrypted credential");
    await userEvent.click(screen.getByRole("button", { name: "Remove" }));

    await vi.waitFor(() => expect(invoke).toHaveBeenCalledWith("provider_openrouter_delete"));
  });

  it("persists the complete Liquid Glass pet settings through the same revision fence", async () => {
    const invoke = settingsInvoke({ rendererHealth: nativeRendererHealth() });
    render(<SettingsApp client={new SettingsClient(invoke as unknown as InvokeFunction)} />);
    await screen.findByRole("heading", { name: "General" });
    await userEvent.click(screen.getByRole("button", { name: /^Pet/ }));
    expect(screen.getByText(
      /Native DDA Liquid Glass.*144 FPS on 144 Hz.*3\.2 ms p95/,
    )).toBeVisible();

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Renderer" }), "compatibility");
    await vi.waitFor(() => {
      expect(screen.getByRole("combobox", { name: "Renderer" })).toHaveValue("compatibility");
    });
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Renderer" }), "liquid");
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Glass privacy" }),
      "enhanced",
    );
    expect(await screen.findByText(/GPU-acquires the active monitor/)).toBeVisible();
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Material response" }),
      "classic",
    );
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Animation frame rate" }),
      "300",
    );
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
    expect(updates.some((preferences) => preferences.pet_optics_mode === "enhanced")).toBe(true);
    expect(updates.some((preferences) => preferences.pet_activation_style === "classic")).toBe(true);
    expect(updates.some((preferences) => preferences.pet_target_fps === 300)).toBe(true);
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

  it("installs the official GSAP ScrollTrigger Skill from the curated store", async () => {
    const invoke = settingsInvoke();
    render(<SettingsApp client={new SettingsClient(invoke as unknown as InvokeFunction)} />);
    await screen.findByRole("heading", { name: "General" });
    await userEvent.click(screen.getByRole("button", { name: /Skills \/ MCP/ }));
    await userEvent.click(screen.getByRole("tab", { name: "Store" }));

    const entry = screen.getByText("GSAP ScrollTrigger").closest("section");
    expect(entry).not.toBeNull();
    await userEvent.click(within(entry as HTMLElement).getByRole("button", { name: "Install" }));

    await vi.waitFor(() => expect(rpcRequest(invoke, "skills.install")?.params).toEqual({
      catalog_id: "gsap-scrolltrigger",
      idempotency_key: "settings:catalog:gsap-scrolltrigger:install",
    }));
  });

  it("provides an independent MCP store and installs Playwright as a disabled preset", async () => {
    const invoke = settingsInvoke();
    render(<SettingsApp client={new SettingsClient(invoke as unknown as InvokeFunction)} />);
    await screen.findByRole("heading", { name: "General" });
    await userEvent.click(screen.getByRole("button", { name: /Skills \/ MCP/ }));
    await userEvent.click(screen.getByRole("tab", { name: /^MCP/ }));

    expect(screen.getByRole("tab", { name: "Installed" })).toBeVisible();
    await userEvent.click(screen.getByRole("tab", { name: "Store" }));
    expect(screen.queryByText("Taste Skill")).not.toBeInTheDocument();
    const entry = screen.getByText("Playwright MCP").closest("section");
    expect(entry).not.toBeNull();
    await userEvent.click(within(entry as HTMLElement).getByRole("button", { name: "Install" }));

    await vi.waitFor(() => expect(rpcRequest(invoke, "mcp.presets.install")?.params).toEqual({
      catalog_id: "playwright",
      credential_ref: null,
      expected_revision: 0,
      idempotency_key: "settings:mcp:preset:playwright:install",
    }));
    expect(rpcRequest(invoke, "mcp.servers.accept")).toBeUndefined();
  });

  it("inspects an external GitHub Skill before installing a managed copy", async () => {
    const invoke = settingsInvoke();
    render(<SettingsApp client={new SettingsClient(invoke as unknown as InvokeFunction)} />);
    await screen.findByRole("heading", { name: "General" });
    await userEvent.click(screen.getByRole("button", { name: /Skills \/ MCP/ }));
    await userEvent.click(screen.getByRole("button", { name: "Add external" }));
    await userEvent.click(screen.getByRole("radio", { name: "GitHub" }));
    await userEvent.type(screen.getByLabelText("Skill source"), "https://github.com/example/fairy-skill");
    await userEvent.click(screen.getByRole("button", { name: "Inspect" }));

    expect(await screen.findByText(/3 files/)).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Install reviewed Skill" }));
    await vi.waitFor(() => expect(rpcRequest(invoke, "skills.import.install")?.params).toEqual(
      expect.objectContaining({
        inspection_token: "inspection-token",
        name: "example-skill",
        version: "1.0.0",
        publisher: "Local user",
      }),
    ));
  });

  it("switches directly between external import and local Skill creation", async () => {
    const invoke = settingsInvoke();
    render(<SettingsApp client={new SettingsClient(invoke as unknown as InvokeFunction)} />);
    await screen.findByRole("heading", { name: "General" });
    await userEvent.click(screen.getByRole("button", { name: /Skills \/ MCP/ }));

    await userEvent.click(screen.getByRole("button", { name: "Add external" }));
    expect(screen.getByText("Fairy copies the reviewed package into managed storage; the source remains unchanged.")).toBeVisible();
    const createButton = screen.getByRole("button", { name: "Create Skill" });
    await userEvent.click(createButton);

    expect(screen.queryByText("Fairy copies the reviewed package into managed storage; the source remains unchanged.")).not.toBeInTheDocument();
    expect(screen.getByText("Build a local, versioned instruction package managed by Fairy.")).toBeVisible();
    expect(createButton).toHaveAttribute("aria-pressed", "true");
    expect(createButton).toHaveAccessibleName("Cancel");
  });

  it("creates a local Skill and imports bounded MCP JSON without raw secrets", async () => {
    const invoke = settingsInvoke();
    render(<SettingsApp client={new SettingsClient(invoke as unknown as InvokeFunction)} />);
    await screen.findByRole("heading", { name: "General" });
    await userEvent.click(screen.getByRole("button", { name: /Skills \/ MCP/ }));
    await userEvent.click(screen.getByRole("button", { name: "Create Skill" }));
    await userEvent.type(screen.getByLabelText("Skill ID"), "review-flow");
    await userEvent.type(screen.getByLabelText("Description"), "Review generated work.");
    await userEvent.type(screen.getByLabelText("Instructions"), "Inspect the artifact and report actionable issues.");
    await userEvent.click(screen.getByRole("button", { name: "Create and install" }));
    await vi.waitFor(() => expect(rpcRequest(invoke, "skills.create")?.params).toEqual(
      expect.objectContaining({ name: "review-flow", version: "1.0.0" }),
    ));

    await userEvent.click(screen.getByRole("tab", { name: /^MCP/ }));
    await userEvent.click(screen.getByRole("button", { name: "Import JSON" }));
    fireEvent.change(screen.getByLabelText("MCP JSON configuration"), {
      target: { value: '{"mcpServers":{"context-helper":{"command":"npx","args":["-y","example-mcp"]}}}' },
    });
    await userEvent.click(screen.getByRole("button", { name: "Review and save" }));
    await vi.waitFor(() => expect(rpcRequest(invoke, "mcp.servers.configure")?.params).toEqual(
      expect.objectContaining({
        server_id: "context-helper",
        command: "npx",
        arguments: ["-y", "example-mcp"],
        environment_refs: {},
      }),
    ));

    const configuredCalls = rpcRequests(invoke, "mcp.servers.configure").length;
    await userEvent.click(screen.getByRole("button", { name: "Import JSON" }));
    fireEvent.change(screen.getByLabelText("MCP JSON configuration"), {
      target: { value: '{"mcpServers":{"unsafe":{"command":"npx","env":{"TOKEN":"secret"}}}}' },
    });
    await userEvent.click(screen.getByRole("button", { name: "Review and save" }));
    expect(await screen.findByText(/Raw env values are not accepted/)).toBeVisible();
    expect(rpcRequests(invoke, "mcp.servers.configure")).toHaveLength(configuredCalls);
  });

  it("configures a stdio MCP server with one argument per line", async () => {
    const invoke = settingsInvoke();
    render(<SettingsApp client={new SettingsClient(invoke as unknown as InvokeFunction)} />);
    await screen.findByRole("heading", { name: "General" });
    await userEvent.click(screen.getByRole("button", { name: /Skills \/ MCP/ }));
    await userEvent.click(screen.getByRole("tab", { name: /^MCP/ }));
    await userEvent.click(screen.getByRole("button", { name: "Add server" }));

    await userEvent.type(screen.getByLabelText("Server ID"), "playwright");
    await userEvent.type(screen.getByLabelText("Display name"), "Playwright");
    await userEvent.type(screen.getByLabelText("Command"), "npx.cmd");
    fireEvent.change(screen.getByLabelText("Arguments (one per line)"), {
      target: { value: "-y\n@playwright/mcp@0.0.78\n--isolated\n--headless\n" },
    });
    await userEvent.click(screen.getByRole("button", { name: "Save server" }));

    await vi.waitFor(() => expect(rpcRequest(invoke, "mcp.servers.configure")?.params)
      .toEqual(expect.objectContaining({
        server_id: "playwright",
        command: "npx.cmd",
        arguments: ["-y", "@playwright/mcp@0.0.78", "--isolated", "--headless"],
      })));
  });

  it("manages archived projects and every conversation type in Recently deleted", async () => {
    const invoke = settingsInvoke({
      archivedProjects: archivedProjectPage.items,
      trashItems: trashPage.items,
    });
    render(<SettingsApp client={new SettingsClient(invoke as unknown as InvokeFunction)} />);

    expect(await screen.findByText("Archived workspace")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Restore" }));
    await vi.waitFor(() => {
      expect(rpcRequest(invoke, "projects.archived.restore")?.params).toEqual({
        project_id: archivedProjectPage.items[0].project.id,
        expected_revision: 4,
      });
    });

    await userEvent.click(screen.getByRole("tab", { name: /Recently deleted/ }));
    expect(screen.getByText("Scratch notes")).toBeVisible();
    expect(screen.getByText("Deleted project chat")).toBeVisible();
    const disabledRestore = screen.getAllByRole("button", { name: "Restore" }).find(
      (button) => button.getAttribute("title") === "Restore the parent project first",
    );
    expect(disabledRestore).toBeDisabled();

    await userEvent.click(screen.getByLabelText("Permanently delete Scratch notes"));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("exclusive Workspace");
    await userEvent.click(screen.getByRole("button", { name: "Permanently delete" }));
    await vi.waitFor(() => {
      expect(rpcRequest(invoke, "trash.items.purge")?.params).toEqual(
        expect.objectContaining({ item_type: "conversation", user_confirmed: true }),
      );
    });

    fireEvent.click(screen.getByRole("checkbox", { name: /^Permanently delete after 30 days/ }));
    await vi.waitFor(() => {
      const update = invoke.mock.calls.find(([command]) => command === "desktop_preferences_update");
      expect((update?.[1] as { input: { preferences: DesktopPreferences } }).input.preferences)
        .toEqual(expect.objectContaining({ trash_auto_purge_30_days: true }));
    });

    await userEvent.click(screen.getByRole("button", { name: "Clear all" }));
    await userEvent.click(screen.getByRole("button", { name: "Permanently delete" }));
    await vi.waitFor(() => {
      expect(rpcRequest(invoke, "trash.items.purge_all")?.params).toEqual({
        user_confirmed: true,
        deleted_before: null,
        maintenance: false,
      });
    });
  });

  it("loads every archived and trash page", async () => {
    const secondArchived: ProjectArchivedItem = {
      ...archivedProjectPage.items[0],
      project: {
        ...archivedProjectPage.items[0].project,
        id: "019f566f-f8b4-7000-8000-000000000093",
        workspace_id: "019f566f-f8b4-7000-8000-000000000093",
        name: "Second archived workspace",
      },
    };
    const secondTrash: TrashItem = {
      ...trashPage.items[0],
      item_id: "019f566f-f8b4-7000-8000-000000000094",
      title: "Second deleted chat",
    };
    const invoke = settingsInvoke({
      archivedProjects: [...archivedProjectPage.items, secondArchived],
      trashItems: [trashPage.items[0], secondTrash],
      historyPageSize: 1,
    });
    render(<SettingsApp client={new SettingsClient(invoke as unknown as InvokeFunction)} />);

    expect(await screen.findByText("Second archived workspace")).toBeVisible();
    await userEvent.click(screen.getByRole("tab", { name: /Recently deleted/ }));
    expect(screen.getByText("Second deleted chat")).toBeVisible();
    expect(rpcRequests(invoke, "projects.archived.list")).toHaveLength(2);
    expect(rpcRequests(invoke, "trash.items.list")).toHaveLength(2);
  });
});

function activeProjectFixture(): Project {
  return {
    ...archivedProjectPage.items[0].project,
    name: "Research Vault",
    archived_at: null,
    metadata_revision: 1,
  };
}

function activeTaskFixture(project: Project): Task {
  return {
    id: "019f566f-f8b4-7000-8000-000000000095",
    project_id: project.id,
    workspace_id: project.workspace_id,
    conversation_id: "019f566f-f8b4-7000-8000-000000000096",
    user_request: "Connect project knowledge",
    operation_mode: "continue_current_chat_draft",
    base_version_id: null,
    execution_target: "local",
    target_version_id: null,
    memory_snapshot_id: null,
    memory_snapshot_hash: null,
    status: "ready",
    display_title: "Connect project knowledge",
    pinned_at: null,
    metadata_revision: 0,
    created_at: "2026-07-12T00:00:00Z",
    updated_at: "2026-07-12T00:00:00Z",
  };
}

function knowledgeSourceFixture(project: Project): KnowledgeSource {
  return {
    id: "019f566f-f8b4-7000-8000-000000000097",
    project_id: project.id,
    display_name: "Product notes",
    display_path: "Notes / Product",
    kind: "obsidian",
    status: "ready",
    revision: 3,
    sync_cursor: 18,
    transport_availability: "local_device_only",
    created_at: "2026-07-12T00:00:00Z",
    updated_at: "2026-07-12T00:01:00Z",
  };
}

function memoryProposalFixture(task: Task): MemoryProposal {
  return {
    id: "019f566f-f8b4-7000-8000-000000000098",
    task_id: task.id,
    conversation_id: task.conversation_id,
    project_id: task.project_id,
    version_id: null,
    content: "The project uses a product-first information architecture.",
    content_hash: "b".repeat(64),
    actor: "assistant",
    authority: "model_suggestion",
    confidence: 0.82,
    proposed_namespace: "project_canonical",
    scan_result: "clean",
    scope_digest: "c".repeat(64),
    sensitivity: "private",
    source_cursor: 27,
    source_event_id: "019f566f-f8b4-7000-8000-000000000099",
    source_type: "model_suggestion",
    status: "pending",
    created_at: "2026-07-12T00:02:00Z",
  };
}

const archivedProjectPage: { items: ProjectArchivedItem[]; next_cursor: null } = {
  items: [{
    project: {
      id: "019f566f-f8b4-7000-8000-000000000090",
      name: "Archived workspace",
      residency: "local_only",
      workspace_id: "019f566f-f8b4-7000-8000-000000000090",
      active_version_id: null,
      active_preview_id: null,
      revision: 0,
      pinned_at: null,
      archived_at: "2026-06-01T00:00:00Z",
      deleted_at: null,
      purged_at: null,
      metadata_revision: 4,
      created_at: "2026-05-01T00:00:00Z",
      updated_at: "2026-06-01T00:00:00Z",
    },
    thread_count: 3,
    archived_at: "2026-06-01T00:00:00Z",
  }],
  next_cursor: null,
};

const trashPage: { items: TrashItem[]; next_cursor: null } = {
  items: [
    {
      item_type: "conversation",
      item_id: "019f566f-f8b4-7000-8000-000000000091",
      title: "Scratch notes",
      source_project_id: null,
      source_project_title: null,
      thread_count: 0,
      deleted_at: "2026-06-02T00:00:00Z",
      estimated_bytes: 4096,
      can_restore: true,
      metadata_revision: 2,
    },
    {
      item_type: "project_conversation",
      item_id: "019f566f-f8b4-7000-8000-000000000092",
      title: "Deleted project chat",
      source_project_id: archivedProjectPage.items[0].project.id,
      source_project_title: "Archived workspace",
      thread_count: 0,
      deleted_at: "2026-06-03T00:00:00Z",
      estimated_bytes: 0,
      can_restore: false,
      metadata_revision: 3,
    },
  ],
  next_cursor: null,
};

function settingsInvoke(options: {
  archivedProjects?: ProjectArchivedItem[];
  trashItems?: TrashItem[];
  historyPageSize?: number;
  projects?: Project[];
  tasks?: Task[];
  knowledgeSources?: KnowledgeSource[];
  memoryProposals?: MemoryProposal[];
  obsidianHealth?: ObsidianConnectorHealth;
  rendererHealth?: PresenceRendererHealth | null;
  localReadiness?: LocalReadinessReport;
  voiceHealth?: VoiceWorkerHealth;
} = {}) {
  let preferences = defaultPreferences();
  let openRouterConfigured = true;
  let voiceHealth = options.voiceHealth ?? unavailableVoiceHealth();
  return vi.fn(async (command: string, args?: Record<string, unknown>) => {
    if (command === "desktop_preferences_get") return preferences;
    if (command === "desktop_preferences_update") {
      const input = args?.input as { preferences: DesktopPreferences };
      preferences = { ...input.preferences, revision: input.preferences.revision + 1 };
      return preferences;
    }
    if (command === "provider_openrouter_status") return {
      configured: openRouterConfigured,
      account_id: openRouterConfigured ? "openrouter-default" : null,
    };
    if (command === "provider_openrouter_delete") {
      openRouterConfigured = false;
      return { configured: false, account_id: null };
    }
    if (command === "pet_renderer_get_health") {
      return options.rendererHealth ?? null;
    }
    if (command === "realtime_local_readiness_get") {
      return options.localReadiness ?? localRuntimeMissingReadiness();
    }
    if (command === "voice_worker_health") return voiceHealth;
    if (command === "voice_worker_prepare") {
      voiceHealth = { ...voiceHealth, status: "ready", model_ready: true, backend: "tensorrt" };
      return voiceHealth;
    }
    if (command === "voice_worker_stop") {
      voiceHealth = { ...voiceHealth, status: "idle", model_ready: false, backend: null };
      return voiceHealth;
    }
    if (command === "open_companion_window") return undefined;
    if (command === "settings_rpc") {
      const request = args?.request as { id: number; method: CoreMethodName; params: Record<string, unknown> };
      const result = request.method === "projects.archived.list"
        ? historyPage(options.archivedProjects ?? [], request.params, options.historyPageSize)
        : request.method === "trash.items.list"
          ? historyPage(options.trashItems ?? [], request.params, options.historyPageSize)
          : request.method === "projects.list"
            ? historyPage(options.projects ?? [], request.params)
            : request.method === "tasks.list"
              ? historyPage(options.tasks ?? [], request.params)
              : request.method === "knowledge.sources.list"
                ? { items: (options.knowledgeSources ?? []).filter((source) => source.project_id === request.params.project_id) }
                : request.method === "memory.proposals.list"
                  ? { items: (options.memoryProposals ?? []).filter((proposal) => proposal.task_id === request.params.task_id) }
                  : request.method === "obsidian.health.get"
                    ? options.obsidianHealth ?? resultFor(request.method, request.params)
                    : resultFor(request.method, request.params);
      return { jsonrpc: "2.0", id: request.id, result };
    }
    throw new Error(`Unexpected command: ${command}`);
  });
}

function idleVoiceHealth(): VoiceWorkerHealth {
  return {
    status: "idle",
    model_repository: "FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
    model_installed: true,
    model_ready: false,
    model_digest: null,
    prompt_ready: true,
    cuda_available: false,
    tensorrt_available: false,
    onnx_cuda_available: false,
    backend: null,
    device_name: null,
    sample_rate: 24_000,
    error_code: null,
  };
}

function unavailableVoiceHealth(): VoiceWorkerHealth {
  return {
    ...idleVoiceHealth(),
    status: "unavailable",
    model_installed: false,
    prompt_ready: false,
    error_code: "VOICE_WORKER_UNAVAILABLE",
  };
}

function localRuntimeMissingReadiness(): LocalReadinessReport {
  const gib = 1_073_741_824;
  return {
    schema_version: 1,
    profile: "auto",
    hardware_cached: false,
    hardware: {
      schema_version: 1,
      windows_supported: true,
      architecture_x64: true,
      avx2_available: true,
      system_total_bytes: 32 * gib,
      disk_available_bytes: 40 * gib,
      adapter: {
        name: "NVIDIA GeForce RTX 4090",
        vendor: "nvidia",
        vendor_id: 0x10de,
        dedicated_vram_bytes: 24 * gib,
        budget_bytes: 22 * gib,
        current_usage_bytes: 2 * gib,
        luid: "00000000:00000001",
      },
      cuda: {
        available: true,
        driver_api_version: 12_080,
        driver_compatible: true,
        device_count: 1,
        matched_device_ordinal: 0,
        adapter_luid_matches: true,
        error_code: null,
      },
      error_code: null,
    },
    model: {
      schema_version: 1,
      sequence: 3,
      phase: "runtime_missing",
      model_version: "4.5-q4-502eec5",
      manifest_digest: "a".repeat(64),
      current_file: null,
      received_bytes: 6_781_995_488,
      total_bytes: 6_781_995_488,
      error_code: "OMNI_RUNTIME_MISSING",
    },
    model_shallow_present: true,
    model_install_required_bytes: 5 * gib,
    runtime: "missing",
    runtime_error_code: "OMNI_RUNTIME_MISSING",
    capability: {
      schema_version: 1,
      static_eligible: true,
      local_beta_eligible: false,
      reason: "runtime_missing",
      available_budget_bytes: 20 * gib,
      required_budget_bytes: 14.5 * gib,
      warnings: [],
    },
  };
}

function historyPage<T>(items: T[], params: Record<string, unknown>, requestedSize?: number) {
  const start = params.cursor === null || params.cursor === undefined ? 0 : Number(params.cursor);
  const pageSize = requestedSize ?? 100;
  const end = Math.min(start + pageSize, items.length);
  return {
    items: items.slice(start, end),
    next_cursor: end < items.length ? String(end) : null,
  };
}

function rpcRequests(invoke: ReturnType<typeof settingsInvoke>, method: CoreMethodName) {
  return invoke.mock.calls.filter(([command, args]) =>
    command === "settings_rpc" &&
    (args as { request?: { method?: CoreMethodName } } | undefined)?.request?.method === method
  );
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
    case "memory.settings.get": return {
      enabled: true,
      retention_days: 365,
      export_to_obsidian: false,
      sync_normalized_content: false,
      revision: 0,
      updated_at: "2026-07-12T00:00:00Z",
    };
    case "memory.settings.update": return {
      enabled: params.enabled,
      retention_days: params.retention_days,
      export_to_obsidian: params.export_to_obsidian,
      sync_normalized_content: params.sync_normalized_content,
      revision: Number(params.expected_revision) + 1,
      updated_at: "2026-07-12T00:00:01Z",
    };
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
      source_kind: "curated",
      trust: "verified_publisher",
      tags: ["design", "frontend"],
      requirements: [],
    }, {
      extension_id: "gsap-scrolltrigger",
      kind: "skill",
      name: "GSAP ScrollTrigger",
      description: "Official scroll animation guidance.",
      publisher: "GreenSock",
      version: "1.0.0+aed9cfd",
      source: "https://github.com/greensock/gsap-skills",
      license: "MIT",
      experimental: false,
      installed: false,
      source_kind: "curated",
      trust: "verified_publisher",
      tags: ["animation", "frontend"],
      requirements: [],
    }, {
      extension_id: "playwright",
      kind: "mcp_preset",
      name: "Playwright MCP",
      description: "Official browser automation server.",
      publisher: "Microsoft",
      version: "0.0.78",
      source: "https://github.com/microsoft/playwright-mcp",
      license: "Apache-2.0",
      experimental: false,
      installed: false,
      source_kind: "curated",
      trust: "verified_publisher",
      tags: ["browser", "testing"],
      requirements: ["Node.js 20 or newer"],
    }] };
    case "skills.import.inspect": return {
      inspection_token: "inspection-token",
      name: "example-skill",
      description: "Imported example Skill.",
      version: null,
      publisher: null,
      license: null,
      source: "https://github.com/example/fairy-skill",
      has_manifest: false,
      file_count: 3,
      content_bytes: 4096,
    };
    case "skills.list": return { items: [], next_cursor: null };
    case "mcp.servers.list": return { items: [], next_cursor: null };
    case "tasks.list": return { items: [], next_cursor: null };
    case "projects.list": return { items: [], next_cursor: null };
    case "knowledge.sources.list": return { items: [] };
    case "memory.proposals.list": return { items: [] };
    case "obsidian.health.get": return {
      desktop_installed: true,
      cli_available: true,
      minimum_installer_version: "1.12.7",
      status: "ready",
      public_summary: "Obsidian Desktop and CLI are ready",
    };
    case "projects.archived.list": return { items: [], next_cursor: null };
    case "trash.items.list": return { items: [], next_cursor: null };
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

function nativeRendererHealth(): PresenceRendererHealth {
  return {
    requested_mode: "liquid",
    mode: "native",
    actual_backend: "native_liquid_glass",
    optics_source: "desktop_duplication",
    status: "running",
    error_code: null,
    fallback_reason: null,
    monitor_refresh_hz: 144,
    effective_fps: 144,
    dda_exclusion: "applied",
    source_format: "rgba16f",
    adapter_luid: "00000000:00000001",
    source_frame_age_ms: 1,
    capture_to_present_p95_ms: 3.2,
    access_lost_count: 0,
    monitor_handoff: "ready",
  };
}

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
    schema_version: 11,
    revision: 0,
    language: "system",
    launch_at_startup: false,
    minimize_to_tray: true,
    theme: "system",
    reduced_motion: false,
    compact_density: false,
    selected_profile_id: "openrouter",
    voice_replies_enabled: true,
    voice_volume_percent: 80,
    voice_rate_percent: 100,
    permission_cloud_profile: "standard",
    analytics_enabled: false,
    realtime_backend: "auto",
    realtime_cloud_provider: "glm_realtime_flash",
    realtime_allow_cloud_fallback: false,
    realtime_activity_profile: "auto",
    realtime_interaction_intensity: "standard",
    realtime_voice_output: "fairy_voice",
    realtime_game_audio_default: false,
    realtime_capture_mode: "selected_window",
    realtime_excluded_applications: [],
    realtime_online_assistance_enabled: false,
    realtime_memory_enabled: true,
    realtime_presence_max_minutes: 240,
    realtime_cloud_daily_limit_minutes: 180,
    realtime_local_keep_warm_minutes: 10,
    trash_auto_purge_30_days: false,
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
    ambient_dialogue_enabled: true,
    ambient_dialogue_voice_enabled: false,
    ambient_generated_dialogue_enabled: false,
    pet_remember_position: true,
    pet_renderer_mode: "auto",
    pet_optics_mode: "standard",
    pet_activation_style: "fluid_response",
    pet_target_fps: 60,
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
