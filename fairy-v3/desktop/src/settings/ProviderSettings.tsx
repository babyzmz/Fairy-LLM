import { Check, Settings2, X } from "lucide-react";
import { useState } from "react";

import type { ProviderHealth, ProviderProfile } from "../core/client";

interface ProviderSettingsProps {
  providers: ProviderProfile[];
  health: ProviderHealth[];
  selectedProfileId: string | null;
  developerMode: boolean;
  onProfileChange(profileId: string): void;
  onDeveloperModeChange(enabled: boolean): void;
}

export function ProviderSettings({
  providers,
  health,
  selectedProfileId,
  developerMode,
  onProfileChange,
  onDeveloperModeChange,
}: ProviderSettingsProps) {
  const [open, setOpen] = useState(false);
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
