import {
  ArrowLeft,
  Bot,
  Brain,
  Check,
  ChevronRight,
  Gamepad2,
  Gauge,
  KeyRound,
  Languages,
  Mic2,
  MonitorCog,
  Palette,
  PawPrint,
  Play,
  RefreshCw,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Trash2,
  Volume2,
  X,
} from "lucide-react";
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { m } from "motion/react";

import type {
  CapabilityManifest,
  ExecutionSettings,
  ExtensionCatalogEntry,
  ModelCatalogPage,
  ModelSelectionPreference,
  MemorySettings,
  McpServer,
  Skill,
  ProjectArchivedItem,
  TrashItem,
  SettingsCategoryId,
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
import { ProjectManagementPanel } from "./SettingsProjectManagement";
import { RealtimeReadinessCard } from "./RealtimeReadinessCard";
import { ExtensionsPanel } from "./SettingsExtensions";
import {
  messageOf,
  titleCase,
  type RealtimeCredentialView,
  type SettingsCategoryProps,
  type SettingsData,
} from "./settingsShared";
import {
  Category,
  HealthRow,
  SettingRange,
  SettingSelect,
  SettingToggle,
} from "./settingsControls";
import "./settings-app.css";

export type { SettingsCategoryId } from "../core/client";

const categories: readonly {
  id: SettingsCategoryId;
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

interface GeneralSettingsData {
  archivedProjects: ProjectArchivedItem[];
  trashItems: TrashItem[];
  autoPurgeError: string | null;
}

interface ModelSettingsData {
  openRouterConfigured: boolean;
  openRouterAccountId: string | null;
  realtimeCredentials: Record<"gemini" | "zhipu", RealtimeCredentialView>;
  modelCatalog: ModelCatalogPage;
  modelSelection: ModelSelectionPreference;
}

interface PermissionSettingsData {
  permissions: ExecutionSettings;
  capabilities: CapabilityManifest;
}

interface ExtensionSettingsData {
  extensionCatalog: ExtensionCatalogEntry[];
  skills: Skill[];
  servers: McpServer[];
  latestTaskId: string | null;
}

interface KnowledgeSettingsData extends KnowledgePrivacyData {
  memorySettings: MemorySettings;
}

export const settingsQueryKeys = {
  preferences: ["settings", "common", "preferences"] as const,
  general: ["settings", "category", "general"] as const,
  models: ["settings", "category", "models"] as const,
  voice: ["settings", "category", "voice"] as const,
  permissions: ["settings", "category", "permissions"] as const,
  extensions: ["settings", "category", "extensions"] as const,
  knowledge: ["settings", "category", "knowledge"] as const,
  pet: ["settings", "category", "pet"] as const,
};

interface SettingsNavigationRequest {
  sequence: number;
  category?: SettingsCategoryId;
}

interface SettingsAppProps {
  client: SettingsClient;
  initialPreferences?: DesktopPreferences | null;
  navigationRequest?: SettingsNavigationRequest;
  onBack?(): void;
  queryClient?: QueryClient;
}

export function SettingsApp(props: SettingsAppProps) {
  const [ownedQueryClient] = useState(() => new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        refetchOnWindowFocus: false,
      },
    },
  }));
  return (
    <QueryClientProvider client={props.queryClient ?? ownedQueryClient}>
      <SettingsContent {...props} />
    </QueryClientProvider>
  );
}

function SettingsContent({
  client,
  initialPreferences = null,
  navigationRequest,
  onBack,
}: SettingsAppProps) {
  const queryClient = useQueryClient();
  const [category, setCategory] = useState<SettingsCategoryId>(
    navigationRequest?.category ?? "general",
  );
  const [visited, setVisited] = useState<ReadonlySet<SettingsCategoryId>>(
    () => new Set([navigationRequest?.category ?? "general"]),
  );
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const contentRef = useRef<HTMLElement | null>(null);
  const lastNavigationSequence = useRef(0);

  useEffect(() => {
    if (
      navigationRequest === undefined ||
      navigationRequest.sequence <= lastNavigationSequence.current
    ) {
      return;
    }
    lastNavigationSequence.current = navigationRequest.sequence;
    if (navigationRequest.category !== undefined) {
      setCategory(navigationRequest.category);
      setVisited((current) => new Set(current).add(navigationRequest.category as SettingsCategoryId));
    }
    setQuery("");
  }, [navigationRequest]);

  useEffect(() => {
    if (initialPreferences === null) return;
    queryClient.setQueryData(settingsQueryKeys.preferences, initialPreferences);
  }, [initialPreferences, queryClient]);

  const preferencesQuery = useQuery({
    queryKey: settingsQueryKeys.preferences,
    queryFn: () => client.preferences.get(),
    initialData: initialPreferences ?? undefined,
    staleTime: Number.POSITIVE_INFINITY,
  });
  const generalQuery = useQuery({
    queryKey: settingsQueryKeys.general,
    enabled: visited.has("general") && preferencesQuery.data !== undefined,
    staleTime: 60_000,
    queryFn: async (): Promise<GeneralSettingsData> => {
      const [archivedProjects, initialTrash] = await Promise.all([
        listAllArchivedProjects(client),
        listAllTrashItems(client),
      ]);
      let trashItems = initialTrash;
      let autoPurgeError = automaticTrashMaintenanceFailed()
        ? "Automatic cleanup did not finish"
        : null;
      const maintenance = await runAutomaticTrashMaintenance(
        preferencesQuery.data as DesktopPreferences,
        client.projectManagement.trash,
      );
      if (maintenance.attempted) {
        if (maintenance.failed) {
          autoPurgeError = "Automatic cleanup did not finish";
        } else {
          trashItems = await listAllTrashItems(client);
          autoPurgeError = null;
        }
      }
      return { archivedProjects, trashItems, autoPurgeError };
    },
  });
  const modelsQuery = useQuery({
    queryKey: settingsQueryKeys.models,
    enabled: visited.has("models"),
    staleTime: 6 * 60 * 60 * 1_000,
    queryFn: async (): Promise<ModelSettingsData> => {
      const readRealtime = async (
        provider: "gemini" | "zhipu",
      ): Promise<RealtimeCredentialView> => {
        try {
          const status = await client.providers.realtimeStatus(provider);
          return { configured: status.configured, hint: status.hint ?? null, error: false };
        } catch {
          return { configured: false, hint: null, error: true };
        }
      };
      const [status, gemini, zhipu, modelCatalog, modelSelection] = await Promise.all([
        client.providers.openRouterStatus(),
        readRealtime("gemini"),
        readRealtime("zhipu"),
        client.models.catalog.list(),
        client.models.selection.get(),
      ]);
      return {
        openRouterConfigured: status.configured,
        openRouterAccountId: status.account_id,
        realtimeCredentials: { gemini, zhipu },
        modelCatalog,
        modelSelection,
      };
    },
  });
  const voiceQuery = useQuery({
    queryKey: settingsQueryKeys.voice,
    enabled: visited.has("voice"),
    staleTime: 30_000,
    queryFn: () => client.voice.health().catch(() => unavailableVoiceHealth()),
  });
  const permissionsQuery = useQuery({
    queryKey: settingsQueryKeys.permissions,
    enabled: visited.has("permissions"),
    staleTime: 60_000,
    queryFn: async (): Promise<PermissionSettingsData> => {
      const [permissions, capabilities] = await Promise.all([
        client.permissions.get(),
        client.permissions.capabilities(),
      ]);
      return { permissions, capabilities };
    },
  });
  const extensionsQuery = useQuery({
    queryKey: settingsQueryKeys.extensions,
    enabled: visited.has("extensions"),
    staleTime: 60_000,
    queryFn: async (): Promise<ExtensionSettingsData> => {
      const [catalog, skills, servers, tasks] = await Promise.all([
        client.extensions.catalog(),
        client.extensions.skills(),
        client.extensions.servers(),
        client.context.latestTask(),
      ]);
      return {
        extensionCatalog: catalog.items,
        skills: skills.items,
        servers: servers.items,
        latestTaskId: tasks.items.at(-1)?.id ?? null,
      };
    },
  });
  const knowledgeQuery = useQuery({
    queryKey: settingsQueryKeys.knowledge,
    enabled: visited.has("knowledge"),
    staleTime: 60_000,
    queryFn: async (): Promise<KnowledgeSettingsData> => {
      const [memorySettings, knowledgePrivacy] = await Promise.all([
        client.memory.settings.get(),
        loadKnowledgePrivacy(client),
      ]);
      return { memorySettings, ...knowledgePrivacy };
    },
  });
  const petQuery = useQuery({
    queryKey: settingsQueryKeys.pet,
    enabled: visited.has("pet"),
    staleTime: 10_000,
    queryFn: async () =>
      await client.pet.rendererHealth().catch(() => null) ?? UNAVAILABLE_RENDERER_HEALTH,
  });

  const data = useMemo<SettingsData | null>(() => {
    const preferences = preferencesQuery.data;
    if (preferences === undefined) return null;
    return {
      preferences,
      memorySettings: knowledgeQuery.data?.memorySettings ?? emptyMemorySettings(),
      openRouterConfigured: modelsQuery.data?.openRouterConfigured ?? false,
      openRouterAccountId: modelsQuery.data?.openRouterAccountId ?? null,
      realtimeCredentials: modelsQuery.data?.realtimeCredentials ?? {
        gemini: { configured: false, hint: null, error: false },
        zhipu: { configured: false, hint: null, error: false },
      },
      modelCatalog: modelsQuery.data?.modelCatalog ?? emptyModelCatalog(),
      modelSelection: modelsQuery.data?.modelSelection ?? emptyModelSelection(),
      permissions: permissionsQuery.data?.permissions ?? emptyExecutionSettings(),
      capabilities: permissionsQuery.data?.capabilities ?? emptyCapabilityManifest(),
      extensionCatalog: extensionsQuery.data?.extensionCatalog ?? [],
      skills: extensionsQuery.data?.skills ?? [],
      servers: extensionsQuery.data?.servers ?? [],
      latestTaskId: extensionsQuery.data?.latestTaskId ?? null,
      voiceHealth: voiceQuery.data ?? unavailableVoiceHealth(),
      rendererHealth: petQuery.data ?? UNAVAILABLE_RENDERER_HEALTH,
      archivedProjects: generalQuery.data?.archivedProjects ?? [],
      trashItems: generalQuery.data?.trashItems ?? [],
      autoPurgeError: generalQuery.data?.autoPurgeError ?? null,
      knowledgeSources: knowledgeQuery.data?.knowledgeSources ?? [],
      memoryProposals: knowledgeQuery.data?.memoryProposals ?? [],
      obsidianHealth: knowledgeQuery.data?.obsidianHealth ?? unavailableObsidianHealth(),
      knowledgeDiagnostics: knowledgeQuery.data?.knowledgeDiagnostics ?? [],
    };
  }, [
    extensionsQuery.data,
    generalQuery.data,
    knowledgeQuery.data,
    modelsQuery.data,
    permissionsQuery.data,
    petQuery.data,
    preferencesQuery.data,
    voiceQuery.data,
  ]);

  useEffect(() => {
    if (preferencesQuery.data !== undefined) {
      applyDesktopPreferences(preferencesQuery.data);
    }
  }, [preferencesQuery.data]);

  const currentCategoryQuery = categoryQueryFor(
    category,
    {
      general: generalQuery,
      models: modelsQuery,
      voice: voiceQuery,
      permissions: permissionsQuery,
      extensions: extensionsQuery,
      knowledge: knowledgeQuery,
      pet: petQuery,
    },
  );

  const load = useCallback(async () => {
    setError(null);
    try {
      if (category === "appearance" || category === "advanced") {
        await preferencesQuery.refetch();
      } else {
        await currentCategoryQuery?.refetch();
      }
    } catch (caught) {
      setError(messageOf(caught));
    }
  }, [category, currentCategoryQuery, preferencesQuery]);

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
        queryClient.setQueryData(settingsQueryKeys.preferences, saved);
        applyDesktopPreferences(saved);
      });
    },
    [act, client.preferences, data, queryClient],
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
        queryClient.setQueryData<KnowledgeSettingsData>(
          settingsQueryKeys.knowledge,
          (current) => current === undefined
            ? { ...emptyKnowledgeSettingsData(), memorySettings: saved }
            : { ...current, memorySettings: saved },
        );
      });
    },
    [act, client.memory.settings, data, queryClient],
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
  const settingsReady =
    data !== null && currentCategoryQuery?.isPending !== true;

  return (
    <main
      className="settings-shell"
      aria-label="Fairy settings"
      data-settings-category={visibleCategory}
      data-settings-state={settingsReady ? "ready" : "loading"}
    >
      <aside className="settings-navigation">
        <header className="settings-brand">
          {onBack ? (
            <button
              className="settings-back"
              type="button"
              aria-label="Back to workspace"
              title="Back to workspace"
              onClick={onBack}
            >
              <ArrowLeft size={16} />
            </button>
          ) : null}
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
            return <button key={item.id} type="button" className={visibleCategory === item.id ? "active" : ""} onClick={() => {
              setCategory(item.id);
              setVisited((current) => new Set(current).add(item.id));
              setQuery("");
            }}>
              <Icon size={16} /><span>{item.label}</span><ChevronRight size={13} />
            </button>;
          })}
        </nav>
        <button className="settings-refresh" type="button" disabled={busy} onClick={() => void load()}>
          <RefreshCw size={14} /> Refresh
        </button>
      </aside>

      <m.section ref={contentRef} className="settings-content" key={visibleCategory} initial={{ opacity: 0.4 }} animate={{ opacity: 1 }}>
        {error ? <div className="settings-error" role="alert"><span>{error}</span><button type="button" onClick={() => setError(null)}><X size={14} /></button></div> : null}
        {data === null || currentCategoryQuery?.isPending === true ? (
          <SettingsCategorySkeleton id={visibleCategory} />
        ) : (
          <SettingsCategory
            id={visibleCategory}
            data={data}
            client={client}
            busy={busy}
            act={act}
            reload={load}
            updatePreferences={updatePreferences}
            updateMemorySettings={updateMemorySettings}
            updateData={(updater) => {
              const next = typeof updater === "function" ? updater(data) : updater;
              if (next === null) return;
              queryClient.setQueryData(settingsQueryKeys.preferences, next.preferences);
              queryClient.setQueryData<ModelSettingsData>(settingsQueryKeys.models, {
                openRouterConfigured: next.openRouterConfigured,
                openRouterAccountId: next.openRouterAccountId,
                realtimeCredentials: next.realtimeCredentials,
                modelCatalog: next.modelCatalog,
                modelSelection: next.modelSelection,
              });
              queryClient.setQueryData<PermissionSettingsData>(settingsQueryKeys.permissions, {
                permissions: next.permissions,
                capabilities: next.capabilities,
              });
            }}
          />
        )}
      </m.section>
    </main>
  );
}

type SettingsQueryHandle = {
  isPending: boolean;
  refetch(): Promise<unknown>;
};

function categoryQueryFor(
  category: SettingsCategoryId,
  queries: {
    general: SettingsQueryHandle;
    models: SettingsQueryHandle;
    voice: SettingsQueryHandle;
    permissions: SettingsQueryHandle;
    extensions: SettingsQueryHandle;
    knowledge: SettingsQueryHandle;
    pet: SettingsQueryHandle;
  },
): SettingsQueryHandle | undefined {
  if (category === "appearance" || category === "advanced") return undefined;
  return queries[category];
}

function SettingsCategorySkeleton({ id }: { id: SettingsCategoryId }) {
  const definition = categories.find((item) => item.id === id) ?? categories[0];
  return (
    <section className="settings-category" aria-busy="true">
      <header>
        <span>Settings</span>
        <h1>{definition.label}</h1>
      </header>
      <div className="settings-category-skeleton" role="status" aria-label={`Loading ${definition.label}`}>
        <span />
        <span />
        <span />
      </div>
    </section>
  );
}

function SettingsCategory(props: SettingsCategoryProps) {
  const { id, data, busy, updatePreferences, updateMemorySettings } = props;
  const [localReady, setLocalReady] = useState(false);
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
      <h2 className="settings-section-title">Realtime Companion Beta</h2>
      <SettingToggle label="Enable Realtime Beta" detail="Off by default. Starting a session still requires explicit microphone and screen consent." checked={data.preferences.realtime_beta_enabled} disabled={busy} onChange={(value) => void updatePreferences({ realtime_beta_enabled: value })} />
      <RealtimeReadinessCard
        client={props.client}
        profile={data.preferences.realtime_activity_profile}
        disabled={busy}
        onReadinessChange={setLocalReady}
      />
      <SettingSelect icon={<Gamepad2 size={17} />} label="Backend" detail="Local Beta is selectable only after the complete readiness report passes. Cloud Live remains available." value={data.preferences.realtime_backend} disabled={busy} onChange={(value) => void updatePreferences({ realtime_backend: value as DesktopPreferences["realtime_backend"] })} options={[{ value: "auto", label: "Auto" }, { value: "local_mini_cpm_o45", label: "Local MiniCPM-o 4.5 Beta", disabled: !localReady }, { value: "cloud_live", label: "Cloud Live" }]} />
      <SettingSelect icon={<MonitorCog size={17} />} label="Cloud provider" detail="Used only by Cloud Live or an approved cloud fallback" value={data.preferences.realtime_cloud_provider} disabled={busy} onChange={(value) => void updatePreferences({ realtime_cloud_provider: value as DesktopPreferences["realtime_cloud_provider"] })} options={[{ value: "gemini_live", label: "Gemini Live" }, { value: "glm_realtime_flash", label: "GLM Realtime Flash" }, { value: "glm_realtime_air", label: "GLM Realtime Air" }]} />
      <SettingSelect icon={<Gauge size={17} />} label="Activity profile" value={data.preferences.realtime_activity_profile} disabled={busy} onChange={(value) => void updatePreferences({ realtime_activity_profile: value as DesktopPreferences["realtime_activity_profile"] })} options={[{ value: "auto", label: "Auto" }, { value: "game", label: "Game" }, { value: "focus", label: "Focus" }]} />
      <SettingSelect icon={<SlidersHorizontal size={17} />} label="Interaction intensity" value={data.preferences.realtime_interaction_intensity} disabled={busy} onChange={(value) => void updatePreferences({ realtime_interaction_intensity: value as DesktopPreferences["realtime_interaction_intensity"] })} options={[{ value: "quiet", label: "Quiet" }, { value: "standard", label: "Standard" }, { value: "active", label: "Active" }]} />
      <SettingSelect icon={<Volume2 size={17} />} label="Voice output" detail="Fairy voice uses the local Voice Worker; text-only never starts voice playback" value={data.preferences.realtime_voice_output} disabled={busy} onChange={(value) => void updatePreferences({ realtime_voice_output: value as DesktopPreferences["realtime_voice_output"] })} options={[{ value: "fairy_voice", label: "Fairy voice" }, { value: "provider_native_voice", label: "Provider native voice" }, { value: "text_only", label: "Text only" }]} />
      <SettingToggle label="Allow cloud fallback" detail="A Local session never moves to Cloud without this explicit preference and a governed transition" checked={data.preferences.realtime_allow_cloud_fallback} disabled={busy} onChange={(value) => void updatePreferences({ realtime_allow_cloud_fallback: value })} />
      <SettingToggle label="Share application audio by default" detail="Still requires confirmation for every session" checked={data.preferences.realtime_game_audio_default} disabled={busy} onChange={(value) => void updatePreferences({ realtime_game_audio_default: value })} />
      <SettingToggle label="Online assistance" detail="Allows governed online assistance candidates; it does not grant tool execution" checked={data.preferences.realtime_online_assistance_enabled} disabled={busy} onChange={(value) => void updatePreferences({ realtime_online_assistance_enabled: value })} />
      <SettingToggle label="Offer companion memory" detail="Only saves a bounded summary you confirm; never saves audio, frames or full transcripts" checked={data.preferences.realtime_memory_enabled} disabled={busy} onChange={(value) => void updatePreferences({ realtime_memory_enabled: value })} />
      <SettingRange label="Presence maximum" value={data.preferences.realtime_presence_max_minutes} min={30} max={240} step={30} suffix=" min" disabled={busy} onCommit={(value) => void updatePreferences({ realtime_presence_max_minutes: value })} />
      <SettingSelect icon={<Gauge size={17} />} label="Cloud daily limit" value={String(data.preferences.realtime_cloud_daily_limit_minutes)} disabled={busy} onChange={(value) => void updatePreferences({ realtime_cloud_daily_limit_minutes: Number(value) })} options={[{ value: "30", label: "30 minutes" }, { value: "60", label: "60 minutes" }, { value: "120", label: "120 minutes" }, { value: "180", label: "180 minutes" }]} />
      <SettingRange label="Local keep-warm" value={data.preferences.realtime_local_keep_warm_minutes} min={0} max={30} step={5} suffix=" min" disabled={busy} onCommit={(value) => void updatePreferences({ realtime_local_keep_warm_minutes: value })} />
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
      <SettingSelect icon={<Sparkles size={17} />} label="Material response" value={data.preferences.pet_activation_style} disabled={busy || !data.preferences.pet_motion_enabled} onChange={(value) => void updatePreferences({ pet_activation_style: value as DesktopPreferences["pet_activation_style"] })} options={[{ value: "fluid_response", label: "Fluid response" }, { value: "classic", label: "Classic" }]} />
      <SettingSelect icon={<Gauge size={17} />} label="Animation frame rate" value={String(data.preferences.pet_target_fps)} disabled={busy || !data.preferences.pet_motion_enabled} onChange={(value) => void updatePreferences({ pet_target_fps: Number(value) as DesktopPreferences["pet_target_fps"] })} options={[{ value: "60", label: "60 FPS" }, { value: "144", label: "144 FPS" }, { value: "300", label: "300 FPS" }]} />
      <SettingRange label="Size" value={data.preferences.pet_size_percent} min={75} max={150} step={5} suffix="%" disabled={busy} onCommit={(value) => void updatePreferences({ pet_size_percent: value })} />
      <SettingRange label="Opacity" value={data.preferences.pet_opacity_percent} min={40} max={100} step={2} suffix="%" disabled={busy} onCommit={(value) => void updatePreferences({ pet_opacity_percent: value })} />
      <SettingToggle label="Animate liquid motion" checked={data.preferences.pet_motion_enabled} disabled={busy} onChange={(value) => void updatePreferences({ pet_motion_enabled: value })} />
      <SettingToggle label="Show particles" checked={data.preferences.pet_particles_enabled} disabled={busy} onChange={(value) => void updatePreferences({ pet_particles_enabled: value })} />
      <SettingToggle label="Expand on hover" checked={data.preferences.pet_hover_enabled} disabled={busy} onChange={(value) => void updatePreferences({ pet_hover_enabled: value })} />
      <SettingRange label="Hover dwell" value={data.preferences.pet_hover_dwell_ms} min={100} max={1000} step={50} suffix=" ms" disabled={busy || !data.preferences.pet_hover_enabled} onCommit={(value) => void updatePreferences({ pet_hover_dwell_ms: value })} />
      <SettingToggle label="Do not disturb" checked={data.preferences.pet_do_not_disturb} disabled={busy} onChange={(value) => void updatePreferences({ pet_do_not_disturb: value })} />
      <SettingToggle label="Ambient dialogue" detail="Use the reviewed Fairy dialogue catalog while the device is idle" checked={data.preferences.ambient_dialogue_enabled} disabled={busy} onChange={(value) => void updatePreferences({ ambient_dialogue_enabled: value })} />
      <SettingToggle label="Speak ambient dialogue" detail="Separate from automatic playback of real replies" checked={data.preferences.ambient_dialogue_voice_enabled} disabled={busy || !data.preferences.ambient_dialogue_enabled || data.preferences.pet_muted} onChange={(value) => void updatePreferences({ ambient_dialogue_voice_enabled: value })} />
      <SettingToggle label="Remember position" checked={data.preferences.pet_remember_position} disabled={busy} onChange={(value) => void updatePreferences({ pet_remember_position: value })} />
    </Category>;
    case "advanced": return <Category title="Advanced" subtitle="Diagnostics and developer tools">
      <SettingToggle label="Generated ambient dialogue" detail="Allows at most two bounded low-cost generations per day; reviewed dialogue remains the fallback" checked={data.preferences.ambient_generated_dialogue_enabled} disabled={busy || !data.preferences.ambient_dialogue_enabled} onChange={(value) => void updatePreferences({ ambient_generated_dialogue_enabled: value })} />
      <SettingToggle label="Developer mode" checked={data.preferences.developer_mode} disabled={busy} onChange={(value) => void updatePreferences({ developer_mode: value })} />
      <HealthRow icon={<ShieldCheck size={17} />} label="Settings capability" status="Restricted settings methods only" tone="success" />
    </Category>;
  }
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
    <RealtimeCredentialForm provider="gemini" label="Gemini Live" status={data.realtimeCredentials.gemini} busy={busy} client={client} act={act} reload={reload} />
    <RealtimeCredentialForm provider="zhipu" label="Zhipu GLM Realtime" status={data.realtimeCredentials.zhipu} busy={busy} client={client} act={act} reload={reload} />
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

function RealtimeCredentialForm({ provider, label, status, busy, client, act, reload }: {
  provider: "gemini" | "zhipu";
  label: string;
  status: RealtimeCredentialView;
  busy: boolean;
  client: SettingsClient;
  act(operation: () => Promise<void>): Promise<void>;
  reload(): Promise<void>;
}) {
  const [apiKey, setApiKey] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const summary = status.error
    ? "Credential status unavailable"
    : status.configured
      ? status.hint
        ? `Credential protected by Windows · ••••${status.hint}`
        : "Credential protected by Windows"
      : "Credential not configured";
  return <>
    <form className="settings-form" onSubmit={(event) => { event.preventDefault(); void act(async () => { await client.providers.configureRealtime({ provider, api_key: apiKey }); setApiKey(""); await reload(); }); }}>
      <div className="settings-form-heading"><KeyRound size={17} /><div><strong>{label}</strong><span className={status.error ? "settings-form-warning" : undefined}>{summary}</span></div></div>
      <label><span>API key</span><input type="password" autoComplete="off" required value={apiKey} disabled={busy} placeholder={status.configured ? "Enter a new key to replace" : "API key"} onChange={(event) => setApiKey(event.target.value)} /></label>
      <div className="settings-form-actions"><button className="primary-command" type="submit" disabled={busy || !apiKey.trim()}><KeyRound size={14} />Save credential</button>{status.configured || status.error ? <button className="danger-icon" type="button" aria-label={`Remove ${label} credential`} disabled={busy} onClick={() => setConfirmDelete(true)}><Trash2 size={15} /></button> : null}</div>
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

function permissionKey(revision: number, profile: string, overrides: Record<string, boolean>) {
  return `settings:permissions:${revision}:${profile}:${Object.entries(overrides).sort(([a], [b]) => a.localeCompare(b)).map(([key, value]) => `${key}=${value}`).join(",")}`;
}

function emptyMemorySettings(): MemorySettings {
  return {
    enabled: true,
    retention_days: 365,
    export_to_obsidian: false,
    sync_normalized_content: false,
    revision: 0,
    updated_at: "1970-01-01T00:00:00Z",
  };
}

function emptyModelCatalog(): ModelCatalogPage {
  return {
    account: {
      account_id: "openrouter-default",
      provider_kind: "openrouter",
      display_name: "OpenRouter",
      credential_status: "unavailable",
    },
    items: [],
    fetched_at: "1970-01-01T00:00:00Z",
    expires_at: "1970-01-01T00:00:00Z",
    stale: true,
    revision: 0,
    last_error_code: null,
  };
}

function emptyModelSelection(): ModelSelectionPreference {
  return {
    mode: "auto",
    model_id: null,
    allow_free_fallback: false,
    zero_data_retention: false,
    revision: 0,
    updated_at: "1970-01-01T00:00:00Z",
  };
}

function emptyExecutionSettings(): ExecutionSettings {
  return {
    profile: "standard",
    capability_overrides: {},
    revision: 0,
    updated_at: "1970-01-01T00:00:00Z",
  };
}

function emptyCapabilityManifest(): CapabilityManifest {
  return {
    profile: "standard",
    operations: {},
    sandbox_healthy: false,
    command_metadata: [],
    slash_commands: [],
    schema_version: 3,
  };
}

function unavailableObsidianHealth(): KnowledgePrivacyData["obsidianHealth"] {
  return {
    desktop_installed: false,
    cli_available: false,
    minimum_installer_version: "1.12.7",
    status: "unavailable",
    public_summary: "Obsidian connector is unavailable",
  };
}

function emptyKnowledgeSettingsData(): KnowledgeSettingsData {
  return {
    memorySettings: emptyMemorySettings(),
    knowledgeSources: [],
    memoryProposals: [],
    obsidianHealth: unavailableObsidianHealth(),
    knowledgeDiagnostics: [],
  };
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
