import { describe, expect, it } from "vitest";

import type {
  ModelCatalogPage,
  ModelSelectionPreference,
  ProviderHealth,
  ProviderProfile,
} from "../core/client";
import {
  selectedProfileId,
  selectionBlockReason,
  selectionSupportsVision,
} from "./modelSelection";

const timestamp = "2026-07-15T00:00:00Z";
const auto: ModelSelectionPreference = {
  mode: "auto",
  model_id: null,
  allow_free_fallback: false,
  zero_data_retention: false,
  revision: 0,
  updated_at: timestamp,
};
const profile: ProviderProfile = {
  id: "openrouter-deepseek-v4-pro",
  display_name: "DeepSeek V4 Pro",
  kind: "openai_compatible",
  base_url: "https://openrouter.ai/api/v1",
  model_id: "deepseek/deepseek-v4-pro",
  capabilities: ["text", "tools", "structured_output"],
  credential_required: true,
  credential_configured: true,
  fallback_profile_id: null,
  timeout_seconds: 180,
  enabled: true,
};
const health: ProviderHealth = {
  profile_id: profile.id,
  status: "available",
  error_code: null,
  diagnostics: [],
};
const catalog = catalogFixture("configured");

describe("model selection projection", () => {
  it("maps Auto and approved text models to fixed execution profiles", () => {
    expect(selectedProfileId(auto)).toBe("openrouter-deepseek-v4-pro");
    expect(selectedProfileId({ ...auto, mode: "manual", model_id: "z-ai/glm-5.2" }))
      .toBe("openrouter-glm-5-2");
    expect(selectedProfileId({ ...auto, mode: "manual", model_id: "google/lyria-3-pro-preview" }))
      .toBeNull();
  });

  it("keeps Auto vision-capable and respects manual input modalities", () => {
    expect(selectionSupportsVision(catalog, auto)).toBe(true);
    expect(selectionSupportsVision(catalog, {
      ...auto,
      mode: "manual",
      model_id: "deepseek/deepseek-v4-pro",
    })).toBe(false);
  });

  it("blocks missing credentials and media models without hiding the selection", () => {
    expect(selectionBlockReason({
      catalog: catalogFixture("unavailable"),
      selection: auto,
      providers: [profile],
      health: [health],
    })).toContain("Connect OpenRouter");
    expect(selectionBlockReason({
      catalog,
      selection: { ...auto, mode: "manual", model_id: "google/lyria-3-pro-preview" },
      providers: [profile],
      health: [health],
    })).toContain("music generation");
    expect(selectionBlockReason({
      catalog,
      selection: auto,
      providers: [profile],
      health: [health],
    })).toBeNull();
  });
});

function catalogFixture(
  credentialStatus: ModelCatalogPage["account"]["credential_status"],
): ModelCatalogPage {
  return {
    account: {
      account_id: "openrouter-default",
      provider_kind: "openrouter",
      display_name: "OpenRouter",
      credential_status: credentialStatus,
    },
    items: [
      entry("deepseek/deepseek-v4-pro", "DeepSeek V4 Pro", "primary", "chat", ["text"]),
      entry("google/lyria-3-pro-preview", "Lyria 3 Pro Preview", "music", "audio", ["text"]),
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
  inputs: string[],
): ModelCatalogPage["items"][number] {
  return {
    model_id: modelId,
    display_name: displayName,
    category,
    endpoint_kind: endpoint,
    description: displayName,
    paid: true,
    availability: "available",
    unavailable_reason: null,
    input_modalities: inputs,
    output_modalities: [endpoint === "chat" ? "text" : endpoint],
    context_length: endpoint === "chat" ? 131072 : null,
    max_output_tokens: endpoint === "chat" ? 16384 : null,
    supports_tools: endpoint === "chat",
    supports_structured_output: endpoint === "chat",
    supports_streaming: endpoint === "chat",
    supported_resolutions: [],
    supported_aspect_ratios: [],
    prices: [],
  };
}
