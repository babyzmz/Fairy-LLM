import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ModelCatalogPage, ModelSelectionPreference } from "../core/client";
import { ModelSelector } from "./ModelSelector";

const timestamp = "2026-07-15T00:00:00Z";
const selection: ModelSelectionPreference = {
  mode: "auto",
  model_id: null,
  allow_free_fallback: false,
  zero_data_retention: false,
  revision: 0,
  updated_at: timestamp,
};

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", class {
    observe() {}
    unobserve() {}
    disconnect() {}
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ModelSelector", () => {
  it("reports a rejected model selection without exposing raw errors", async () => {
    const user = userEvent.setup();
    render(<ModelSelector catalog={catalogFixture()} selection={selection}
      onSelect={async () => { throw new Error("private server detail"); }} onOpenSettings={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Model: Auto" }));
    await user.click(screen.getByRole("menuitemradio", { name: /Kimi K2.7 Code/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not update the model selection. Please try again.");
    expect(screen.queryByText("private server detail")).not.toBeInTheDocument();
  });
  it("groups friendly model choices and preserves unavailable entries", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn(async () => undefined);
    render(
      <ModelSelector
        catalog={catalogFixture()}
        selection={selection}
        onSelect={onSelect}
        onOpenSettings={vi.fn(async () => undefined)}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Model: Auto" }));
    expect(screen.getByText("General & reasoning")).toBeVisible();
    expect(screen.getByText("Professional generation")).toBeVisible();
    expect(screen.getAllByText("Free").length).toBeGreaterThan(0);
    expect(screen.queryByText("moonshotai/kimi-k2.7-code")).not.toBeInTheDocument();
    expect(screen.getByRole("menuitemradio", { name: /Seedance 2.0/ })).toHaveAttribute("data-disabled");

    await user.click(screen.getByRole("menuitemradio", { name: /Kimi K2.7 Code/ }));
    expect(onSelect).toHaveBeenCalledWith("manual", "moonshotai/kimi-k2.7-code");
  });

  it("opens model settings from the same in-window menu", async () => {
    const user = userEvent.setup();
    const onOpenSettings = vi.fn(async () => undefined);
    render(
      <ModelSelector
        catalog={catalogFixture()}
        selection={selection}
        onSelect={vi.fn(async () => undefined)}
        onOpenSettings={onOpenSettings}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Model: Auto" }));
    await user.click(screen.getByRole("menuitemradio", { name: "Model settings" }));
    expect(onOpenSettings).toHaveBeenCalledOnce();
  });
});

function catalogFixture(): ModelCatalogPage {
  return {
    account: {
      account_id: "openrouter-default",
      provider_kind: "openrouter",
      display_name: "OpenRouter",
      credential_status: "configured",
    },
    items: [
      entry("deepseek/deepseek-v4-pro", "DeepSeek V4 Pro", "primary", "chat", "available"),
      entry("moonshotai/kimi-k2.7-code", "Kimi K2.7 Code", "code", "chat", "available"),
      entry("bytedance/seedance-2.0", "Seedance 2.0", "video", "videos", "unavailable"),
      entry("qwen/qwen3-coder:free", "Qwen3 Coder", "free_code", "chat", "available", false),
    ],
    fetched_at: timestamp,
    expires_at: timestamp,
    stale: false,
    revision: 1,
    last_error_code: null,
  };
}

function entry(
  modelId: string,
  displayName: string,
  category: ModelCatalogPage["items"][number]["category"],
  endpoint: ModelCatalogPage["items"][number]["endpoint_kind"],
  availability: ModelCatalogPage["items"][number]["availability"],
  paid = true,
): ModelCatalogPage["items"][number] {
  return {
    model_id: modelId,
    display_name: displayName,
    category,
    endpoint_kind: endpoint,
    description: displayName,
    paid,
    availability,
    unavailable_reason: availability === "unavailable" ? "Not available for this account" : null,
    input_modalities: ["text"],
    output_modalities: [endpoint === "chat" ? "text" : endpoint],
    context_length: null,
    max_output_tokens: null,
    supports_tools: endpoint === "chat",
    supports_structured_output: endpoint === "chat",
    supports_streaming: endpoint === "chat",
    supported_resolutions: [],
    supported_aspect_ratios: [],
    prices: [],
  };
}
