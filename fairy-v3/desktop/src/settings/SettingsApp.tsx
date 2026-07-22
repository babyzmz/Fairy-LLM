import {
  Bot,
  Brain,
  Check,
  ChevronRight,
  FolderKanban,
  Gamepad2,
  Gauge,
  HardDrive,
  Eye,
  KeyRound,
  Languages,
  MessageSquareText,
  Mic2,
  MonitorCog,
  Palette,
  PawPrint,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
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
  ExtensionCatalogEntry,
  ModelCatalogPage,
  ModelSelectionPreference,
  MemorySettings,
  McpServer,
  McpToolPolicyInput,
  Skill,
  SkillImportInspection,
  ProjectArchivedItem,
  TrashItem,
} from "../core/client";
import { ActionDialog } from "../ui/ActionDialog";
import { startNativeVoiceTest } from "../voice/nativeVoice";
import {
  UNAVAILABLE_RENDERER_HEALTH,
  type PresenceRendererHealth,
} from "../presence/transport/rendererHealth";
import {
  applyDesktopPreferences,
  type DesktopPreferences,
  type SettingsClient,
  type VoiceWorkerHealth,
} from "./client";
import {
  automaticTrashMaintenanceFailed,
  markTrashMaintenanceSucceeded,
  runAutomaticTrashMaintenance,
} from "./trashMaintenance";
import {
  KnowledgePrivacyPanel,
  loadKnowledgePrivacy,
  type KnowledgePrivacyData,
} from "./KnowledgePrivacyPanel";
import {
  Category,
  HealthRow,
  SettingRange,
  SettingSelect,
  SettingToggle,
} from "./settingsControls";
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
  { id: "pet", label: "Pet", keywords: "presence companion mute always top liquid glass renderer hover particles opacity size", icon: PawPrint },
  { id: "advanced", label: "Advanced", keywords: "developer diagnostics logs", icon: SlidersHorizontal },
];

interface SettingsData extends KnowledgePrivacyData {
  preferences: DesktopPreferences;
  memorySettings: MemorySettings;
  openRouterConfigured: boolean;
  openRouterAccountId: string | null;
  realtimeCredentials: Record<"gemini" | "zhipu", boolean>;
  modelCatalog: ModelCatalogPage;
  modelSelection: ModelSelectionPreference;
  permissions: ExecutionSettings;
  capabilities: CapabilityManifest;
  skills: Skill[];
  extensionCatalog: ExtensionCatalogEntry[];
  servers: McpServer[];
  latestTaskId: string | null;
  voiceHealth: VoiceWorkerHealth;
  rendererHealth: PresenceRendererHealth;
  archivedProjects: ProjectArchivedItem[];
  trashItems: TrashItem[];
  autoPurgeError: string | null;
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
      const [preferences, memorySettings, status, geminiStatus, zhipuStatus, modelCatalog, modelSelection, permissions, capabilities, catalog, skills, servers, tasks, voiceHealth, rendererHealth, archived, initialTrash, knowledgePrivacy] =
        await Promise.all([
          client.preferences.get(),
          client.memory.settings.get(),
          client.providers.openRouterStatus(),
          client.providers.realtimeStatus("gemini").catch(() => ({ provider: "gemini" as const, configured: false })),
          client.providers.realtimeStatus("zhipu").catch(() => ({ provider: "zhipu" as const, configured: false })),
          client.models.catalog.list(),
          client.models.selection.get(),
          client.permissions.get(),
          client.permissions.capabilities(),
          client.extensions.catalog(),
          client.extensions.skills(),
          client.extensions.servers(),
          client.context.latestTask(),
          client.voice.health().catch(() => unavailableVoiceHealth()),
          client.pet.rendererHealth().catch(() => null),
          listAllArchivedProjects(client),
          listAllTrashItems(client),
          loadKnowledgePrivacy(client),
        ]);
      let trash = initialTrash;
      let autoPurgeError = automaticTrashMaintenanceFailed()
        ? "Automatic cleanup did not finish"
        : null;
      const maintenance = await runAutomaticTrashMaintenance(
        preferences,
        client.projectManagement.trash,
      );
      if (maintenance.attempted) {
        if (maintenance.failed) {
          autoPurgeError = "Automatic cleanup did not finish";
        } else {
          trash = await listAllTrashItems(client);
          autoPurgeError = null;
        }
      }
      setData({
        preferences,
        memorySettings,
        openRouterConfigured: status.configured,
        openRouterAccountId: status.account_id,
        realtimeCredentials: { gemini: geminiStatus.configured, zhipu: zhipuStatus.configured },
        modelCatalog,
        modelSelection,
        permissions,
        capabilities,
        extensionCatalog: catalog.items,
        skills: skills.items,
        servers: servers.items,
        latestTaskId: tasks.items.at(-1)?.id ?? null,
        voiceHealth,
        rendererHealth: rendererHealth ?? UNAVAILABLE_RENDERER_HEALTH,
        archivedProjects: archived,
        trashItems: trash,
        autoPurgeError,
        ...knowledgePrivacy,
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

  const updateMemorySettings = useCallback(
    async (patch: Partial<Pick<MemorySettings, "enabled" | "retention_days" | "export_to_obsidian" | "sync_normalized_content">>) => {
      if (data === null) return;
      await act(async () => {
        const next = { ...data.memorySettings, ...patch };
        const saved = await client.memory.settings.update({
          enabled: next.enabled,
          retention_days: next.retention_days,
          export_to_obsidian: next.export_to_obsidian,
          sync_normalized_content: next.sync_normalized_content,
          expected_revision: data.memorySettings.revision,
          idempotency_key: [
            "settings:memory",
            data.memorySettings.revision,
            Number(next.enabled),
            next.retention_days,
            Number(next.export_to_obsidian),
            Number(next.sync_normalized_content),
          ].join(":"),
        });
        setData((current) => current === null ? null : { ...current, memorySettings: saved });
      });
    },
    [act, client.memory.settings, data],
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

      <m.section className="settings-content" key={visibleCategory} initial={{ opacity: 0.4 }} animate={{ opacity: 1 }}>
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
            updateMemorySettings={updateMemorySettings}
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
  updateMemorySettings(patch: Partial<Pick<MemorySettings, "enabled" | "retention_days" | "export_to_obsidian" | "sync_normalized_content">>): Promise<void>;
  updateData: React.Dispatch<React.SetStateAction<SettingsData | null>>;
}) {
  const { id, data, busy, updatePreferences, updateMemorySettings } = props;
  switch (id) {
    case "general": return <Category title="General" subtitle="Desktop behavior">
      <SettingSelect icon={<Languages size={17} />} label="Language" value={data.preferences.language} disabled={busy} onChange={(value) => void updatePreferences({ language: value as DesktopPreferences["language"] })} options={[{ value: "system", label: "System default" }, { value: "en", label: "English" }, { value: "zh-CN", label: "简体中文" }]} />
      <SettingToggle label="Launch at startup" checked={data.preferences.launch_at_startup} disabled={busy} onChange={(value) => void updatePreferences({ launch_at_startup: value })} />
      <SettingToggle label="Minimize to notification area" checked={data.preferences.minimize_to_tray} disabled={busy} onChange={(value) => void updatePreferences({ minimize_to_tray: value })} />
      <ProjectManagementPanel {...props} />
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
        tone={data.voiceHealth.status === "ready" ? "success" : ["idle", "warming"].includes(data.voiceHealth.status) ? "neutral" : "error"}
      />
      <div className="settings-section-command">
        <span>{data.voiceHealth.status === "idle" ? "Starts only when playback is requested" : data.voiceHealth.device_name ?? "A CUDA GPU is required"}</span>
        {["idle", "ready"].includes(data.voiceHealth.status) ? (
          <button className="secondary-command" type="button" disabled={busy} onClick={() => void props.act(async () => {
            const playback = await startNativeVoiceTest();
            await playback.finished;
          })}>
            <Play size={14} /> Test Fairy voice
          </button>
        ) : data.voiceHealth.status === "warming" ? null : (
          <button className="secondary-command" type="button" disabled={busy} onClick={() => void props.act(async () => {
            await props.client.voice.installModel();
            await props.reload();
          })}>
            <RefreshCw size={14} /> {data.voiceHealth.model_installed ? "Retry voice runtime" : "Install voice model"}
          </button>
        )}
      </div>
      <h2 className="settings-section-title">Realtime game companion</h2>
      <SettingSelect icon={<Gamepad2 size={17} />} label="Realtime provider" detail="Auto uses GLM Flash for Chinese sessions and Gemini Live otherwise" value={data.preferences.realtime_provider} disabled={busy} onChange={(value) => void updatePreferences({ realtime_provider: value as DesktopPreferences["realtime_provider"] })} options={[{ value: "auto", label: "Auto" }, { value: "gemini_live", label: "Gemini Live" }, { value: "glm_realtime_flash", label: "GLM Realtime Flash" }, { value: "glm_realtime_air", label: "GLM Realtime Air" }]} />
      <SettingSelect icon={<Volume2 size={17} />} label="Realtime voice" detail="Fairy voice keeps provider speech text-only and plays it through the local Voice Worker" value={data.preferences.realtime_voice_mode} disabled={busy} onChange={(value) => void updatePreferences({ realtime_voice_mode: value as DesktopPreferences["realtime_voice_mode"] })} options={[{ value: "native", label: "Provider native voice" }, { value: "fairy", label: "Fairy local voice" }]} />
      <SettingToggle label="Share game audio by default" detail="Still requires confirmation for every session" checked={data.preferences.realtime_game_audio_default} disabled={busy} onChange={(value) => void updatePreferences({ realtime_game_audio_default: value })} />
      <SettingToggle label="Offer game progress memory" detail="Only saves a bounded summary you confirm; never saves audio, frames or full transcripts" checked={data.preferences.realtime_memory_enabled} disabled={busy} onChange={(value) => void updatePreferences({ realtime_memory_enabled: value })} />
      <SettingRange label="Maximum realtime session" value={data.preferences.realtime_max_session_minutes} min={5} max={120} step={5} suffix=" min" disabled={busy} onCommit={(value) => void updatePreferences({ realtime_max_session_minutes: value })} />
    </Category>;
    case "permissions": return <PermissionsPanel {...props} />;
    case "extensions": return <ExtensionsPanel {...props} />;
    case "knowledge": return <KnowledgePrivacyPanel {...props} />;
    case "pet": return <Category title="Pet" subtitle="Companion behavior">
      <SettingToggle label="Enable Fairy pet" checked={data.preferences.pet_enabled} disabled={busy} onChange={(value) => void updatePreferences({ pet_enabled: value })} />
      <SettingToggle label="Always on top" checked={data.preferences.pet_always_on_top} disabled={busy} onChange={(value) => void updatePreferences({ pet_always_on_top: value })} />
      <SettingToggle label="Mute pet" checked={data.preferences.pet_muted} disabled={busy} onChange={(value) => void updatePreferences({ pet_muted: value })} />
      <SettingSelect icon={<PawPrint size={17} />} label="Renderer" value={data.preferences.pet_renderer_mode} disabled={busy} onChange={(value) => void updatePreferences({ pet_renderer_mode: value as DesktopPreferences["pet_renderer_mode"] })} options={[{ value: "auto", label: "Automatic" }, { value: "liquid", label: "Liquid Glass" }, { value: "compatibility", label: "Compatibility" }]} />
      <HealthRow
        icon={<Gauge size={17} />}
        label="Active renderer"
        status={rendererHealthLabel(data.rendererHealth)}
        tone={rendererHealthTone(data.rendererHealth)}
      />
      <SettingSelect icon={<ShieldCheck size={17} />} label="Glass privacy" detail={data.preferences.pet_optics_mode === "standard" ? "No desktop capture; uses a procedural optical environment" : "GPU-acquires the active monitor while enabled; samples only the pet area and never saves or uploads pixels"} value={data.preferences.pet_optics_mode} disabled={busy || data.preferences.pet_renderer_mode === "compatibility"} onChange={(value) => void updatePreferences({ pet_optics_mode: value as DesktopPreferences["pet_optics_mode"] })} options={[{ value: "standard", label: "Standard privacy" }, { value: "enhanced", label: "Enhanced refraction" }]} />
      <SettingSelect icon={<Gauge size={17} />} label="Animation frame rate" value={String(data.preferences.pet_target_fps)} disabled={busy || !data.preferences.pet_motion_enabled} onChange={(value) => void updatePreferences({ pet_target_fps: Number(value) as DesktopPreferences["pet_target_fps"] })} options={[{ value: "60", label: "60 FPS" }, { value: "144", label: "144 FPS" }, { value: "300", label: "300 FPS" }]} />
      <SettingRange label="Size" value={data.preferences.pet_size_percent} min={75} max={150} step={5} suffix="%" disabled={busy} onCommit={(value) => void updatePreferences({ pet_size_percent: value })} />
      <SettingRange label="Opacity" value={data.preferences.pet_opacity_percent} min={40} max={100} step={2} suffix="%" disabled={busy} onCommit={(value) => void updatePreferences({ pet_opacity_percent: value })} />
      <SettingToggle label="Animate liquid motion" checked={data.preferences.pet_motion_enabled} disabled={busy} onChange={(value) => void updatePreferences({ pet_motion_enabled: value })} />
      <SettingToggle label="Show particles" checked={data.preferences.pet_particles_enabled} disabled={busy} onChange={(value) => void updatePreferences({ pet_particles_enabled: value })} />
      <SettingToggle label="Expand on hover" checked={data.preferences.pet_hover_enabled} disabled={busy} onChange={(value) => void updatePreferences({ pet_hover_enabled: value })} />
      <SettingRange label="Hover dwell" value={data.preferences.pet_hover_dwell_ms} min={100} max={1000} step={50} suffix=" ms" disabled={busy || !data.preferences.pet_hover_enabled} onCommit={(value) => void updatePreferences({ pet_hover_dwell_ms: value })} />
      <SettingToggle label="Do not disturb" checked={data.preferences.pet_do_not_disturb} disabled={busy} onChange={(value) => void updatePreferences({ pet_do_not_disturb: value })} />
      <SettingToggle label="Remember position" checked={data.preferences.pet_remember_position} disabled={busy} onChange={(value) => void updatePreferences({ pet_remember_position: value })} />
    </Category>;
    case "advanced": return <Category title="Advanced" subtitle="Diagnostics and developer tools">
      <SettingToggle label="Developer mode" checked={data.preferences.developer_mode} disabled={busy} onChange={(value) => void updatePreferences({ developer_mode: value })} />
      <HealthRow icon={<ShieldCheck size={17} />} label="Settings capability" status="Restricted settings methods only" tone="success" />
    </Category>;
  }
}

type ProjectManagementAction =
  | { kind: "archive-delete"; item: ProjectArchivedItem }
  | { kind: "trash-purge"; item: TrashItem }
  | { kind: "trash-purge-all" };

function ProjectManagementPanel(props: Parameters<typeof SettingsCategory>[0]) {
  const { data, busy, client, act, reload, updatePreferences } = props;
  const [tab, setTab] = useState<"archived" | "trash">("archived");
  const [query, setQuery] = useState("");
  const [pending, setPending] = useState<ProjectManagementAction | null>(null);
  const normalized = query.trim().toLocaleLowerCase();
  const archived = data.archivedProjects.filter((item) =>
    item.project.name.toLocaleLowerCase().includes(normalized),
  );
  const trash = data.trashItems.filter((item) =>
    `${item.title} ${item.source_project_title ?? ""}`.toLocaleLowerCase().includes(normalized),
  );
  const estimatedBytes = data.trashItems.reduce((total, item) => total + item.estimated_bytes, 0);
  const runAutoPurge = () => act(async () => {
    await client.projectManagement.trash.purgeAll({
      user_confirmed: true,
      deleted_before: new Date(Date.now() - 30 * 24 * 60 * 60 * 1_000).toISOString(),
      maintenance: true,
    });
    markTrashMaintenanceSucceeded();
    await reload();
  });

  return (
    <section className="project-management" aria-labelledby="project-management-heading">
      <header>
        <div>
          <HardDrive size={17} />
          <span><strong id="project-management-heading">Project management</strong><small>Archived work and Recently deleted</small></span>
        </div>
        <span>{data.archivedProjects.length + data.trashItems.length} items</span>
      </header>
      <div className="settings-tabs" role="tablist" aria-label="Project management views">
        <button type="button" role="tab" aria-selected={tab === "archived"} className={tab === "archived" ? "active" : ""} onClick={() => setTab("archived")}>Archived <span>{data.archivedProjects.length}</span></button>
        <button type="button" role="tab" aria-selected={tab === "trash"} className={tab === "trash" ? "active" : ""} onClick={() => setTab("trash")}>Recently deleted <span>{data.trashItems.length}</span></button>
      </div>
      <label className="project-management-search">
        <Search size={14} />
        <span className="sr-only">Search project management</span>
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={tab === "archived" ? "Search archived projects" : "Search recently deleted"} />
      </label>

      {tab === "archived" ? (
        <div className="project-management-list">
          {archived.length === 0 ? <p className="settings-empty">No archived projects</p> : archived.map((item) => (
            <div className="project-management-row" key={item.project.id}>
              <FolderKanban size={16} />
              <span><strong>{item.project.name}</strong><small>{item.thread_count} chat{item.thread_count === 1 ? "" : "s"} · Archived {formatHistoryDate(item.archived_at)}</small></span>
              <button className="secondary-command" type="button" disabled={busy} onClick={() => void act(async () => {
                await client.projectManagement.archived.restore({
                  project_id: item.project.id,
                  expected_revision: item.project.metadata_revision,
                });
                await reload();
              })}><RotateCcw size={14} />Restore</button>
              <button className="danger-icon" type="button" aria-label={`Delete archived project ${item.project.name}`} title="Move to Recently deleted" disabled={busy} onClick={() => setPending({ kind: "archive-delete", item })}><Trash2 size={14} /></button>
            </div>
          ))}
        </div>
      ) : (
        <>
          <div className="project-management-summary">
            <span>{data.trashItems.length} items · {formatBytes(estimatedBytes)} estimated</span>
            <button className="secondary-command" type="button" disabled={busy || data.trashItems.length === 0} onClick={() => setPending({ kind: "trash-purge-all" })}>Clear all</button>
          </div>
          <div className="project-management-list">
            {trash.length === 0 ? <p className="settings-empty">Recently deleted is empty</p> : trash.map((item) => (
              <div className="project-management-row" key={`${item.item_type}:${item.item_id}`}>
                {item.item_type === "project" ? <FolderKanban size={16} /> : <MessageSquareText size={16} />}
                <span><strong>{item.title}</strong><small>{trashTypeLabel(item)}{item.source_project_title ? ` · ${item.source_project_title}` : ""} · Deleted {formatHistoryDate(item.deleted_at)}</small></span>
                <button className="secondary-command" type="button" disabled={busy || !item.can_restore} title={item.can_restore ? "Restore" : "Restore the parent project first"} onClick={() => void act(async () => {
                  await client.projectManagement.trash.restore({
                    item_type: item.item_type,
                    item_id: item.item_id,
                    expected_revision: item.metadata_revision,
                    user_confirmed: true,
                  });
                  await reload();
                })}><RotateCcw size={14} />Restore</button>
                <button className="danger-icon" type="button" aria-label={`Permanently delete ${item.title}`} title="Permanently delete" disabled={busy} onClick={() => setPending({ kind: "trash-purge", item })}><Trash2 size={14} /></button>
              </div>
            ))}
          </div>
          <SettingToggle
            label="Permanently delete after 30 days"
            detail="Runs at most once per day on this device; disabled by default"
            checked={data.preferences.trash_auto_purge_30_days}
            disabled={busy}
            onChange={(value) => void updatePreferences({ trash_auto_purge_30_days: value })}
          />
          {data.autoPurgeError ? <div className="settings-callout project-management-retry"><span>Automatic cleanup did not finish; remaining items were kept.</span><button className="secondary-command" type="button" disabled={busy} onClick={() => void runAutoPurge()}>Retry cleanup</button></div> : null}
        </>
      )}

      <ActionDialog
        open={pending !== null}
        busy={busy}
        destructive
        title={pending === null ? "Delete" : managementActionTitle(pending)}
        description={pending === null ? "" : managementActionDescription(pending, estimatedBytes)}
        confirmLabel={pending?.kind === "archive-delete" ? "Move to Recently deleted" : "Permanently delete"}
        onCancel={() => setPending(null)}
        onConfirm={async () => {
          if (pending === null) return;
          await act(async () => {
            if (pending.kind === "archive-delete") {
              await client.projectManagement.archived.delete({
                project_id: pending.item.project.id,
                expected_revision: pending.item.project.metadata_revision,
                cancel_active: false,
                user_confirmed: true,
              });
            } else if (pending.kind === "trash-purge") {
              await client.projectManagement.trash.purge({
                item_type: pending.item.item_type,
                item_id: pending.item.item_id,
                expected_revision: pending.item.metadata_revision,
                user_confirmed: true,
              });
            } else {
              await client.projectManagement.trash.purgeAll({
                user_confirmed: true,
                deleted_before: null,
                maintenance: false,
              });
            }
            await reload();
          });
          setPending(null);
        }}
      />
    </section>
  );
}

function managementActionTitle(action: ProjectManagementAction) {
  if (action.kind === "archive-delete") return "Move archived project to Recently deleted";
  if (action.kind === "trash-purge-all") return "Permanently delete all items";
  return "Permanently delete item";
}

function managementActionDescription(action: ProjectManagementAction, estimatedBytes: number) {
  if (action.kind === "archive-delete") {
    return `Move “${action.item.project.name}” and its ${action.item.thread_count} chat${action.item.thread_count === 1 ? "" : "s"} to Recently deleted. It can still be restored.`;
  }
  if (action.kind === "trash-purge-all") {
    return `Permanently delete all user content in Recently deleted and release approximately ${formatBytes(estimatedBytes)}. Minimal synchronization tombstones and audit identifiers remain.`;
  }
  return `Permanently delete “${action.item.title}”. Transcript, attachments, derived indexes and exclusive Workspace content cannot be restored.`;
}

function trashTypeLabel(item: TrashItem) {
  if (item.item_type === "project") return `${item.thread_count} project chat${item.thread_count === 1 ? "" : "s"}`;
  return item.item_type === "project_conversation" ? "Project chat" : "Chat";
}

function formatHistoryDate(value: string) {
  return new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short", day: "numeric" }).format(new Date(value));
}

function formatBytes(value: number) {
  if (value < 1_024) return `${value} B`;
  if (value < 1_048_576) return `${(value / 1_024).toFixed(1)} KiB`;
  if (value < 1_073_741_824) return `${(value / 1_048_576).toFixed(1)} MiB`;
  return `${(value / 1_073_741_824).toFixed(1)} GiB`;
}

async function listAllArchivedProjects(client: SettingsClient): Promise<ProjectArchivedItem[]> {
  const items: ProjectArchivedItem[] = [];
  const seenCursors = new Set<string>();
  let cursor: string | null = null;
  do {
    const page = await client.projectManagement.archived.list({ limit: 100, cursor });
    items.push(...page.items);
    cursor = page.next_cursor ?? null;
    if (cursor !== null) {
      if (seenCursors.has(cursor)) throw new Error("Archived project pagination did not advance");
      seenCursors.add(cursor);
    }
  } while (cursor !== null);
  return items;
}

async function listAllTrashItems(client: SettingsClient): Promise<TrashItem[]> {
  const items: TrashItem[] = [];
  const seenCursors = new Set<string>();
  let cursor: string | null = null;
  do {
    const page = await client.projectManagement.trash.list({ limit: 100, cursor });
    items.push(...page.items);
    cursor = page.next_cursor ?? null;
    if (cursor !== null) {
      if (seenCursors.has(cursor)) throw new Error("Trash pagination did not advance");
      seenCursors.add(cursor);
    }
  } while (cursor !== null);
  return items;
}

function ModelsPanel(props: Parameters<typeof SettingsCategory>[0]) {
  const { data, busy, client, act, reload, updateData } = props;
  const [apiKey, setApiKey] = useState("");
  const [confirmCredentialDelete, setConfirmCredentialDelete] = useState(false);
  const selectedEntry = data.modelSelection.mode === "manual"
    ? data.modelCatalog.items.find((entry) => entry.model_id === data.modelSelection.model_id)
    : null;
  const updatePolicy = (patch: Partial<Pick<ModelSelectionPreference, "mode" | "model_id" | "allow_free_fallback" | "zero_data_retention">>) =>
    act(async () => {
      const current = data.modelSelection;
      const saved = await client.models.selection.update({
        mode: patch.mode ?? current.mode,
        model_id: patch.model_id === undefined ? current.model_id : patch.model_id,
        allow_free_fallback: patch.allow_free_fallback ?? current.allow_free_fallback,
        zero_data_retention: patch.zero_data_retention ?? current.zero_data_retention,
        expected_revision: current.revision,
        idempotency_key: `settings:model-selection:${current.revision}:${crypto.randomUUID()}`,
      });
      updateData((value) => value === null ? null : { ...value, modelSelection: saved });
    });
  return <Category title="Models" subtitle="Providers and credentials">
    <div className="settings-model-account">
      <div className="settings-form-heading"><KeyRound size={17} /><div><strong>OpenRouter</strong><span>{data.openRouterConfigured ? "Credential protected by Windows" : "Credential not configured"}</span></div></div>
      <span className={`settings-account-status ${data.modelCatalog.account.credential_status}`}>{titleCase(data.modelCatalog.account.credential_status)}</span>
    </div>
    <div className="settings-model-selection">
      <div><strong>Global selection</strong><small>{data.modelSelection.mode === "auto" ? "Auto routes each new Turn" : selectedEntry?.display_name ?? "Selected model unavailable"}</small></div>
      {data.modelSelection.mode !== "auto" ? <button className="secondary-command" type="button" disabled={busy} onClick={() => void updatePolicy({ mode: "auto", model_id: null })}><Sparkles size={14} />Use Auto</button> : <span className="settings-selection-badge"><Check size={13} />Auto</span>}
    </div>
    <SettingToggle label="Zero data retention routing" detail="Require compatible ZDR endpoints when available" checked={data.modelSelection.zero_data_retention} disabled={busy} onChange={(value) => void updatePolicy({ zero_data_retention: value })} />
    <SettingToggle label="Allow free fallback" detail="Disabled by default; Auto may use approved free text models" checked={data.modelSelection.allow_free_fallback} disabled={busy} onChange={(value) => void updatePolicy({ allow_free_fallback: value })} />
    <div className="settings-section-command"><span>{data.modelCatalog.items.length} approved models · {data.modelCatalog.stale ? "Last known catalog" : "Catalog current"}</span><button className="secondary-command" type="button" disabled={busy} onClick={() => void act(async () => { const catalog = await client.models.catalog.refresh(); updateData((value) => value === null ? null : { ...value, modelCatalog: catalog }); })}><RefreshCw size={14} />Refresh catalog</button></div>
    <div className="settings-model-catalog">
      {data.modelCatalog.items.map((entry) => <div className="settings-model-row" key={entry.model_id}>
        <span className={`settings-health-dot ${entry.availability}`} />
        <div><strong>{entry.display_name}</strong><small>{modelPurpose(entry.category)}</small></div>
        <span>{entry.paid ? "Paid" : "Free"} · {modelModality(entry.endpoint_kind)}</span>
      </div>)}
    </div>
    <form className="settings-form" onSubmit={(event) => { event.preventDefault(); void act(async () => { await client.providers.configureOpenRouter({ api_key: apiKey }); setApiKey(""); await reload(); }); }}>
      <div className="settings-form-heading"><KeyRound size={17} /><div><strong>OpenRouter credential</strong><span>{data.openRouterConfigured ? "Configured" : "Not configured"}</span></div></div>
      <label><span>API key</span><input type="password" autoComplete="off" required value={apiKey} disabled={busy} placeholder={data.openRouterConfigured ? "Enter a new key to replace" : "sk-or-v1-..."} onChange={(event) => setApiKey(event.target.value)} /></label>
      <div className="settings-form-actions"><button className="primary-command" type="submit" disabled={busy || !apiKey.trim()}><KeyRound size={14} />Save and connect</button>
        {data.openRouterConfigured ? <button className="danger-icon" type="button" aria-label="Remove OpenRouter credential" title="Remove OpenRouter credential" disabled={busy} onClick={() => setConfirmCredentialDelete(true)}><Trash2 size={15} /></button> : null}
      </div>
    </form>
    <RealtimeCredentialForm provider="gemini" label="Gemini Live" configured={data.realtimeCredentials.gemini} busy={busy} client={client} act={act} reload={reload} />
    <RealtimeCredentialForm provider="zhipu" label="Zhipu GLM Realtime" configured={data.realtimeCredentials.zhipu} busy={busy} client={client} act={act} reload={reload} />
    <ActionDialog
      open={confirmCredentialDelete}
      busy={busy}
      destructive
      title="Remove OpenRouter credential"
      description="The encrypted credential will be removed from this device. Existing chats and artifacts remain available."
      confirmLabel="Remove"
      onCancel={() => setConfirmCredentialDelete(false)}
      onConfirm={async () => {
        await act(async () => {
          await client.providers.deleteOpenRouter();
          await reload();
        });
        setConfirmCredentialDelete(false);
      }}
    />
  </Category>;
}

function RealtimeCredentialForm({ provider, label, configured, busy, client, act, reload }: {
  provider: "gemini" | "zhipu";
  label: string;
  configured: boolean;
  busy: boolean;
  client: SettingsClient;
  act(operation: () => Promise<void>): Promise<void>;
  reload(): Promise<void>;
}) {
  const [apiKey, setApiKey] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  return <>
    <form className="settings-form" onSubmit={(event) => { event.preventDefault(); void act(async () => { await client.providers.configureRealtime({ provider, api_key: apiKey }); setApiKey(""); await reload(); }); }}>
      <div className="settings-form-heading"><KeyRound size={17} /><div><strong>{label}</strong><span>{configured ? "Credential protected by Windows" : "Credential not configured"}</span></div></div>
      <label><span>API key</span><input type="password" autoComplete="off" required value={apiKey} disabled={busy} placeholder={configured ? "Enter a new key to replace" : "API key"} onChange={(event) => setApiKey(event.target.value)} /></label>
      <div className="settings-form-actions"><button className="primary-command" type="submit" disabled={busy || !apiKey.trim()}><KeyRound size={14} />Save credential</button>{configured ? <button className="danger-icon" type="button" aria-label={`Remove ${label} credential`} disabled={busy} onClick={() => setConfirmDelete(true)}><Trash2 size={15} /></button> : null}</div>
    </form>
    <ActionDialog open={confirmDelete} busy={busy} destructive title={`Remove ${label} credential`} description="The encrypted credential will be removed from this device. Saved session audits and game memories remain." confirmLabel="Remove" onCancel={() => setConfirmDelete(false)} onConfirm={async () => { await act(async () => { await client.providers.deleteRealtime(provider); await reload(); }); setConfirmDelete(false); }} />
  </>;
}

function modelPurpose(category: ModelCatalogPage["items"][number]["category"]): string {
  switch (category) {
    case "primary": return "Primary orchestration and general work";
    case "strongest": return "Complex reasoning and review";
    case "code": return "Code implementation and debugging";
    case "image": return "Image generation";
    case "music": return "Music generation";
    case "video": return "Video generation";
    case "free_general": return "Free general model";
    case "free_code": return "Free code model";
  }
}

function modelModality(endpoint: ModelCatalogPage["items"][number]["endpoint_kind"]): string {
  switch (endpoint) {
    case "images": return "Image";
    case "audio": return "Music";
    case "videos": return "Video";
    case "chat": return "Text";
  }
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

function InstalledSkills({ data, busy, client, act, reload }: Pick<Parameters<typeof SettingsCategory>[0], "data" | "busy" | "client" | "act" | "reload">) {
  const [pendingRemoval, setPendingRemoval] = useState<Skill | null>(null);
  if (data.skills.length === 0) return <p className="settings-empty">No Skills installed</p>;
  return <><div className="settings-extension-list">{data.skills.map((skill) => <section className="settings-mcp-server" key={`${skill.name}@${skill.version}`}><div className="settings-extension-row"><span className={`settings-health-dot ${skill.available ? "available" : "unavailable"}`} /><div><strong>{skill.name}</strong><small>{skill.description}</small></div><code>{skill.version}</code><label className="compact-switch"><input type="checkbox" aria-label={`Enable ${skill.name}`} checked={skill.enabled} disabled={busy} onChange={(event) => void act(async () => { await client.extensions.setSkillEnabled({ name: skill.name, expected_content_sha256: skill.content_sha256, enabled: event.target.checked, idempotency_key: `settings:skill:${skill.name}:${skill.content_sha256}:enabled:${event.target.checked}` }); await reload(); })} /><span /></label><button className="danger-icon" type="button" aria-label={`Remove ${skill.name}`} disabled={busy} onClick={() => setPendingRemoval(skill)}><Trash2 size={14} /></button></div></section>)}</div><ActionDialog open={pendingRemoval !== null} busy={busy} destructive title="Remove Skill" description={pendingRemoval === null ? "" : `Remove ${pendingRemoval.name} and its model-visible tool from this device?`} confirmLabel="Remove" onCancel={() => setPendingRemoval(null)} onConfirm={async () => { if (pendingRemoval === null) return; const skill = pendingRemoval; await act(async () => { await client.extensions.removeSkill({ name: skill.name, expected_content_sha256: skill.content_sha256, idempotency_key: `settings:skill:${skill.name}:${skill.content_sha256}:remove` }); await reload(); }); setPendingRemoval(null); }} /></>;
}

function SkillStore(props: Pick<Parameters<typeof SettingsCategory>[0], "data" | "busy" | "client" | "act" | "reload">) {
  return <StoreSurface {...props} kind="skills" />;
}

function McpStore(props: Pick<Parameters<typeof SettingsCategory>[0], "data" | "busy" | "client" | "act" | "reload">) {
  return <StoreSurface {...props} kind="mcp" />;
}

function StoreSurface({ data, busy, client, act, reload, kind }: Pick<Parameters<typeof SettingsCategory>[0], "data" | "busy" | "client" | "act" | "reload"> & { kind: "skills" | "mcp" }) {
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

function SkillImportForm({ busy, client, act, reload, onDone }: Pick<Parameters<typeof SettingsCategory>[0], "busy" | "client" | "act" | "reload"> & { onDone(): void }) {
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

function SkillCreateForm({ busy, client, act, reload, onDone }: Pick<Parameters<typeof SettingsCategory>[0], "busy" | "client" | "act" | "reload"> & { onDone(): void }) {
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

function McpServerList({ data, busy, client, act, reload }: Pick<Parameters<typeof SettingsCategory>[0], "data" | "busy" | "client" | "act" | "reload">) {
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

function permissionKey(revision: number, profile: string, overrides: Record<string, boolean>) {
  return `settings:permissions:${revision}:${profile}:${Object.entries(overrides).sort(([a], [b]) => a.localeCompare(b)).map(([key, value]) => `${key}=${value}`).join(",")}`;
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

function titleCase(value: string) { return `${value.charAt(0).toUpperCase()}${value.slice(1)}`; }
function messageOf(value: unknown) {
  if (value instanceof Error) return value.message;
  if (typeof value === "string" && value.trim() !== "") return value;
  if (
    typeof value === "object" &&
    value !== null &&
    "message" in value &&
    typeof value.message === "string"
  ) {
    return value.message;
  }
  return "Settings request failed";
}

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
  if (health.status === "idle") return "Stopped · starts on first playback";
  if (health.status === "ready") return `Ready at ${health.sample_rate / 1000} kHz`;
  if (health.status === "warming") return "Warming model";
  if (health.status === "model_missing") return "Model not installed";
  return health.error_code ?? "Voice unavailable";
}

function rendererHealthLabel(health: PresenceRendererHealth): string {
  const rate = health.effective_fps > 0
    ? health.monitor_refresh_hz > 0
      ? ` · ${health.effective_fps} FPS on ${health.monitor_refresh_hz} Hz`
      : ` · ${health.effective_fps} FPS`
    : "";
  if (health.actual_backend === "native_liquid_glass") {
    const latency = health.capture_to_present_p95_ms > 0
      ? ` · ${health.capture_to_present_p95_ms.toFixed(1)} ms p95`
      : "";
    return `Native DDA Liquid Glass${rate}${latency}`;
  }
  if (health.actual_backend === "native_identity_fallback") {
    return `Host Backdrop identity fallback · no refraction${rate}`;
  }
  if (health.actual_backend === "webgl_compatibility") {
    return `WebGL compatibility${rate}`;
  }
  if (health.actual_backend === "canvas_compatibility") {
    return `Canvas compatibility${rate}`;
  }
  if (health.status === "initializing") return "Starting renderer";
  return health.fallback_reason ?? health.error_code ?? "Renderer unavailable";
}

function rendererHealthTone(
  health: PresenceRendererHealth,
): "neutral" | "success" | "error" {
  if (
    health.actual_backend === "native_liquid_glass" &&
    health.status === "running" &&
    health.fallback_reason === null
  ) {
    return "success";
  }
  if (health.status === "failed") return "error";
  return "neutral";
}
