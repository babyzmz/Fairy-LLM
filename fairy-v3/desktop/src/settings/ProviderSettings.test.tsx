import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ProviderHealth, ProviderProfile } from "../core/client";
import { ProviderSettings } from "./ProviderSettings";

afterEach(cleanup);

describe("ProviderSettings", () => {
  it("shows credential state without exposing secret material", () => {
    const onDeveloperModeChange = vi.fn();
    render(
      <ProviderSettings
        providers={[provider()]}
        health={[health()]}
        selectedProfileId="openrouter-free"
        developerMode={false}
        onProfileChange={vi.fn()}
        onDeveloperModeChange={onDeveloperModeChange}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Provider settings" }));

    expect(screen.getByText("Credential configured")).toBeVisible();
    expect(document.body.textContent).not.toContain("sk-or-v1-");
    fireEvent.click(screen.getByRole("checkbox", { name: "Developer mode" }));
    expect(onDeveloperModeChange).toHaveBeenCalledWith(true);
  });

  it("configures OpenRouter without retaining or displaying the submitted key", async () => {
    const configure = vi.fn(async () => undefined);
    render(
      <ProviderSettings
        providers={[]}
        health={[]}
        selectedProfileId={null}
        developerMode={false}
        openRouterConfigured={false}
        onProfileChange={vi.fn()}
        onDeveloperModeChange={vi.fn()}
        onConfigureOpenRouter={configure}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Provider settings" }));
    fireEvent.change(screen.getByLabelText("API key"), {
      target: { value: "sk-or-v1-private-test-value" },
    });
    fireEvent.change(screen.getByLabelText("Model ID"), {
      target: { value: "tencent/hy3:free" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save and connect" }));

    expect(configure).toHaveBeenCalledWith(
      "sk-or-v1-private-test-value",
      "tencent/hy3:free",
    );
    await vi.waitFor(() => expect(screen.getByLabelText("API key")).toHaveValue(""));
    expect(document.body.textContent).not.toContain("sk-or-v1-private-test-value");
  });
});

function provider(): ProviderProfile {
  return {
    id: "openrouter-free",
    display_name: "OpenRouter Free",
    kind: "openai_compatible",
    base_url: "https://openrouter.ai/api/v1",
    model_id: "openrouter/free",
    capabilities: ["text", "tools"],
    credential_required: true,
    credential_configured: true,
    enabled: true,
    timeout_seconds: 60,
    fallback_profile_id: null,
  };
}

function health(): ProviderHealth {
  return {
    profile_id: "openrouter-free",
    status: "available",
    error_code: null,
    diagnostics: [],
  };
}
