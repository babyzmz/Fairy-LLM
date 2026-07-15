import type {
  ModelCatalogEntry,
  ModelCatalogPage,
  ModelSelectionPreference,
  ProviderHealth,
  ProviderProfile,
} from "../core/client";

export const AUTO_PRIMARY_MODEL_ID = "deepseek/deepseek-v4-pro";

export const CHAT_PROFILE_BY_MODEL_ID: Readonly<Record<string, string>> = {
  "deepseek/deepseek-v4-pro": "openrouter-deepseek-v4-pro",
  "z-ai/glm-5.2": "openrouter-glm-5-2",
  "moonshotai/kimi-k2.7-code": "openrouter-kimi-k2-7-code",
  "nvidia/nemotron-3-ultra-550b-a55b:free": "openrouter-nemotron-free",
  "qwen/qwen3-coder:free": "openrouter-qwen3-coder-free",
};

export function selectedModelId(
  selection: ModelSelectionPreference | null,
): string {
  return selection?.mode === "manual" && typeof selection.model_id === "string"
    ? selection.model_id
    : AUTO_PRIMARY_MODEL_ID;
}

export function selectedCatalogEntry(
  catalog: ModelCatalogPage | null,
  selection: ModelSelectionPreference | null,
): ModelCatalogEntry | null {
  const modelId = selectedModelId(selection);
  return catalog?.items.find((entry) => entry.model_id === modelId) ?? null;
}

export function selectedProfileId(
  selection: ModelSelectionPreference | null,
): string | null {
  return CHAT_PROFILE_BY_MODEL_ID[selectedModelId(selection)] ?? null;
}

export function selectionSupportsVision(
  catalog: ModelCatalogPage | null,
  selection: ModelSelectionPreference | null,
): boolean {
  if (selection?.mode !== "manual") return true;
  return selectedCatalogEntry(catalog, selection)?.input_modalities.includes("image") ?? false;
}

export function selectionBlockReason(input: {
  catalog: ModelCatalogPage | null;
  selection: ModelSelectionPreference | null;
  providers: ProviderProfile[];
  health: ProviderHealth[];
}): string | null {
  const { catalog, selection, providers, health } = input;
  if (catalog?.account.credential_status !== "configured") {
    return "Connect OpenRouter in Model settings before sending.";
  }
  const entry = selectedCatalogEntry(catalog, selection);
  if (selection?.mode === "manual" && entry?.endpoint_kind !== "chat") {
    return `This is a ${mediaLabel(entry?.endpoint_kind)} model. Use Auto for chat or send a compatible generation request.`;
  }
  if (entry?.availability === "unavailable") {
    return entry.unavailable_reason ?? "The selected model is currently unavailable.";
  }
  const profileId = selectedProfileId(selection);
  const profile = providers.find((item) => item.id === profileId);
  const providerHealth = health.find((item) => item.profile_id === profileId);
  if (profile === undefined || !profile.enabled || !profile.credential_configured) {
    return "The selected model is not ready on this device.";
  }
  if (providerHealth?.status === "unavailable") {
    return "The selected model provider is unavailable.";
  }
  return null;
}

function mediaLabel(endpoint: ModelCatalogEntry["endpoint_kind"] | undefined): string {
  switch (endpoint) {
    case "images": return "image generation";
    case "audio": return "music generation";
    case "videos": return "video generation";
    default: return "specialized";
  }
}
