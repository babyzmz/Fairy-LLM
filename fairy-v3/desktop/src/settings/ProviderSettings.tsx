import { Check, KeyRound, Settings2, Trash2, X } from "lucide-react";
import { useState } from "react";

import type { ProviderHealth, ProviderProfile } from "../core/client";
import "./provider-settings.css";

interface ProviderSettingsProps {
  providers: ProviderProfile[];
  health: ProviderHealth[];
  selectedProfileId: string | null;
  developerMode: boolean;
  openRouterConfigured?: boolean;
  openRouterModelId?: string | null;
  busy?: boolean;
  onProfileChange(profileId: string): void;
  onDeveloperModeChange(enabled: boolean): void;
  onConfigureOpenRouter?(apiKey: string, modelId: string): Promise<void>;
  onDeleteOpenRouter?(): Promise<void>;
}

export function ProviderSettings({
  providers,
  health,
  selectedProfileId,
  developerMode,
  openRouterConfigured = false,
  openRouterModelId = null,
  busy = false,
  onProfileChange,
  onDeveloperModeChange,
  onConfigureOpenRouter,
  onDeleteOpenRouter,
}: ProviderSettingsProps) {
  const [open, setOpen] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [modelId, setModelId] = useState(
    openRouterModelId ?? "tencent/hy3:free",
  );
  const statuses = new Map(health.map((item) => [item.profile_id, item]));

  return (
    <div className="provider-control">
      <label className="compact-select provider-select">
        <span className="sr-only">Select model provider</span>
        <select
          aria-label="Select model provider"
          value={selectedProfileId ?? ""}
          disabled={providers.length === 0}
          onChange={(event) => onProfileChange(event.target.value)}
        >
          {providers.length === 0 ? <option value="">No provider</option> : null}
          {providers.map((provider) => (
            <option key={provider.id} value={provider.id} disabled={!provider.enabled}>
              {provider.display_name}
            </option>
          ))}
        </select>
      </label>
      <button
        className={`icon-button ${open ? "active" : ""}`}
        type="button"
        aria-label="Provider settings"
        title="Provider settings"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        <Settings2 size={16} />
      </button>
      {open ? (
        <aside className="provider-settings" aria-label="Provider settings panel">
          <header>
            <div>
              <span className="eyebrow">Models</span>
              <h2>Provider settings</h2>
            </div>
            <button
              className="icon-button"
              type="button"
              aria-label="Close provider settings"
              title="Close provider settings"
              onClick={() => setOpen(false)}
            >
              <X size={16} />
            </button>
          </header>
          <div className="provider-list">
            {providers.map((provider) => {
              const itemHealth = statuses.get(provider.id);
              const available = itemHealth?.status !== "unavailable";
              return (
                <button
                  className={provider.id === selectedProfileId ? "selected" : ""}
                  type="button"
                  key={provider.id}
                  disabled={!provider.enabled}
                  onClick={() => onProfileChange(provider.id)}
                >
                  <span className={`provider-health provider-health-${itemHealth?.status ?? "unknown"}`} />
                  <span>
                    <strong>{provider.display_name}</strong>
                    <small>{provider.model_id}</small>
                  </span>
                  <span className="provider-credential">
                    {provider.credential_required
                      ? provider.credential_configured
                        ? "Credential configured"
                        : "Credential missing"
                      : "No credential required"}
                  </span>
                  {provider.id === selectedProfileId && available ? <Check size={15} /> : null}
                </button>
              );
            })}
          </div>
          {onConfigureOpenRouter ? (
            <form
              className="provider-setup"
              onSubmit={(event) => {
                event.preventDefault();
                void onConfigureOpenRouter(apiKey, modelId).then(() => setApiKey(""));
              }}
            >
              <div className="provider-setup-heading">
                <KeyRound size={15} />
                <strong>OpenRouter</strong>
                <span>{openRouterConfigured ? "Configured" : "Not configured"}</span>
              </div>
              <label>
                <span>API key</span>
                <input
                  type="password"
                  autoComplete="off"
                  value={apiKey}
                  placeholder={openRouterConfigured ? "Enter a new key to replace" : "sk-or-v1-..."}
                  required
                  disabled={busy}
                  onChange={(event) => setApiKey(event.target.value)}
                />
              </label>
              <label>
                <span>Model ID</span>
                <input
                  list="openrouter-free-models"
                  value={modelId}
                  required
                  disabled={busy}
                  onChange={(event) => setModelId(event.target.value)}
                />
                <datalist id="openrouter-free-models">
                  <option value="tencent/hy3:free" />
                  <option value="nvidia/nemotron-3-ultra-550b-a55b:free" />
                </datalist>
              </label>
              <div className="provider-setup-actions">
                <button className="primary-command" type="submit" disabled={busy || apiKey.trim() === ""}>
                  <KeyRound size={14} /> Save and connect
                </button>
                {openRouterConfigured && onDeleteOpenRouter ? (
                  <button
                    className="icon-button"
                    type="button"
                    aria-label="Remove OpenRouter credential"
                    title="Remove OpenRouter credential"
                    disabled={busy}
                    onClick={() => void onDeleteOpenRouter()}
                  >
                    <Trash2 size={15} />
                  </button>
                ) : null}
              </div>
            </form>
          ) : null}
          <label className="developer-toggle">
            <input
              type="checkbox"
              checked={developerMode}
              onChange={(event) => onDeveloperModeChange(event.target.checked)}
            />
            <span>Developer mode</span>
          </label>
        </aside>
      ) : null}
    </div>
  );
}
