import { ShieldCheck, X } from "lucide-react";
import { useMemo, useState } from "react";

import type {
  CapabilityManifest,
  ExecutionSettings,
  ToolDefinitionMetadata,
} from "../core/client";
import "./executionControls.css";

type PermissionProfile = ExecutionSettings["profile"];

interface ExecutionControlsProps {
  settings: ExecutionSettings | null;
  manifest: CapabilityManifest | null;
  disabled: boolean;
  onProfileChange(profile: PermissionProfile): Promise<void>;
  onCapabilityChange(name: string, enabled: boolean): Promise<void>;
}

const profiles: readonly { value: PermissionProfile; label: string }[] = [
  { value: "observe", label: "Observe" },
  { value: "standard", label: "Standard" },
  { value: "autonomous", label: "Autonomous" },
];

export function ExecutionControls({
  settings,
  manifest,
  disabled,
  onProfileChange,
  onCapabilityChange,
}: ExecutionControlsProps) {
  const [open, setOpen] = useState(false);
  const capabilities = useMemo(
    () =>
      [...(manifest?.command_metadata ?? [])]
        .filter((definition) => definition.model_visible)
        .sort((left, right) => left.name.localeCompare(right.name)),
    [manifest?.command_metadata],
  );

  return (
    <div className="execution-control">
      <button
        className={`icon-button ${open ? "active" : ""}`}
        type="button"
        aria-label="Execution controls"
        title="Execution controls"
        aria-expanded={open}
        disabled={settings === null && disabled}
        onClick={() => setOpen((current) => !current)}
      >
        <ShieldCheck size={16} />
      </button>
      {open ? (
        <aside className="execution-settings" aria-label="Execution controls panel">
          <header>
            <div>
              <span className="eyebrow">Policy</span>
              <h2>Execution controls</h2>
            </div>
            <button
              className="icon-button"
              type="button"
              aria-label="Close execution controls"
              title="Close execution controls"
              onClick={() => setOpen(false)}
            >
              <X size={16} />
            </button>
          </header>
          {settings === null || manifest === null ? (
            <div className="execution-unavailable" role="status">
              Core settings unavailable
            </div>
          ) : (
            <>
              <fieldset className="permission-profiles" disabled={disabled}>
                <legend>Permission profile</legend>
                <div className="permission-segments">
                  {profiles.map((profile) => (
                    <label key={profile.value}>
                      <input
                        type="radio"
                        name="permission-profile"
                        value={profile.value}
                        checked={settings.profile === profile.value}
                        onChange={() => settle(onProfileChange(profile.value))}
                      />
                      <span>{profile.label}</span>
                    </label>
                  ))}
                </div>
              </fieldset>
              <section className="capability-settings" aria-label="Capability toggles">
                <div className="capability-heading">
                  <span>Capabilities</span>
                  <small>{manifest.sandbox_healthy ? "Sandbox ready" : "Sandbox unavailable"}</small>
                </div>
                <div className="capability-list">
                  {capabilities.map((definition) => (
                    <CapabilityToggle
                      key={definition.name}
                      definition={definition}
                      settings={settings}
                      manifest={manifest}
                      disabled={disabled}
                      onChange={onCapabilityChange}
                    />
                  ))}
                </div>
              </section>
            </>
          )}
        </aside>
      ) : null}
    </div>
  );
}

function CapabilityToggle({
  definition,
  settings,
  manifest,
  disabled,
  onChange,
}: {
  definition: ToolDefinitionMetadata;
  settings: ExecutionSettings;
  manifest: CapabilityManifest;
  disabled: boolean;
  onChange(name: string, enabled: boolean): Promise<void>;
}) {
  const allowedByProfile = definition.profiles.includes(settings.profile);
  const sandboxAvailable = !definition.requires_sandbox || manifest.sandbox_healthy;
  const enabledByUser = settings.capability_overrides[definition.name] !== false;
  const effective = manifest.operations[definition.name] === true;
  const status = !allowedByProfile
    ? `Requires ${definition.profiles.join(" or ")}`
    : !sandboxAvailable
      ? "Sandbox unavailable"
      : effective
        ? "Effective"
        : "Disabled";

  return (
    <label className="capability-toggle">
      <span className="capability-copy">
        <strong>{definition.name}</strong>
        <span>{definition.description}</span>
        <small data-effective={effective}>{status}</small>
      </span>
      <input
        type="checkbox"
        aria-label={definition.name}
        checked={enabledByUser}
        disabled={disabled || !allowedByProfile || !sandboxAvailable}
        onChange={(event) =>
          settle(onChange(definition.name, event.target.checked))
        }
      />
    </label>
  );
}

function settle(operation: Promise<void>): void {
  void operation.catch(() => undefined);
}
