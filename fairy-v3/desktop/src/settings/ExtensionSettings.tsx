import { Blocks, Check, Plus, RefreshCw, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import type { McpServer, McpToolPolicyInput, Skill } from "../core/client";
import type { McpServerDraft } from "./extensionTypes";
import "./extensionSettings.css";

interface ExtensionSettingsProps {
  skills: Skill[];
  servers: McpServer[];
  disabled: boolean;
  discoveryAvailable: boolean;
  onConfigure(input: McpServerDraft): Promise<void>;
  onDiscover(serverId: string): Promise<void>;
  onAccept(serverId: string, tools: McpToolPolicyInput[]): Promise<void>;
  onEnabledChange(serverId: string, enabled: boolean): Promise<void>;
  onDelete(serverId: string): Promise<void>;
}

type ExtensionTab = "skills" | "mcp";

export function ExtensionSettings({
  skills,
  servers,
  disabled,
  discoveryAvailable,
  onConfigure,
  onDiscover,
  onAccept,
  onEnabledChange,
  onDelete,
}: ExtensionSettingsProps) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<ExtensionTab>("skills");

  return (
    <div className="execution-control extension-control">
      <button
        className={`icon-button ${open ? "active" : ""}`}
        type="button"
        aria-label="Extensions"
        title="Extensions"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        <Blocks size={16} />
      </button>
      {open ? (
        <aside className="execution-settings extension-settings" aria-label="Extensions panel">
          <header>
            <div>
              <span className="eyebrow">Registry</span>
              <h2>Extensions</h2>
            </div>
            <button
              className="icon-button"
              type="button"
              aria-label="Close extensions"
              title="Close extensions"
              onClick={() => setOpen(false)}
            >
              <X size={16} />
            </button>
          </header>
          <div className="extension-tabs" role="tablist" aria-label="Extension type">
            <button
              type="button"
              role="tab"
              aria-selected={tab === "skills"}
              className={tab === "skills" ? "active" : ""}
              onClick={() => setTab("skills")}
            >
              Skills <span>{skills.length}</span>
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === "mcp"}
              className={tab === "mcp" ? "active" : ""}
              onClick={() => setTab("mcp")}
            >
              MCP <span>{servers.length}</span>
            </button>
          </div>
          {tab === "skills" ? (
            <SkillRegistry skills={skills} />
          ) : (
            <McpRegistry
              servers={servers}
              disabled={disabled}
              discoveryAvailable={discoveryAvailable}
              onConfigure={onConfigure}
              onDiscover={onDiscover}
              onAccept={onAccept}
              onEnabledChange={onEnabledChange}
              onDelete={onDelete}
            />
          )}
        </aside>
      ) : null}
    </div>
  );
}

function SkillRegistry({ skills }: { skills: Skill[] }) {
  const ordered = useMemo(
    () => [...skills].sort((left, right) => left.name.localeCompare(right.name)),
    [skills],
  );
  if (ordered.length === 0) {
    return <p className="extension-empty">No governed skills installed</p>;
  }
  return (
    <div className="skill-list">
      {ordered.map((skill) => (
        <section className="skill-row" key={`${skill.name}@${skill.version}`}>
          <div className="extension-row-heading">
            <div>
              <strong>{skill.name}</strong>
              <code>{skill.version}</code>
            </div>
            <span data-status={skill.available ? "ready" : "unavailable"}>
              {skill.available ? "Ready" : "Unavailable"}
            </span>
          </div>
          <p>{skill.description}</p>
          <dl className="extension-metadata">
            <div>
              <dt>Publisher</dt>
              <dd>{skill.provenance.publisher}</dd>
            </div>
            <div>
              <dt>Source</dt>
              <dd>{skill.provenance.source}</dd>
            </div>
            <div>
              <dt>Capabilities</dt>
              <dd>{skill.required_capabilities.join(", ") || "None"}</dd>
            </div>
            <div>
              <dt>MCP</dt>
              <dd>{skill.compatible_mcp_servers.join(", ") || "None"}</dd>
            </div>
          </dl>
        </section>
      ))}
    </div>
  );
}

function McpRegistry({
  servers,
  disabled,
  discoveryAvailable,
  onConfigure,
  onDiscover,
  onAccept,
  onEnabledChange,
  onDelete,
}: Omit<ExtensionSettingsProps, "skills">) {
  const [adding, setAdding] = useState(false);
  return (
    <div className="mcp-registry">
      <div className="extension-actions">
        <span>{servers.length === 0 ? "No servers configured" : `${servers.length} servers`}</span>
        <button
          className="compact-command"
          type="button"
          aria-expanded={adding}
          onClick={() => setAdding((current) => !current)}
        >
          {adding ? <X size={13} /> : <Plus size={13} />}
          {adding ? "Cancel" : "Add server"}
        </button>
      </div>
      {adding ? (
        <McpServerForm
          disabled={disabled}
          onSubmit={async (input) => {
            await onConfigure(input);
            setAdding(false);
          }}
        />
      ) : null}
      <div className="mcp-server-list">
        {servers.map((server) => (
          <McpServerRow
            key={server.server_id}
            server={server}
            disabled={disabled}
            discoveryAvailable={discoveryAvailable}
            onDiscover={onDiscover}
            onAccept={onAccept}
            onEnabledChange={onEnabledChange}
            onDelete={onDelete}
          />
        ))}
      </div>
    </div>
  );
}

function McpServerForm({
  disabled,
  onSubmit,
}: {
  disabled: boolean;
  onSubmit(input: McpServerDraft): Promise<void>;
}) {
  const [transport, setTransport] = useState<McpServerDraft["transport"]>("stdio");
  const [serverId, setServerId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [command, setCommand] = useState("");
  const [argumentsText, setArgumentsText] = useState("");
  const [endpoint, setEndpoint] = useState("");
  const [credentialRef, setCredentialRef] = useState("");
  const [environmentText, setEnvironmentText] = useState("");
  const targetReady = transport === "stdio" ? command.trim() !== "" : endpoint.trim() !== "";

  return (
    <form
      className="mcp-form"
      onSubmit={(event) => {
        event.preventDefault();
        settle(
          onSubmit({
            serverId: serverId.trim(),
            displayName: displayName.trim(),
            transport,
            command: transport === "stdio" ? command.trim() : null,
            arguments: transport === "stdio" ? nonEmptyLines(argumentsText) : [],
            endpoint: transport === "streamable_http" ? endpoint.trim() : null,
            credentialRef: emptyToNull(credentialRef),
            environmentRefs:
              transport === "stdio" ? referenceMap(environmentText) : {},
          }),
        );
      }}
    >
      <fieldset disabled={disabled}>
        <legend>New MCP server</legend>
        <div className="transport-segments">
          <label>
            <input
              type="radio"
              name="mcp-transport"
              checked={transport === "stdio"}
              onChange={() => setTransport("stdio")}
            />
            <span>Local stdio</span>
          </label>
          <label>
            <input
              type="radio"
              name="mcp-transport"
              checked={transport === "streamable_http"}
              onChange={() => setTransport("streamable_http")}
            />
            <span>Streamable HTTP</span>
          </label>
        </div>
        <div className="mcp-form-grid">
          <label>
            <span>Server ID</span>
            <input value={serverId} required onChange={(event) => setServerId(event.target.value)} />
          </label>
          <label>
            <span>Display name</span>
            <input
              value={displayName}
              required
              onChange={(event) => setDisplayName(event.target.value)}
            />
          </label>
          {transport === "stdio" ? (
            <>
              <label className="mcp-form-wide">
                <span>Command</span>
                <input value={command} required onChange={(event) => setCommand(event.target.value)} />
              </label>
              <label>
                <span>Arguments</span>
                <textarea
                  value={argumentsText}
                  rows={3}
                  onChange={(event) => setArgumentsText(event.target.value)}
                />
              </label>
              <label>
                <span>Environment references</span>
                <textarea
                  value={environmentText}
                  rows={3}
                  onChange={(event) => setEnvironmentText(event.target.value)}
                />
              </label>
            </>
          ) : (
            <label className="mcp-form-wide">
              <span>HTTPS endpoint</span>
              <input
                type="url"
                value={endpoint}
                required
                onChange={(event) => setEndpoint(event.target.value)}
              />
            </label>
          )}
          <label className="mcp-form-wide">
            <span>Credential reference</span>
            <input
              value={credentialRef}
              autoComplete="off"
              onChange={(event) => setCredentialRef(event.target.value)}
            />
          </label>
        </div>
        <button
          className="primary-command"
          type="submit"
          disabled={!targetReady || serverId.trim() === "" || displayName.trim() === ""}
        >
          <Plus size={13} /> Configure
        </button>
      </fieldset>
    </form>
  );
}

function McpServerRow({
  server,
  disabled,
  discoveryAvailable,
  onDiscover,
  onAccept,
  onEnabledChange,
  onDelete,
}: {
  server: McpServer;
  disabled: boolean;
  discoveryAvailable: boolean;
  onDiscover(serverId: string): Promise<void>;
  onAccept(serverId: string, tools: McpToolPolicyInput[]): Promise<void>;
  onEnabledChange(serverId: string, enabled: boolean): Promise<void>;
  onDelete(serverId: string): Promise<void>;
}) {
  const [policies, setPolicies] = useState<McpToolPolicyInput[]>(() => defaultPolicies(server));
  const [confirmDelete, setConfirmDelete] = useState(false);
  useEffect(() => setPolicies(defaultPolicies(server)), [server.pending_schema_digest, server]);

  return (
    <section className="mcp-server-row">
      <div className="extension-row-heading">
        <div>
          <strong>{server.display_name}</strong>
          <code>{server.transport === "stdio" ? "STDIO" : "HTTP"}</code>
        </div>
        <span data-status={server.status}>{server.status.replace("_", " ")}</span>
      </div>
      <div className="mcp-server-meta">
        <code>{server.server_id}</code>
        <span>{server.credential_configured ? "Credential configured" : "No credential"}</span>
        <span>r{server.revision}</span>
      </div>
      {server.last_error_code !== null ? (
        <p className="mcp-error" role="status">{server.last_error_code}</p>
      ) : null}
      <div className="mcp-server-actions">
        <button
          className="compact-command"
          type="button"
          disabled={disabled || !discoveryAvailable}
          onClick={() => settle(onDiscover(server.server_id))}
        >
          <RefreshCw size={13} /> Discover
        </button>
        <label className="mcp-enabled-toggle">
          <input
            type="checkbox"
            checked={server.enabled}
            disabled={disabled || server.accepted_schema_digest === null}
            onChange={(event) => settle(onEnabledChange(server.server_id, event.target.checked))}
          />
          <span>Enabled</span>
        </label>
        {confirmDelete ? (
          <div className="delete-confirm">
            <button
              className="danger-command"
              type="button"
              disabled={disabled}
              onClick={() => settle(onDelete(server.server_id))}
            >
              Delete
            </button>
            <button type="button" className="compact-command" onClick={() => setConfirmDelete(false)}>
              Cancel
            </button>
          </div>
        ) : (
          <button
            className="icon-button row-icon"
            type="button"
            aria-label={`Delete ${server.display_name}`}
            title={`Delete ${server.display_name}`}
            disabled={disabled}
            onClick={() => setConfirmDelete(true)}
          >
            <Trash2 size={14} />
          </button>
        )}
      </div>
      {server.pending_tools.length > 0 &&
      server.pending_schema_digest !== server.accepted_schema_digest ? (
        <ToolPolicyReview
          server={server}
          policies={policies}
          disabled={disabled}
          onPolicies={setPolicies}
          onAccept={() => onAccept(server.server_id, policies)}
        />
      ) : null}
    </section>
  );
}

function ToolPolicyReview({
  server,
  policies,
  disabled,
  onPolicies,
  onAccept,
}: {
  server: McpServer;
  policies: McpToolPolicyInput[];
  disabled: boolean;
  onPolicies(policies: McpToolPolicyInput[]): void;
  onAccept(): Promise<void>;
}) {
  const update = (name: string, patch: Partial<McpToolPolicyInput>) => {
    onPolicies(policies.map((policy) => (policy.name === name ? { ...policy, ...patch } : policy)));
  };
  return (
    <div className="tool-policy-review">
      <div className="policy-review-heading">
        <strong>Pending tool review</strong>
        <span>{server.pending_tools.length}</span>
      </div>
      {server.pending_tools.map((tool) => {
        const policy = policies.find((item) => item.name === tool.name);
        if (policy === undefined) return null;
        return (
          <fieldset className="tool-policy-row" key={tool.name} disabled={disabled}>
            <legend>{tool.name}</legend>
            <p>{tool.description}</p>
            <div className="policy-grid">
              <label>
                <span>Effect</span>
                <select
                  value={policy.side_effect}
                  onChange={(event) => update(tool.name, { side_effect: event.target.value as McpToolPolicyInput["side_effect"] })}
                >
                  <option value="none">None</option>
                  <option value="read">Read</option>
                  <option value="write">Write</option>
                  <option value="execute">Execute</option>
                </select>
              </label>
              <label>
                <span>Risk</span>
                <select
                  value={policy.risk_level}
                  onChange={(event) => update(tool.name, { risk_level: event.target.value as McpToolPolicyInput["risk_level"] })}
                >
                  <option value="low">Low</option>
                  <option value="medium">Medium</option>
                  <option value="high">High</option>
                </select>
              </label>
              <label>
                <span>Approval</span>
                <select
                  value={policy.approval_policy}
                  onChange={(event) => update(tool.name, { approval_policy: event.target.value as McpToolPolicyInput["approval_policy"] })}
                >
                  <option value="never">Never</option>
                  <option value="profile">By profile</option>
                  <option value="always">Always</option>
                </select>
              </label>
            </div>
            <div className="policy-checks">
              {(["observe", "standard", "autonomous"] as const).map((profile) => (
                <label key={profile}>
                  <input
                    type="checkbox"
                    checked={policy.profiles.includes(profile)}
                    onChange={(event) => update(tool.name, { profiles: toggleValue(policy.profiles, profile, event.target.checked) })}
                  />
                  <span>{profile}</span>
                </label>
              ))}
              <label>
                <input
                  type="checkbox"
                  checked={policy.idempotent}
                  onChange={(event) => update(tool.name, { idempotent: event.target.checked })}
                />
                <span>Idempotent</span>
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={policy.enabled}
                  onChange={(event) => update(tool.name, { enabled: event.target.checked })}
                />
                <span>Enabled</span>
              </label>
            </div>
          </fieldset>
        );
      })}
      <button className="primary-command" type="button" disabled={disabled} onClick={() => settle(onAccept())}>
        <Check size={13} /> Accept reviewed schema
      </button>
    </div>
  );
}

function defaultPolicies(server: McpServer): McpToolPolicyInput[] {
  return server.pending_tools.map((tool) => ({
    name: tool.name,
    side_effect: "execute",
    risk_level: "high",
    approval_policy: "always",
    profiles: ["standard", "autonomous"],
    idempotent: false,
    enabled: true,
  }));
}

function nonEmptyLines(value: string): string[] {
  return value.split(/\r?\n/u).map((item) => item.trim()).filter(Boolean);
}

function referenceMap(value: string): Record<string, string> {
  return Object.fromEntries(
    nonEmptyLines(value).map((line) => {
      const separator = line.indexOf("=");
      if (separator <= 0 || separator === line.length - 1) {
        throw new Error("Environment references must use NAME=REFERENCE");
      }
      return [line.slice(0, separator).trim(), line.slice(separator + 1).trim()];
    }),
  );
}

function emptyToNull(value: string): string | null {
  const trimmed = value.trim();
  return trimmed === "" ? null : trimmed;
}

function toggleValue<T>(values: T[], value: T, enabled: boolean): T[] {
  return enabled ? [...new Set([...values, value])] : values.filter((item) => item !== value);
}

function settle(operation: Promise<void>): void {
  void operation.catch(() => undefined);
}
