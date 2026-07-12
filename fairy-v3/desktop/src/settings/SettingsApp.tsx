import {
  Bot,
  Brain,
  Check,
  ChevronRight,
  Eye,
  KeyRound,
  Languages,
  Mic2,
  MonitorCog,
  Palette,
  PawPrint,
  Play,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Trash2,
  Volume2,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { m } from "motion/react";

import type {
  CapabilityManifest,
  ExecutionSettings,
  McpServer,
  McpToolPolicyInput,
  ProviderHealth,
  ProviderProfile,
  Skill,
} from "../core/client";
import { startNativeVoiceTest } from "../voice/nativeVoice";
import {
  applyDesktopPreferences,
  type DesktopPreferences,
  type SettingsClient,
  type VoiceWorkerHealth,
} from "./client";
import "./settings-app.css";

type CategoryId =
  | "general"
  | "appearance"
  | "models"
  | "voice"
  | "permissions"
  | "extensions"
  | "knowledge"
  | "pet"
  | "advanced";

const categories: readonly {
  id: CategoryId;
  label: string;
  keywords: string;
  icon: typeof MonitorCog;
}[] = [
  { id: "general", label: "General", keywords: "startup tray language", icon: MonitorCog },
  { id: "appearance", label: "Appearance", keywords: "theme motion density animation", icon: Palette },
  { id: "models", label: "Models", keywords: "provider openrouter api key model", icon: Bot },
  { id: "voice", label: "Voice", keywords: "tts speech volume rate autoplay", icon: Mic2 },
  { id: "permissions", label: "Execution permissions", keywords: "observe standard autonomous sandbox capability cloud", icon: ShieldCheck },
  { id: "extensions", label: "Skills / MCP", keywords: "skills tools servers extension", icon: Sparkles },
  { id: "knowledge", label: "Knowledge & privacy", keywords: "memory retention analytics privacy", icon: Brain },
  { id: "pet", label: "Pet", keywords: "presence companion mute always top", icon: PawPrint },
  { id: "advanced", label: "Advanced", keywords: "developer diagnostics logs", icon: SlidersHorizontal },
];

interface SettingsData {
  preferences: DesktopPreferences;
  providers: ProviderProfile[];
  health: ProviderHealth[];
  openRouterConfigured: boolean;
  openRouterModelId: string | null;
  permissions: ExecutionSettings;
  capabilities: CapabilityManifest;
  skills: Skill[];
  servers: McpServer[];
  voiceHealth: VoiceWorkerHealth;
}

export function SettingsApp({ client }: { client: SettingsClient }) {
  const [category, setCategory] = useState<CategoryId>("general");
  const [query, setQuery] = useState("");
  const [data, setData] = useState<SettingsData | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [preferences, providers, health, status, permissions, capabilities, skills, servers, voiceHealth] =
        await Promise.all([
          client.preferences.get(),
          client.providers.list(),
          client.providers.health(),
          client.providers.openRouterStatus(),
          client.permissions.get(),
          client.permissions.capabilities(),
          client.extensions.skills(),
          client.extensions.servers(),
          client.voice.health().catch(() => unavailableVoiceHealth()),
        ]);
      setData({
        preferences,
        providers: providers.items,
        health: health.items,
        openRouterConfigured: status.configured,
        openRouterModelId: status.model_id,
        permissions,
        capabilities,
        skills: skills.items,
        servers: servers.items,
        voiceHealth,
      });
      applyDesktopPreferences(preferences);
    } catch (caught) {
      setError(messageOf(caught));
    }
  }, [client]);

  useEffect(() => {
    void load();
  }, [load]);

  const act = useCallback(async (operation: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await operation();
    } catch (caught) {
      const message = messageOf(caught);
      const code = typeof caught === "object" && caught !== null && "errorCode" in caught
        ? String(caught.errorCode)
        : "";
      if (message.includes("REVISION_CONFLICT") || code.includes("VERSION_CONFLICT")) {
        await load();
        setError("Settings changed on another device. Latest values loaded; review and retry.");
      } else {
        setError(message);
      }
    } finally {
      setBusy(false);
    }
  }, [load]);

  const updatePreferences = useCallback(
    async (patch: Partial<DesktopPreferences>) => {
      if (data === null) return;
      await act(async () => {
        const saved = await client.preferences.update({ ...data.preferences, ...patch });
        setData((current) => current === null ? null : { ...current, preferences: saved });
        applyDesktopPreferences(saved);
      });
    },
    [act, client.preferences, data],
  );

  const normalizedQuery = query.trim().toLocaleLowerCase();
  const visibleCategories = useMemo(
    () => categories.filter((item) =>
      normalizedQuery === "" || `${item.label} ${item.keywords}`.toLocaleLowerCase().includes(normalizedQuery),
    ),
    [normalizedQuery],
  );
  const visibleCategory = normalizedQuery === ""
    ? category
    : visibleCategories.some((item) => item.id === category)
      ? category
      : visibleCategories[0]?.id ?? category;

  return (
    <main className="settings-shell" aria-label="Fairy settings">
      <aside className="settings-navigation">
        <header className="settings-brand">
          <span className="settings-mark" aria-hidden="true" />
          <div><strong>Fairy</strong><span>Settings</span></div>
        </header>
        <label className="settings-search">
          <Search size={15} />
          <span className="sr-only">Search settings</span>
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search settings" />
          {query ? <button type="button" aria-label="Clear search" onClick={() => setQuery("")}><X size={13} /></button> : null}
        </label>
        <nav aria-label="Settings categories">
          {visibleCategories.map((item) => {
            const Icon = item.icon;
            return <button key={item.id} type="button" className={visibleCategory === item.id ? "active" : ""} onClick={() => { setCategory(item.id); setQuery(""); }}>
              <Icon size={16} /><span>{item.label}</span><ChevronRight size={13} />
            </button>;
          })}
        </nav>
        <button className="settings-refresh" type="button" disabled={busy} onClick={() => void load()}>
          <RefreshCw size={14} /> Refresh
        </button>
      </aside>

      <m.section className="settings-content" key={visibleCategory} initial={{ opacity: 0.4, x: 8 }} animate={{ opacity: 1, x: 0 }}>
        {error ? <div className="settings-error" role="alert"><span>{error}</span><button type="button" onClick={() => setError(null)}><X size={14} /></button></div> : null}
        {data === null ? <div className="settings-loading" role="status"><span className="settings-spinner" />Connecting to Fairy Core</div> : (
          <SettingsCategory
            id={visibleCategory}
            data={data}
            client={client}
            busy={busy}
            act={act}
            reload={load}
            updatePreferences={updatePreferences}
            updateData={setData}
          />
        )}
      </m.section>
    </main>
  );
}

function SettingsCategory(props: {
  id: CategoryId;
  data: SettingsData;
  client: SettingsClient;
  busy: boolean;
  act(operation: () => Promise<void>): Promise<void>;
  reload(): Promise<void>;
  updatePreferences(patch: Partial<DesktopPreferences>): Promise<void>;
  updateData: React.Dispatch<React.SetStateAction<SettingsData | null>>;
}) {
  const { id, data, busy, updatePreferences } = props;
  switch (id) {
    case "general": return <Category title="General" subtitle="Desktop behavior">
      <SettingSelect icon={<Languages size={17} />} label="Language" value={data.preferences.language} disabled={busy} onChange={(value) => void updatePreferences({ language: value as DesktopPreferences["language"] })} options={[{ value: "system", label: "System default" }, { value: "en", label: "English" }, { value: "zh-CN", label: "简体中文" }]} />
      <SettingToggle label="Launch at startup" checked={data.preferences.launch_at_startup} disabled={busy} onChange={(value) => void updatePreferences({ launch_at_startup: value })} />
      <SettingToggle label="Minimize to notification area" checked={data.preferences.minimize_to_tray} disabled={busy} onChange={(value) => void updatePreferences({ minimize_to_tray: value })} />
    </Category>;
    case "appearance": return <Category title="Appearance" subtitle="Theme and motion">
      <SettingSelect icon={<Palette size={17} />} label="Theme" value={data.preferences.theme} disabled={busy} onChange={(value) => void updatePreferences({ theme: value as DesktopPreferences["theme"] })} options={[{ value: "system", label: "System" }, { value: "dark", label: "Dark" }, { value: "light", label: "Light" }]} />
      <SettingToggle label="Reduce motion" checked={data.preferences.reduced_motion} disabled={busy} onChange={(value) => void updatePreferences({ reduced_motion: value })} />
      <SettingToggle label="Compact density" checked={data.preferences.compact_density} disabled={busy} onChange={(value) => void updatePreferences({ compact_density: value })} />
    </Category>;
    case "models": return <ModelsPanel {...props} />;
    case "voice": return <Category title="Voice" subtitle="Playback and speech output">
      <SettingToggle label="Auto-play in main chat" checked={data.preferences.voice_auto_play_chat} disabled={busy} onChange={(value) => void updatePreferences({ voice_auto_play_chat: value })} />
      <SettingToggle label="Auto-play pet replies" checked={data.preferences.voice_auto_play_pet} disabled={busy} onChange={(value) => void updatePreferences({ voice_auto_play_pet: value })} />
      <SettingRange label="Volume" value={data.preferences.voice_volume_percent} min={0} max={100} suffix="%" disabled={busy} onCommit={(value) => void updatePreferences({ voice_volume_percent: value })} />
      <SettingRange label="Speech rate" value={data.preferences.voice_rate_percent} min={50} max={200} suffix="%" disabled={busy} onCommit={(value) => void updatePreferences({ voice_rate_percent: value })} />
      <HealthRow
        icon={<Volume2 size={17} />}
        label="Fairy Voice Worker"
        status={voiceHealthLabel(data.voiceHealth)}
        tone={data.voiceHealth.status === "ready" ? "success" : data.voiceHealth.status === "warming" ? "neutral" : "error"}
      />
      <div className="settings-section-command">
        <span>{data.voiceHealth.device_name ?? "A CUDA GPU is required"}</span>
        {data.voiceHealth.status === "ready" ? (
          <button className="secondary-command" type="button" disabled={busy} onClick={() => void props.act(async () => {
            const playback = await startNativeVoiceTest();
            await playback.finished;
          })}>
            <Play size={14} /> Test Fairy voice
          </button>
        ) : data.voiceHealth.status === "warming" ? null : (
          <button className="secondary-command" type="button" disabled={busy || !data.voiceHealth.cuda_available} onClick={() => void props.act(async () => {
            await props.client.voice.installModel();
            await props.reload();
          })}>
            <RefreshCw size={14} /> {data.voiceHealth.model_installed ? "Retry voice runtime" : "Install voice model"}
          </button>
        )}
      </div>
    </Category>;
    case "permissions": return <PermissionsPanel {...props} />;
    case "extensions": return <ExtensionsPanel {...props} />;
    case "knowledge": return <Category title="Knowledge & privacy" subtitle="Memory and local data">
      <SettingToggle label="Use durable memory" checked={data.preferences.memory_enabled} disabled={busy} onChange={(value) => void updatePreferences({ memory_enabled: value })} />
      <SettingRange label="Memory retention" value={data.preferences.memory_retention_days} min={1} max={3650} suffix=" days" disabled={busy} onCommit={(value) => void updatePreferences({ memory_retention_days: value })} />
      <SettingToggle label="Anonymous diagnostics" checked={data.preferences.analytics_enabled} disabled={busy} onChange={(value) => void updatePreferences({ analytics_enabled: value })} />
      <HealthRow icon={<Eye size={17} />} label="Credential storage" status="Protected by Windows DPAPI; secrets are never returned" tone="success" />
    </Category>;
    case "pet": return <Category title="Pet" subtitle="Companion behavior">
      <SettingToggle label="Enable Fairy pet" checked={data.preferences.pet_enabled} disabled={busy} onChange={(value) => void updatePreferences({ pet_enabled: value })} />
      <SettingToggle label="Always on top" checked={data.preferences.pet_always_on_top} disabled={busy} onChange={(value) => void updatePreferences({ pet_always_on_top: value })} />
      <SettingToggle label="Mute pet" checked={data.preferences.pet_muted} disabled={busy} onChange={(value) => void updatePreferences({ pet_muted: value })} />
    </Category>;
    case "advanced": return <Category title="Advanced" subtitle="Diagnostics and developer tools">
      <SettingToggle label="Developer mode" checked={data.preferences.developer_mode} disabled={busy} onChange={(value) => void updatePreferences({ developer_mode: value })} />
      <HealthRow icon={<ShieldCheck size={17} />} label="Settings capability" status="Restricted settings methods only" tone="success" />
    </Category>;
  }
}

function ModelsPanel(props: Parameters<typeof SettingsCategory>[0]) {
  const { data, busy, client, act, reload, updatePreferences } = props;
  const [apiKey, setApiKey] = useState("");
  const [modelId, setModelId] = useState(data.openRouterModelId ?? "tencent/hy3:free");
  const status = new Map(data.health.map((item) => [item.profile_id, item.status]));
  return <Category title="Models" subtitle="Providers and credentials">
    <div className="settings-provider-list">
      {data.providers.map((provider) => <button key={provider.id} type="button" className={data.preferences.selected_profile_id === provider.id ? "selected" : ""} disabled={busy || !provider.enabled} onClick={() => void updatePreferences({ selected_profile_id: provider.id })}>
        <span className={`settings-health-dot ${status.get(provider.id) ?? "unknown"}`} />
        <span><strong>{provider.display_name}</strong><small>{provider.model_id}</small></span>
        <span>{provider.credential_required ? provider.credential_configured ? "Connected" : "Credential required" : "Ready"}</span>
        {data.preferences.selected_profile_id === provider.id ? <Check size={16} /> : null}
      </button>)}
    </div>
    <form className="settings-form" onSubmit={(event) => { event.preventDefault(); void act(async () => { await client.providers.configureOpenRouter({ api_key: apiKey, model_id: modelId }); setApiKey(""); await reload(); }); }}>
      <div className="settings-form-heading"><KeyRound size={17} /><div><strong>OpenRouter credential</strong><span>{data.openRouterConfigured ? "Configured" : "Not configured"}</span></div></div>
      <label><span>API key</span><input type="password" autoComplete="off" required value={apiKey} disabled={busy} placeholder={data.openRouterConfigured ? "Enter a new key to replace" : "sk-or-v1-..."} onChange={(event) => setApiKey(event.target.value)} /></label>
      <label><span>Model ID</span><input value={modelId} list="settings-free-models" required disabled={busy} onChange={(event) => setModelId(event.target.value)} /><datalist id="settings-free-models"><option value="tencent/hy3:free" /><option value="nvidia/nemotron-3-ultra-550b-a55b:free" /></datalist></label>
      <div className="settings-form-actions"><button className="primary-command" type="submit" disabled={busy || !apiKey.trim()}><KeyRound size={14} />Save and connect</button>
        {data.openRouterConfigured ? <button className="danger-icon" type="button" aria-label="Remove OpenRouter credential" title="Remove OpenRouter credential" disabled={busy} onClick={() => { if (!window.confirm("Remove the OpenRouter credential from this device?")) return; void act(async () => { await client.providers.deleteOpenRouter(); await reload(); }); }}><Trash2 size={15} /></button> : null}
      </div>
    </form>
  </Category>;
}

function PermissionsPanel(props: Parameters<typeof SettingsCategory>[0]) {
  const { data, busy, client, act, updateData, updatePreferences } = props;
  const updatePermissions = (profile: ExecutionSettings["profile"], overrides = data.permissions.capability_overrides) => void act(async () => {
    const saved = await client.permissions.update({ expected_revision: data.permissions.revision, profile, capability_overrides: overrides, idempotency_key: permissionKey(data.permissions.revision, profile, overrides) });
    updateData((current) => current === null ? null : { ...current, permissions: saved });
  });
  const definitions = data.capabilities.command_metadata.filter((item) => item.model_visible);
  return <Category title="Execution permissions" subtitle="Local and cloud policy">
    <fieldset className="settings-segments" disabled={busy}><legend>Local profile</legend>{(["observe", "standard", "autonomous"] as const).map((profile) => <label key={profile}><input type="radio" name="local-profile" checked={data.permissions.profile === profile} onChange={() => updatePermissions(profile)} /><span>{titleCase(profile)}</span></label>)}</fieldset>
    <SettingSelect icon={<ShieldCheck size={17} />} label="Cloud profile" value={data.preferences.permission_cloud_profile} disabled={busy} onChange={(value) => void updatePreferences({ permission_cloud_profile: value as DesktopPreferences["permission_cloud_profile"] })} options={[{ value: "observe", label: "Observe" }, { value: "standard", label: "Standard" }, { value: "autonomous", label: "Autonomous" }]} />
    <div className="settings-capabilities"><header><strong>Capabilities</strong><span>{data.capabilities.sandbox_healthy ? "Sandbox ready" : "Sandbox unavailable"}</span></header>
      {definitions.map((definition) => { const enabled = data.permissions.capability_overrides[definition.name] !== false; const available = definition.profiles.includes(data.permissions.profile) && (!definition.requires_sandbox || data.capabilities.sandbox_healthy); return <SettingToggle key={definition.name} label={definition.name} detail={definition.description} checked={enabled} disabled={busy || !available} onChange={(value) => { const overrides = { ...data.permissions.capability_overrides }; if (value) delete overrides[definition.name]; else overrides[definition.name] = false; updatePermissions(data.permissions.profile, overrides); }} />; })}
    </div>
  </Category>;
}

function ExtensionsPanel(props: Parameters<typeof SettingsCategory>[0]) {
  const { data, busy, client, act, reload } = props;
  const [tab, setTab] = useState<"skills" | "mcp">("skills");
  const [adding, setAdding] = useState(false);
  return <Category title="Skills / MCP" subtitle="Governed extensions">
    <div className="settings-tabs" role="tablist"><button type="button" role="tab" aria-selected={tab === "skills"} className={tab === "skills" ? "active" : ""} onClick={() => setTab("skills")}>Skills <span>{data.skills.length}</span></button><button type="button" role="tab" aria-selected={tab === "mcp"} className={tab === "mcp" ? "active" : ""} onClick={() => setTab("mcp")}>MCP <span>{data.servers.length}</span></button></div>
    {tab === "skills" ? <div className="settings-extension-list">{data.skills.map((skill) => <div className="settings-extension-row" key={`${skill.name}@${skill.version}`}><span className={`settings-health-dot ${skill.available ? "available" : "unavailable"}`} /><div><strong>{skill.name}</strong><small>{skill.description}</small></div><code>{skill.version}</code></div>)}</div> : <>
      <div className="settings-section-command"><span>{data.servers.length} configured servers</span><button className="secondary-command" type="button" onClick={() => setAdding((value) => !value)}>{adding ? <X size={14} /> : <Plus size={14} />}{adding ? "Cancel" : "Add server"}</button></div>
      {adding ? <McpForm busy={busy} onSave={(input) => act(async () => { await client.extensions.configure(input); setAdding(false); await reload(); })} /> : null}
      <div className="settings-extension-list">{data.servers.map((server) => <section className="settings-mcp-server" key={server.server_id}><div className="settings-extension-row"><span className={`settings-health-dot ${server.enabled ? "available" : "unknown"}`} /><div><strong>{server.display_name}</strong><small>{server.transport}</small></div><label className="compact-switch"><input type="checkbox" aria-label={`Enable ${server.display_name}`} checked={server.enabled} disabled={busy || server.accepted_schema_digest === null} onChange={(event) => void act(async () => { await client.extensions.setEnabled({ server_id: server.server_id, expected_revision: server.revision, enabled: event.target.checked, idempotency_key: extensionKey(server, "enabled", event.target.checked) }); await reload(); })} /><span /></label><button className="danger-icon" type="button" aria-label={`Delete ${server.display_name}`} title={`Delete ${server.display_name}`} disabled={busy} onClick={() => { if (!window.confirm(`Delete MCP server “${server.display_name}”?`)) return; void act(async () => { await client.extensions.delete({ server_id: server.server_id, expected_revision: server.revision, idempotency_key: extensionKey(server, "delete", true) }); await reload(); }); }}><Trash2 size={14} /></button></div>{server.pending_schema_digest !== null && server.pending_schema_digest !== server.accepted_schema_digest ? <PendingMcpReview server={server} busy={busy} onAccept={(tools) => act(async () => { await client.extensions.accept({ server_id: server.server_id, expected_revision: server.revision, schema_digest: server.pending_schema_digest as string, enabled: true, tools, idempotency_key: extensionKey(server, "accept", true) }); await reload(); })} /> : null}</section>)}</div>
    </>}
  </Category>;
}

function PendingMcpReview({ server, busy, onAccept }: { server: McpServer; busy: boolean; onAccept(tools: McpToolPolicyInput[]): Promise<void> }) {
  const [policies, setPolicies] = useState<McpToolPolicyInput[]>(() => server.pending_tools.map((tool) => ({ name: tool.name, side_effect: "execute", risk_level: "high", approval_policy: "always", profiles: ["standard", "autonomous"], idempotent: false, enabled: true })));
  const patchPolicy = (name: string, patch: Partial<McpToolPolicyInput>) => setPolicies((current) => current.map((policy) => policy.name === name ? { ...policy, ...patch } : policy));
  return <div className="settings-mcp-review"><header><strong>Pending tool review</strong><span>{server.pending_tools.length}</span></header>{server.pending_tools.map((tool) => { const policy = policies.find((item) => item.name === tool.name); if (!policy) return null; return <fieldset key={tool.name} disabled={busy}><legend>{tool.name}</legend><p>{tool.description}</p><div><label><span>Effect</span><select aria-label="Effect" value={policy.side_effect} onChange={(event) => patchPolicy(tool.name, { side_effect: event.target.value as McpToolPolicyInput["side_effect"] })}><option value="none">None</option><option value="read">Read</option><option value="write">Write</option><option value="execute">Execute</option></select></label><label><span>Risk</span><select aria-label="Risk" value={policy.risk_level} onChange={(event) => patchPolicy(tool.name, { risk_level: event.target.value as McpToolPolicyInput["risk_level"] })}><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option></select></label><label><span>Approval</span><select aria-label="Approval" value={policy.approval_policy} onChange={(event) => patchPolicy(tool.name, { approval_policy: event.target.value as McpToolPolicyInput["approval_policy"] })}><option value="never">Never</option><option value="profile">By profile</option><option value="always">Always</option></select></label></div></fieldset>; })}<button className="primary-command" type="button" disabled={busy} onClick={() => void onAccept(policies)}><Check size={14} />Accept reviewed schema</button></div>;
}

function McpForm({ busy, onSave }: { busy: boolean; onSave(input: Parameters<SettingsClient["extensions"]["configure"]>[0]): Promise<void> }) {
  const [transport, setTransport] = useState<"stdio" | "streamable_http">("stdio");
  const [serverId, setServerId] = useState("");
  const [name, setName] = useState("");
  const [target, setTarget] = useState("");
  return <form className="settings-form" onSubmit={(event) => { event.preventDefault(); const input = { server_id: serverId.trim(), display_name: name.trim(), transport, command: transport === "stdio" ? target.trim() : null, arguments: [], endpoint: transport === "streamable_http" ? target.trim() : null, credential_ref: null, environment_refs: {}, expected_revision: 0, idempotency_key: `mcp:create:${serverId.trim()}` }; void onSave(input); }}>
    <fieldset className="settings-segments" disabled={busy}><legend>Transport</legend><label><input type="radio" checked={transport === "stdio"} onChange={() => setTransport("stdio")} /><span>Local stdio</span></label><label><input type="radio" checked={transport === "streamable_http"} onChange={() => setTransport("streamable_http")} /><span>Streamable HTTP</span></label></fieldset>
    <label><span>Server ID</span><input required pattern="[a-z0-9][a-z0-9._-]*" value={serverId} disabled={busy} onChange={(event) => setServerId(event.target.value)} /></label>
    <label><span>Display name</span><input required value={name} disabled={busy} onChange={(event) => setName(event.target.value)} /></label>
    <label><span>{transport === "stdio" ? "Command" : "HTTPS endpoint"}</span><input required value={target} disabled={busy} onChange={(event) => setTarget(event.target.value)} /></label>
    <div className="settings-form-actions"><button className="primary-command" type="submit" disabled={busy || !serverId.trim() || !name.trim() || !target.trim()}><Plus size={14} />Save server</button></div>
  </form>;
}

function Category({ title, subtitle, children }: { title: string; subtitle: string; children: React.ReactNode }) {
  return <div className="settings-category"><header><span>{subtitle}</span><h1>{title}</h1></header><div className="settings-category-body">{children}</div></div>;
}

function SettingToggle({ label, detail, checked, disabled, onChange }: { label: string; detail?: string; checked: boolean; disabled: boolean; onChange(value: boolean): void }) {
  return <label className="settings-row settings-toggle-row"><span><strong>{label}</strong>{detail ? <small>{detail}</small> : null}</span><span className="settings-switch"><input type="checkbox" checked={checked} disabled={disabled} onChange={(event) => onChange(event.target.checked)} /><span /></span></label>;
}

function SettingSelect({ icon, label, value, options, disabled, onChange }: { icon: React.ReactNode; label: string; value: string; options: { value: string; label: string }[]; disabled: boolean; onChange(value: string): void }) {
  return <label className="settings-row settings-select-row"><span className="settings-row-copy">{icon}<strong>{label}</strong></span><select value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)}>{options.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}</select></label>;
}

function SettingRange({ label, value, min, max, suffix, disabled, onCommit }: { label: string; value: number; min: number; max: number; suffix: string; disabled: boolean; onCommit(value: number): void }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  return <label className="settings-row settings-range-row"><span><strong>{label}</strong><small>{draft}{suffix}</small></span><input type="range" min={min} max={max} value={draft} disabled={disabled} onChange={(event) => setDraft(Number(event.target.value))} onPointerUp={() => onCommit(draft)} onKeyUp={() => onCommit(draft)} /></label>;
}

function HealthRow({ icon, label, status, tone }: { icon: React.ReactNode; label: string; status: string; tone: "neutral" | "success" | "error" }) {
  return <div className="settings-row settings-health-row"><span className="settings-row-copy">{icon}<strong>{label}</strong></span><span data-tone={tone}>{status}</span></div>;
}

function permissionKey(revision: number, profile: string, overrides: Record<string, boolean>) {
  return `settings:permissions:${revision}:${profile}:${Object.entries(overrides).sort(([a], [b]) => a.localeCompare(b)).map(([key, value]) => `${key}=${value}`).join(",")}`;
}

function extensionKey(server: McpServer, action: string, value: boolean) {
  return `settings:mcp:${server.server_id}:${server.revision}:${action}:${value}`;
}

function titleCase(value: string) { return `${value.charAt(0).toUpperCase()}${value.slice(1)}`; }
function messageOf(value: unknown) { return value instanceof Error ? value.message : "Settings request failed"; }

function unavailableVoiceHealth(): VoiceWorkerHealth {
  return {
    status: "unavailable",
    model_repository: "FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
    model_installed: false,
    model_ready: false,
    model_digest: null,
    prompt_ready: false,
    cuda_available: false,
    tensorrt_available: false,
    backend: null,
    device_name: null,
    sample_rate: 24_000,
    error_code: "VOICE_WORKER_UNAVAILABLE",
  };
}

function voiceHealthLabel(health: VoiceWorkerHealth): string {
  if (health.status === "ready") return `Ready at ${health.sample_rate / 1000} kHz`;
  if (health.status === "warming") return "Warming model";
  if (health.status === "model_missing") return "Model not installed";
  return health.error_code ?? "Voice unavailable";
}
