import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { InvokeFunction } from "../core/tauriTransport";
import {
  SettingsClient,
  type LocalReadinessReport,
  type OmniModelInstallPhase,
} from "./client";
import { RealtimeReadinessCard } from "./RealtimeReadinessCard";

vi.mock("@tauri-apps/api/event", () => ({
  listen: vi.fn(async () => vi.fn()),
}));

afterEach(() => cleanup());

describe("RealtimeReadinessCard", () => {
  it.each([
    {
      name: "unsupported hardware",
      report: report({ staticEligible: false, reason: "vram_below12gb" }),
      label: "Unavailable",
    },
    {
      name: "installable model",
      report: report({ phase: "not_installed", shallow: false, reason: "model_missing" }),
      label: "Install model",
    },
    {
      name: "resumable partial",
      report: report({ phase: "partial", shallow: false, reason: "model_missing" }),
      label: "Resume install",
    },
    {
      name: "corrupt staging",
      report: report({ phase: "corrupt", shallow: false, reason: "model_verification_failed" }),
      label: "Repair required",
    },
    {
      name: "runtime missing",
      report: report({ phase: "runtime_missing", runtime: "missing", reason: "runtime_missing" }),
      label: "Runtime required",
    },
    {
      name: "self-test failed",
      report: report({ phase: "self_test_failed", runtime: "failed", reason: "self_test_failed" }),
      label: "Self-test failed",
    },
    {
      name: "temporary VRAM pressure",
      report: report({ runtime: "passed", reason: "insufficient_free_vram" }),
      label: "Temporarily unavailable",
    },
    {
      name: "ready",
      report: report({ runtime: "passed", reason: "eligible", ready: true }),
      label: "Ready",
    },
  ])("renders $name without inferring a stronger state", async ({ report: value, label }) => {
    renderCard(value);
    await waitFor(() => {
      expect(screen.getByRole("status", { name: "Local readiness status" }))
        .toHaveTextContent(label);
    });
  });

  it("explains the nominal 12 GB dedicated VRAM hardware floor", async () => {
    renderCard(report({ staticEligible: false, reason: "vram_below12gb" }));

    expect(await screen.findByText(
      "Local Beta requires an NVIDIA GPU with at least 12 GB dedicated VRAM.",
    )).toBeInTheDocument();
  });

  it("describes live VRAM pressure as temporary GPU use", async () => {
    renderCard(report({ runtime: "passed", reason: "insufficient_free_vram" }));

    expect(await screen.findByText(
      "Close GPU-heavy applications, then refresh and retry.",
    )).toBeInTheDocument();
    expect(screen.queryByText(/lighter activity profile/i)).not.toBeInTheDocument();
  });

  it("shows bounded numeric progress and cancels the active operation", async () => {
    const downloading = report({
      phase: "downloading",
      shallow: false,
      reason: "model_missing",
      receivedBytes: 50,
      totalBytes: 100,
    });
    const invoke = renderCard(downloading);

    expect(await screen.findByRole("progressbar", { name: "Local model operation progress" }))
      .toHaveAttribute("aria-valuenow", "50");
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(invoke.mock.calls.some(([command]) => command === "omni_model_install_cancel")).toBe(true);
  });

  it("starts an explicit install only after the user presses the action", async () => {
    const installable = report({
      phase: "not_installed",
      shallow: false,
      reason: "model_missing",
    });
    const invoke = renderCard(installable);

    expect(invoke.mock.calls.some(([command]) => command === "omni_model_install_start")).toBe(false);
    await userEvent.click(await screen.findByRole("button", { name: /Install model/ }));
    expect(invoke).toHaveBeenCalledWith("omni_model_install_start", {
      input: { profile: "auto" },
    });
  });

  it("requires confirmation before removing a verified model", async () => {
    const invoke = renderCard(report({
      phase: "runtime_missing",
      runtime: "missing",
      reason: "runtime_missing",
    }));
    await screen.findByText("Runtime required");

    await userEvent.click(screen.getByRole("button", { name: "Remove local MiniCPM-o model" }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("Cloud Live settings and saved chats are unchanged");
    expect(invoke.mock.calls.some(([command]) => command === "omni_model_remove")).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Remove model" }));
    expect(invoke.mock.calls.some(([command]) => command === "omni_model_remove")).toBe(true);
  });

  it("publishes readiness only for the complete eligible report", async () => {
    const onReadinessChange = vi.fn();
    renderCard(report({ runtime: "passed", reason: "eligible", ready: true }), onReadinessChange);
    await screen.findByText("Ready");
    expect(onReadinessChange).toHaveBeenLastCalledWith(true);
  });
});

function renderCard(value: LocalReadinessReport, onReadinessChange = vi.fn()) {
  const invoke = vi.fn(async (command: string) => {
    if (command === "realtime_local_readiness_get") return value;
    if ([
      "omni_model_install_start",
      "omni_model_install_cancel",
      "omni_model_verify",
      "omni_model_remove",
    ].includes(command)) {
      return value.model;
    }
    throw new Error(`Unexpected command: ${command}`);
  });
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <RealtimeReadinessCard
        client={new SettingsClient(invoke as unknown as InvokeFunction)}
        profile="auto"
        onReadinessChange={onReadinessChange}
      />
    </QueryClientProvider>,
  );
  return invoke;
}

function report(options: {
  phase?: OmniModelInstallPhase;
  runtime?: LocalReadinessReport["runtime"];
  reason?: LocalReadinessReport["capability"]["reason"];
  staticEligible?: boolean;
  ready?: boolean;
  shallow?: boolean;
  receivedBytes?: number;
  totalBytes?: number;
} = {}): LocalReadinessReport {
  const gib = 1_073_741_824;
  const phase = options.phase ?? "runtime_missing";
  const runtime = options.runtime ?? "missing";
  const totalBytes = options.totalBytes ?? 6_781_995_488;
  const receivedBytes = options.receivedBytes
    ?? (["not_installed", "partial"].includes(phase) ? 0 : totalBytes);
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
        available: options.staticEligible !== false,
        driver_api_version: 12_080,
        driver_compatible: options.staticEligible !== false,
        device_count: 1,
        matched_device_ordinal: 0,
        adapter_luid_matches: options.staticEligible !== false,
        error_code: null,
      },
      error_code: null,
    },
    model: {
      schema_version: 1,
      sequence: 4,
      phase,
      model_version: "4.5-q4-502eec5",
      manifest_digest: "a".repeat(64),
      current_file: phase === "downloading" ? "MiniCPM-o-4_5-Q4_K_M.gguf" : null,
      received_bytes: receivedBytes,
      total_bytes: totalBytes,
      error_code: phase === "corrupt" ? "MODEL_ARTIFACT_DIGEST_MISMATCH" : null,
    },
    model_shallow_present: options.shallow ?? !["not_installed", "partial", "corrupt", "downloading"].includes(phase),
    model_install_required_bytes: options.shallow === false ? totalBytes + 7 * gib : 5 * gib,
    runtime,
    runtime_error_code: runtime === "missing"
      ? "OMNI_RUNTIME_MISSING"
      : runtime === "failed"
        ? "OMNI_SELF_TEST_CRASHED"
        : null,
    capability: {
      schema_version: 1,
      static_eligible: options.staticEligible ?? true,
      local_beta_eligible: options.ready ?? false,
      reason: options.reason ?? "runtime_missing",
      available_budget_bytes: 20 * gib,
      required_budget_bytes: 15 * gib,
      warnings: [],
    },
  };
}
