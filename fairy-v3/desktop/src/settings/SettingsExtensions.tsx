import {
  Check,
  Eye,
  Plus,
  RefreshCw,
  Search,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import { useState } from "react";

import type {
  ExtensionCatalogEntry,
  McpServer,
  McpToolPolicyInput,
  Skill,
  SkillImportInspection,
} from "../core/client";
import { ActionDialog } from "../ui/ActionDialog";
import type { SettingsClient } from "./client";
import { Category } from "./settingsControls";
import {
  formatBytes,
  messageOf,
  titleCase,
  type SettingsCategoryProps,
} from "./settingsShared";

export function ExtensionsPanel(props: SettingsCategoryProps) {
  const { data, busy, client, act, reload } = props;
  const [tab, setTab] = useState<"skills" | "mcp">("skills");
  const [views, setViews] = useState<Record<"skills" | "mcp", "installed" | "store">>({
    skills: "installed",
    mcp: "installed",
  });
  const [skillAction, setSkillAction] = useState<"import" | "create" | null>(null);
  const [mcpAction, setMcpAction] = useState<"manual" | "json" | null>(null);
  const view = views[tab];
  const setView = (next: "installed" | "store") => {
    setViews((current) => ({ ...current, [tab]: next }));
    setSkillAction(null);
    setMcpAction(null);
  };
  return <Category title="Skills / MCP" subtitle="Governed extensions">
    <div className="settings-tabs" role="tablist"><button type="button" role="tab" aria-selected={tab === "skills"} className={tab === "skills" ? "active" : ""} onClick={() => setTab("skills")}>Skills <span>{data.skills.length}</span></button><button type="button" role="tab" aria-selected={tab === "mcp"} className={tab === "mcp" ? "active" : ""} onClick={() => setTab("mcp")}>MCP <span>{data.servers.length}</span></button></div>
    <div className="settings-extension-toolbar">
      <ExtensionViewTabs kind={tab} view={view} setView={setView} />
      {view === "installed" ? <div className="settings-extension-actions">
        {tab === "skills" ? <>
          <button aria-pressed={skillAction === "import"} className="secondary-command" type="button" disabled={busy} onClick={() => setSkillAction((current) => current === "import" ? null : "import")}>{skillAction === "import" ? <X size={14} /> : <Plus size={14} />}{skillAction === "import" ? "Cancel" : "Add external"}</button>
          <button aria-pressed={skillAction === "create"} className="secondary-command" type="button" disabled={busy} onClick={() => setSkillAction((current) => current === "create" ? null : "create")}>{skillAction === "create" ? <X size={14} /> : <Sparkles size={14} />}{skillAction === "create" ? "Cancel" : "Create Skill"}</button>
        </> : <>
          <button aria-pressed={mcpAction === "manual"} className="secondary-command" type="button" disabled={busy} onClick={() => setMcpAction((current) => current === "manual" ? null : "manual")}>{mcpAction === "manual" ? <X size={14} /> : <Plus size={14} />}{mcpAction === "manual" ? "Cancel" : "Add server"}</button>
          <button aria-pressed={mcpAction === "json"} className="secondary-command" type="button" disabled={busy} onClick={() => setMcpAction((current) => current === "json" ? null : "json")}>{mcpAction === "json" ? <X size={14} /> : <Plus size={14} />}{mcpAction === "json" ? "Cancel" : "Import JSON"}</button>
        </>}
      </div> : <span>{filterCatalog(data.extensionCatalog, tab).length} curated</span>}
    </div>
    {tab === "skills" && view === "installed" ? <>
      {skillAction === "import" ? <SkillImportForm busy={busy} client={client} act={act} reload={reload} onDone={() => setSkillAction(null)} /> : null}
      {skillAction === "create" ? <SkillCreateForm busy={busy} client={client} act={act} reload={reload} onDone={() => setSkillAction(null)} /> : null}
      <InstalledSkills data={data} busy={busy} client={client} act={act} reload={reload} />
    </> : null}
    {tab === "skills" && view === "store" ? <SkillStore data={data} busy={busy} client={client} act={act} reload={reload} /> : null}
    {tab === "mcp" && view === "installed" ? <>
      {mcpAction === "manual" ? <McpForm busy={busy} onSave={(input) => act(async () => { await client.extensions.configure(input); setMcpAction(null); await reload(); })} /> : null}
      {mcpAction === "json" ? <McpJsonImportForm busy={busy} onSave={(input) => act(async () => { await client.extensions.configure(input); setMcpAction(null); await reload(); })} /> : null}
      {data.latestTaskId === null ? <p className="settings-callout">Create a chat or project Task before discovering MCP tools. Discovery is attached to an auditable Task scope.</p> : null}
      <McpServerList data={data} busy={busy} client={client} act={act} reload={reload} />
    </> : null}
    {tab === "mcp" && view === "store" ? <McpStore data={data} busy={busy} client={client} act={act} reload={reload} /> : null}
  </Category>;
}

function ExtensionViewTabs({ kind, view, setView }: { kind: "skills" | "mcp"; view: "installed" | "store"; setView(value: "installed" | "store"): void }) {
  return <div className="settings-subtabs" role="tablist" aria-label={`${kind === "skills" ? "Skill" : "MCP"} view`}>
    <button type="button" role="tab" aria-selected={view === "installed"} className={view === "installed" ? "active" : ""} onClick={() => setView("installed")}>Installed</button>
    <button type="button" role="tab" aria-selected={view === "store"} className={view === "store" ? "active" : ""} onClick={() => setView("store")}>Store</button>
  </div>;
}

function InstalledSkills({ data, busy, client, act, reload }: Pick<SettingsCategoryProps, "data" | "busy" | "client" | "act" | "reload">) {
  const [pendingRemoval, setPendingRemoval] = useState<Skill | null>(null);
  if (data.skills.length === 0) return <p className="settings-empty">No Skills installed</p>;
  return <><div className="settings-extension-list">{data.skills.map((skill) => <section className="settings-mcp-server" key={`${skill.name}@${skill.version}`}><div className="settings-extension-row"><span className={`settings-health-dot ${skill.available ? "available" : "unavailable"}`} /><div><strong>{skill.name}</strong><small>{skill.description}</small></div><code>{skill.version}</code><label className="compact-switch"><input type="checkbox" aria-label={`Enable ${skill.name}`} checked={skill.enabled} disabled={busy} onChange={(event) => void act(async () => { await client.extensions.setSkillEnabled({ name: skill.name, expected_content_sha256: skill.content_sha256, enabled: event.target.checked, idempotency_key: `settings:skill:${skill.name}:${skill.content_sha256}:enabled:${event.target.checked}` }); await reload(); })} /><span /></label><button className="danger-icon" type="button" aria-label={`Remove ${skill.name}`} disabled={busy} onClick={() => setPendingRemoval(skill)}><Trash2 size={14} /></button></div></section>)}</div><ActionDialog open={pendingRemoval !== null} busy={busy} destructive title="Remove Skill" description={pendingRemoval === null ? "" : `Remove ${pendingRemoval.name} and its model-visible tool from this device?`} confirmLabel="Remove" onCancel={() => setPendingRemoval(null)} onConfirm={async () => { if (pendingRemoval === null) return; const skill = pendingRemoval; await act(async () => { await client.extensions.removeSkill({ name: skill.name, expected_content_sha256: skill.content_sha256, idempotency_key: `settings:skill:${skill.name}:${skill.content_sha256}:remove` }); await reload(); }); setPendingRemoval(null); }} /></>;
}

function SkillStore(props: Pick<SettingsCategoryProps, "data" | "busy" | "client" | "act" | "reload">) {
  return <StoreSurface {...props} kind="skills" />;
}

function McpStore(props: Pick<SettingsCategoryProps, "data" | "busy" | "client" | "act" | "reload">) {
  return <StoreSurface {...props} kind="mcp" />;
}

function StoreSurface({ data, busy, client, act, reload, kind }: Pick<SettingsCategoryProps, "data" | "busy" | "client" | "act" | "reload"> & { kind: "skills" | "mcp" }) {
  const [query, setQuery] = useState("");
  const entries = filterCatalog(data.extensionCatalog, kind).filter((entry) => {
    const haystack = `${entry.name} ${entry.description} ${entry.publisher} ${(entry.tags ?? []).join(" ")}`.toLocaleLowerCase();
    return haystack.includes(query.trim().toLocaleLowerCase());
  });
  return <div className="settings-store">
    <label className="settings-store-search"><Search size={14} /><input aria-label={`Search ${kind === "skills" ? "Skills" : "MCP"} store`} value={query} placeholder="Search the curated store" onChange={(event) => setQuery(event.target.value)} /></label>
    {entries.length === 0 ? <p className="settings-empty">No matching extensions</p> : <div className="settings-extension-list">{entries.map((entry) => <StoreEntry key={entry.extension_id} entry={entry} busy={busy} install={async (credentialRef) => {
      await act(async () => {
        if (kind === "skills") {
          await client.extensions.installSkill({ catalog_id: entry.extension_id, idempotency_key: `settings:catalog:${entry.extension_id}:install` });
        } else {
          await client.extensions.installPreset({ catalog_id: entry.extension_id, credential_ref: credentialRef, expected_revision: 0, idempotency_key: `settings:mcp:preset:${entry.extension_id}:install` });
        }
        await reload();
      });
    }} />)}</div>}
  </div>;
}

function StoreEntry({ entry, busy, install }: { entry: ExtensionCatalogEntry; busy: boolean; install(credentialRef: string | null): Promise<void> }) {
  const [credentialRef, setCredentialRef] = useState("");
  const needsCredential = entry.extension_id === "github";
  return <section className="settings-store-entry">
    <div><strong>{entry.name}</strong><small>{entry.description}</small><span>{entry.publisher} · {entry.license}{entry.experimental ? " · Experimental" : ""}</span>{(entry.tags ?? []).length > 0 ? <div className="settings-extension-tags">{(entry.tags ?? []).map((tag) => <span key={tag}>{tag}</span>)}</div> : null}{(entry.requirements ?? []).length > 0 ? <p>{(entry.requirements ?? []).join(" · ")}</p> : null}</div>
    <div className="settings-store-setup">
      {needsCredential && !entry.installed ? <label><span>Credential reference</span><input value={credentialRef} placeholder="github-token" disabled={busy} onChange={(event) => setCredentialRef(event.target.value)} /></label> : null}
      <button className="secondary-command" type="button" disabled={busy || entry.installed || (needsCredential && !credentialRef.trim())} onClick={() => void install(credentialRef.trim() || null)}>{entry.installed ? <Check size={14} /> : <Plus size={14} />}{entry.installed ? "Installed" : "Install"}</button>
    </div>
  </section>;
}

function filterCatalog(entries: ExtensionCatalogEntry[], kind: "skills" | "mcp") {
  return entries.filter((entry) => kind === "skills" ? entry.kind === "skill" : entry.kind === "mcp_preset");
}

function SkillImportForm({ busy, client, act, reload, onDone }: Pick<SettingsCategoryProps, "busy" | "client" | "act" | "reload"> & { onDone(): void }) {
  const [sourceKind, setSourceKind] = useState<"folder" | "zip" | "github">("folder");
  const [source, setSource] = useState("");
  const [inspection, setInspection] = useState<SkillImportInspection | null>(null);
  const [version, setVersion] = useState("1.0.0");
  const [description, setDescription] = useState("");
  const [publisher, setPublisher] = useState("Local user");
  const [license, setLicense] = useState("Proprietary");
  const [capabilities, setCapabilities] = useState("");
  const [servers, setServers] = useState("");
  const resetInspection = () => setInspection(null);
  const inspect = () => void act(async () => {
    const result = await client.extensions.inspectSkillImport({ source_kind: sourceKind, source: source.trim() });
    setInspection(result);
    setVersion(result.version ?? "1.0.0");
    setDescription(result.description);
    setPublisher(result.publisher ?? "Local user");
    setLicense(result.license ?? "Proprietary");
  });
  const locked = inspection?.has_manifest === true;
  return <section className="settings-extension-editor">
    <header><strong>Add external Skill</strong><span>Fairy copies the reviewed package into managed storage; the source remains unchanged.</span></header>
    <fieldset className="settings-segments" disabled={busy}><legend>Source</legend>{(["folder", "zip", "github"] as const).map((kind) => <label key={kind}><input type="radio" name="skill-import-source" checked={sourceKind === kind} onChange={() => { setSourceKind(kind); setSource(""); resetInspection(); }} /><span>{kind === "github" ? "GitHub" : titleCase(kind)}</span></label>)}</fieldset>
    <div className="settings-path-input"><input aria-label="Skill source" value={source} disabled={busy} placeholder={sourceKind === "github" ? "https://github.com/owner/repository/tree/ref/path" : sourceKind === "zip" ? "Select a Skill ZIP archive" : "Select a Skill folder"} onChange={(event) => { setSource(event.target.value); resetInspection(); }} />{sourceKind !== "github" ? <button className="secondary-command" type="button" disabled={busy} onClick={() => void act(async () => { const selected = await client.extensions.selectSkillSource(sourceKind); if (selected !== null) { setSource(selected); resetInspection(); } })}>Browse</button> : null}<button className="secondary-command" type="button" disabled={busy || !source.trim()} onClick={inspect}><Eye size={14} />Inspect</button></div>
    {inspection !== null ? <form className="settings-form" onSubmit={(event) => { event.preventDefault(); void act(async () => {
      await client.extensions.installSkillImport({
        inspection_token: inspection.inspection_token,
        name: inspection.name,
        version: version.trim(),
        description: description.trim(),
        publisher: publisher.trim(),
        license: license.trim(),
        input_schema: defaultSkillInputSchema(),
        required_capabilities: commaValues(capabilities),
        compatible_mcp_servers: commaValues(servers),
        idempotency_key: `settings:skill:import:${inspection.inspection_token}`,
      });
      await reload();
      onDone();
    }); }}>
      <div className="settings-import-summary"><strong>{inspection.name}</strong><span>{inspection.file_count} files · {formatBytes(inspection.content_bytes)} · {inspection.has_manifest ? "Signed metadata" : "Manifest will be created"}</span></div>
      <label><span>Version</span><input required value={version} readOnly={locked} disabled={busy} onChange={(event) => setVersion(event.target.value)} /></label>
      <label><span>Publisher</span><input required value={publisher} readOnly={locked} disabled={busy} onChange={(event) => setPublisher(event.target.value)} /></label>
      <label className="settings-form-wide"><span>Description</span><textarea required rows={3} value={description} readOnly={locked} disabled={busy} onChange={(event) => setDescription(event.target.value)} /></label>
      <label><span>License</span><input required value={license} readOnly={locked} disabled={busy} onChange={(event) => setLicense(event.target.value)} /></label>
      <label><span>Required capabilities</span><input value={capabilities} disabled={busy || locked} placeholder="web.research, file.read" onChange={(event) => setCapabilities(event.target.value)} /></label>
      <label className="settings-form-wide"><span>Compatible MCP servers</span><input value={servers} disabled={busy || locked} placeholder="playwright, context7" onChange={(event) => setServers(event.target.value)} /></label>
      <div className="settings-form-actions"><button className="primary-command" type="submit" disabled={busy || !version.trim() || !description.trim() || !publisher.trim() || !license.trim()}><Plus size={14} />Install reviewed Skill</button></div>
    </form> : null}
  </section>;
}

function SkillCreateForm({ busy, client, act, reload, onDone }: Pick<SettingsCategoryProps, "busy" | "client" | "act" | "reload"> & { onDone(): void }) {
  const [name, setName] = useState("");
  const [version, setVersion] = useState("1.0.0");
  const [description, setDescription] = useState("");
  const [instructions, setInstructions] = useState("");
  const [publisher, setPublisher] = useState("Local user");
  const [license, setLicense] = useState("Proprietary");
  const [capabilities, setCapabilities] = useState("");
  const [servers, setServers] = useState("");
  return <section className="settings-extension-editor"><header><strong>Create Skill</strong><span>Build a local, versioned instruction package managed by Fairy.</span></header><form className="settings-form" onSubmit={(event) => { event.preventDefault(); void act(async () => {
    await client.extensions.createSkill({
      name: name.trim(),
      version: version.trim(),
      description: description.trim(),
      instructions: instructions.trim(),
      publisher: publisher.trim(),
      license: license.trim(),
      input_schema: defaultSkillInputSchema(),
      required_capabilities: commaValues(capabilities),
      compatible_mcp_servers: commaValues(servers),
      idempotency_key: `settings:skill:create:${name.trim()}:${version.trim()}`,
    });
    await reload();
    onDone();
  }); }}>
    <label><span>Skill ID</span><input required maxLength={64} pattern="[a-z0-9][a-z0-9-]*" value={name} disabled={busy} placeholder="my-workflow" onChange={(event) => setName(event.target.value)} /></label>
    <label><span>Version</span><input required value={version} disabled={busy} onChange={(event) => setVersion(event.target.value)} /></label>
    <label className="settings-form-wide"><span>Description</span><input required value={description} disabled={busy} onChange={(event) => setDescription(event.target.value)} /></label>
    <label className="settings-form-wide"><span>Instructions</span><textarea required rows={10} value={instructions} disabled={busy} placeholder="Describe when this Skill applies, its workflow, constraints, and expected output." onChange={(event) => setInstructions(event.target.value)} /></label>
    <label><span>Publisher</span><input required value={publisher} disabled={busy} onChange={(event) => setPublisher(event.target.value)} /></label>
    <label><span>License</span><input required value={license} disabled={busy} onChange={(event) => setLicense(event.target.value)} /></label>
    <label><span>Required capabilities</span><input value={capabilities} disabled={busy} placeholder="web.research, file.read" onChange={(event) => setCapabilities(event.target.value)} /></label>
    <label><span>Compatible MCP servers</span><input value={servers} disabled={busy} placeholder="playwright, context7" onChange={(event) => setServers(event.target.value)} /></label>
    <div className="settings-form-actions"><button className="primary-command" type="submit" disabled={busy || !name.trim() || !description.trim() || !instructions.trim()}><Sparkles size={14} />Create and install</button></div>
  </form></section>;
}

function McpJsonImportForm({ busy, onSave }: { busy: boolean; onSave(input: Parameters<SettingsClient["extensions"]["configure"]>[0]): Promise<void> }) {
  const [json, setJson] = useState("");
  const [parseError, setParseError] = useState<string | null>(null);
  return <form className="settings-form" onSubmit={(event) => {
    event.preventDefault();
    try {
      const input = parseMcpJson(json);
      setParseError(null);
      void onSave(input);
    } catch (error) {
      setParseError(messageOf(error));
    }
  }}>
    <div className="settings-form-heading"><Plus size={17} /><div><strong>Import MCP JSON</strong><span>Claude-style mcpServers JSON or a single Fairy server object. Raw environment secrets and headers are rejected.</span></div></div>
    <label className="settings-form-wide"><span>Configuration</span><textarea aria-label="MCP JSON configuration" required rows={10} value={json} disabled={busy} placeholder={'{"mcpServers":{"example":{"command":"npx","args":["-y","package"]}}}'} onChange={(event) => setJson(event.target.value)} /></label>
    {parseError !== null ? <p className="settings-inline-error">{parseError}</p> : null}
    <div className="settings-form-actions"><button className="primary-command" type="submit" disabled={busy || !json.trim()}><Plus size={14} />Review and save</button></div>
  </form>;
}

function McpServerList({ data, busy, client, act, reload }: Pick<SettingsCategoryProps, "data" | "busy" | "client" | "act" | "reload">) {
  const [pendingDelete, setPendingDelete] = useState<McpServer | null>(null);
  if (data.servers.length === 0) return <p className="settings-empty">No MCP servers installed</p>;
  return <>
    <div className="settings-extension-list">{data.servers.map((server) => <section className="settings-mcp-server" key={server.server_id}>
      <div className="settings-extension-row">
        <span className={`settings-health-dot ${server.enabled ? "available" : server.last_error_code ? "unavailable" : "unknown"}`} />
        <div><strong>{server.display_name}</strong><small>{server.transport} · {titleCase(server.status)}{server.last_error_code ? ` · ${server.last_error_code}` : ""}</small></div>
        <button className="secondary-command" type="button" disabled={busy || data.latestTaskId === null} onClick={() => void act(async () => {
          await client.extensions.discover({ server_id: server.server_id, task_id: data.latestTaskId as string, expected_revision: server.revision, idempotency_key: extensionKey(server, "discover", true) });
          await reload();
        })}><RefreshCw size={14} />Discover</button>
        <label className="compact-switch"><input type="checkbox" aria-label={`Enable ${server.display_name}`} checked={server.enabled} disabled={busy || server.accepted_schema_digest === null} onChange={(event) => void act(async () => {
          await client.extensions.setEnabled({ server_id: server.server_id, expected_revision: server.revision, enabled: event.target.checked, idempotency_key: extensionKey(server, "enabled", event.target.checked) });
          await reload();
        })} /><span /></label>
        <button className="danger-icon" type="button" aria-label={`Delete ${server.display_name}`} disabled={busy} onClick={() => setPendingDelete(server)}><Trash2 size={14} /></button>
      </div>
      {server.pending_schema_digest !== null && server.pending_schema_digest !== server.accepted_schema_digest ? <PendingMcpReview server={server} busy={busy} onAccept={(tools) => act(async () => {
        await client.extensions.accept({ server_id: server.server_id, expected_revision: server.revision, schema_digest: server.pending_schema_digest as string, enabled: true, tools, idempotency_key: extensionKey(server, "accept", true) });
        await reload();
      })} /> : null}
    </section>)}</div>
    <ActionDialog open={pendingDelete !== null} busy={busy} destructive title="Delete MCP server" description={pendingDelete === null ? "" : `Delete ${pendingDelete.display_name} and revoke all imported MCP tools?`} confirmLabel="Delete" onCancel={() => setPendingDelete(null)} onConfirm={async () => {
      if (pendingDelete === null) return;
      const server = pendingDelete;
      await act(async () => {
        await client.extensions.delete({ server_id: server.server_id, expected_revision: server.revision, idempotency_key: extensionKey(server, "delete", true) });
        await reload();
      });
      setPendingDelete(null);
    }} />
  </>;
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
  const [argumentsText, setArgumentsText] = useState("");
  return <form className="settings-form" onSubmit={(event) => { event.preventDefault(); const input = { server_id: serverId.trim(), display_name: name.trim(), transport, command: transport === "stdio" ? target.trim() : null, arguments: transport === "stdio" ? parseMcpArguments(argumentsText) : [], endpoint: transport === "streamable_http" ? target.trim() : null, credential_ref: null, environment_refs: {}, expected_revision: 0, idempotency_key: `mcp:create:${serverId.trim()}` }; void onSave(input); }}>
    <fieldset className="settings-segments" disabled={busy}><legend>Transport</legend><label><input type="radio" checked={transport === "stdio"} onChange={() => setTransport("stdio")} /><span>Local stdio</span></label><label><input type="radio" checked={transport === "streamable_http"} onChange={() => setTransport("streamable_http")} /><span>Streamable HTTP</span></label></fieldset>
    <label><span>Server ID</span><input required maxLength={64} pattern="[a-z0-9][a-z0-9-]*" value={serverId} disabled={busy} onChange={(event) => setServerId(event.target.value)} /></label>
    <label><span>Display name</span><input required value={name} disabled={busy} onChange={(event) => setName(event.target.value)} /></label>
    <label className="settings-form-wide"><span>{transport === "stdio" ? "Command" : "HTTPS endpoint"}</span><input required value={target} disabled={busy} onChange={(event) => setTarget(event.target.value)} /></label>
    {transport === "stdio" ? <label className="settings-form-wide"><span>Arguments (one per line)</span><textarea aria-label="Arguments (one per line)" rows={5} value={argumentsText} disabled={busy} onChange={(event) => setArgumentsText(event.target.value)} /></label> : null}
    <div className="settings-form-actions"><button className="primary-command" type="submit" disabled={busy || !serverId.trim() || !name.trim() || !target.trim()}><Plus size={14} />Save server</button></div>
  </form>;
}

function extensionKey(server: McpServer, action: string, value: boolean) {
  return `settings:mcp:${server.server_id}:${server.revision}:${action}:${value}`;
}

function parseMcpArguments(value: string): string[] {
  return value.split(/\r?\n/u).map((argument) => argument.trim()).filter(Boolean);
}

function defaultSkillInputSchema(): Record<string, unknown> {
  return {
    type: "object",
    properties: { brief: { type: "string", maxLength: 12_000 } },
    additionalProperties: false,
  };
}

function commaValues(value: string): string[] {
  return [...new Set(value.split(",").map((item) => item.trim()).filter(Boolean))];
}

function parseMcpJson(value: string): Parameters<SettingsClient["extensions"]["configure"]>[0] {
  const root = jsonRecord(JSON.parse(value) as unknown, "MCP configuration");
  let serverId: string;
  let config: Record<string, unknown>;
  if ("mcpServers" in root) {
    const servers = jsonRecord(root.mcpServers, "mcpServers");
    const entries = Object.entries(servers);
    if (entries.length !== 1) throw new Error("Import exactly one MCP server at a time");
    [serverId, config] = [entries[0][0], jsonRecord(entries[0][1], "MCP server")];
  } else {
    config = root;
    serverId = stringValue(config.server_id ?? config.id, "server_id");
  }
  if (!/^[a-z0-9][a-z0-9-]{0,63}$/u.test(serverId)) {
    throw new Error("Server ID must use lowercase letters, numbers, and hyphens");
  }
  for (const unsafeKey of ["env", "headers", "authorization", "token", "apiKey", "api_key"]) {
    if (unsafeKey in config) throw new Error(`Raw ${unsafeKey} values are not accepted; use a stored credential reference`);
  }
  const command = optionalString(config.command);
  const endpoint = optionalString(config.url ?? config.endpoint);
  if ((command === null) === (endpoint === null)) {
    throw new Error("Provide either a local command or one HTTPS endpoint");
  }
  if (command !== null && /[\r\n;&|<>]/u.test(command)) {
    throw new Error("MCP command must be a single executable, not a shell expression");
  }
  const rawArguments = config.args ?? config.arguments ?? [];
  if (!Array.isArray(rawArguments) || rawArguments.some((argument) => typeof argument !== "string")) {
    throw new Error("MCP arguments must be a JSON string array");
  }
  const environmentRefs = config.environment_refs === undefined
    ? {}
    : stringRecord(config.environment_refs, "environment_refs");
  return {
    server_id: serverId,
    display_name: optionalString(config.display_name ?? config.name) ?? titleCase(serverId.replaceAll("-", " ")),
    transport: endpoint === null ? "stdio" : "streamable_http",
    command,
    arguments: endpoint === null ? rawArguments as string[] : [],
    endpoint,
    credential_ref: optionalString(config.credential_ref ?? config.credentialRef),
    environment_refs: environmentRefs,
    expected_revision: 0,
    idempotency_key: `settings:mcp:json:${serverId}:${crypto.randomUUID()}`,
  };
}

function jsonRecord(value: unknown, label: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${label} must be a JSON object`);
  }
  return value as Record<string, unknown>;
}

function stringRecord(value: unknown, label: string): Record<string, string> {
  const record = jsonRecord(value, label);
  if (Object.values(record).some((entry) => typeof entry !== "string")) {
    throw new Error(`${label} values must be stored credential reference names`);
  }
  return record as Record<string, string>;
}

function optionalString(value: unknown): string | null {
  if (value === undefined || value === null || value === "") return null;
  if (typeof value !== "string") throw new Error("MCP text fields must be strings");
  return value.trim() || null;
}

function stringValue(value: unknown, label: string): string {
  const result = optionalString(value);
  if (result === null) throw new Error(`${label} is required`);
  return result;
}
