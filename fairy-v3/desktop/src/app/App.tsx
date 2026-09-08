import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { PresenceBridge } from "../presence/PresenceBridge";
import { projectPetReply } from "../presence/domain/reply";
import { VoiceController, useVoicePresence } from "../voice/VoiceController";
import { DesktopPreferencesBridge } from "../settings/DesktopPreferencesBridge";
import type { DesktopPreferences } from "../settings/client";
import type { SettingsClient } from "../settings/client";
import { AmbientDialogueHost } from "../persona/AmbientDialogueHost";
import type { AmbientDialogueProjection } from "../core/contracts";
import type { CoreClient, SettingsCategoryId } from "../core/client";
import {
  subscribeRealtimePresence,
  type RealtimePresenceState,
} from "../realtime/realtimePresence";

import type {
  MainView,
  MainViewHost,
  MainViewRequest,
} from "./mainViewBridge";
import { WorkspaceShell } from "./WorkspaceShell";
import {
  type BackgroundTaskNotificationHost,
  useBackgroundTaskNotifications,
} from "./backgroundNotifications";
import { type WorkspaceClient, useWorkspaceModel } from "./workspaceModel";

interface AppProps {
  client: WorkspaceClient & {
    ambient?: CoreClient["ambient"];
  };
  settingsClient?: SettingsClient;
  mainViewHost?: MainViewHost;
  backgroundNotificationHost?: BackgroundTaskNotificationHost;
}

const LazySettingsApp = lazy(async () => {
  const module = await import("../settings/SettingsApp");
  return { default: module.SettingsApp };
});

function Workspace({
  client,
  preferences,
  onOpenSettings,
  navigationRequest,
  backgroundNotificationHost,
  workspaceVisible,
}: {
  client: AppProps["client"];
  preferences: DesktopPreferences | null;
  onOpenSettings(category?: SettingsCategoryId): Promise<void>;
  navigationRequest: {
    sequence: number;
    conversationId: string | null;
    turnId: string | null;
    mode: "chat" | "project" | null;
  };
  backgroundNotificationHost?: BackgroundTaskNotificationHost;
  workspaceVisible: boolean;
}) {
  const model = useWorkspaceModel(client);
  const lastNavigationSequence = useRef(-1);
  useEffect(() => {
    if (
      (navigationRequest.conversationId === null && navigationRequest.mode === null)
      || navigationRequest.sequence <= lastNavigationSequence.current
    ) return;
    lastNavigationSequence.current = navigationRequest.sequence;
    if (navigationRequest.conversationId !== null) {
      model.openBackgroundTaskLocation(navigationRequest.conversationId, navigationRequest.turnId);
    } else if (navigationRequest.mode !== null) model.setMode(navigationRequest.mode);
  }, [model, navigationRequest]);
  useBackgroundTaskNotifications({
    page: model.backgroundTasks,
    ready: model.backgroundTasksReady,
    host: backgroundNotificationHost,
    workspaceVisible,
    selectedConversationId: model.mode === "chat"
      ? model.selectedChatConversation?.id ?? null
      : model.selectedConversation?.id ?? null,
  });
  const [realtimePresence, setRealtimePresence] = useState<RealtimePresenceState>("idle");
  useEffect(
    () => subscribeRealtimePresence(
      (projection) => setRealtimePresence(projection?.state ?? "idle"),
    ),
    [],
  );
  const shellModel = useMemo(
    () => ({
      ...model,
      openSettings: onOpenSettings,
    }),
    [model, onOpenSettings],
  );
  const profile =
    model.providers.find((provider) => provider.id === model.selectedProfileId) ?? null;
  const health =
    model.providerHealth.find(
      (item) => item.profile_id === model.selectedProfileId,
    ) ?? null;
  const conversationId =
    model.mode === "chat"
      ? model.selectedChatConversation?.id ?? null
      : model.selectedConversation?.id ?? null;
  return (
    <>
      <VoiceController
        client={client}
        conversationId={conversationId}
        profile={profile}
        health={health}
        turn={model.mode === "chat" ? model.chatTurn : null}
        events={model.mode === "chat" ? model.chatEvents : []}
        petTaskId={model.petTaskId}
      >
        <WorkspacePresence
          model={shellModel}
          ambientClient={client.ambient}
          preferences={preferences}
          realtimePresence={realtimePresence}
        />
        <WorkspaceShell model={shellModel} preferences={preferences} fairyEyeActive={workspaceVisible} visible={workspaceVisible} />
      </VoiceController>
    </>
  );
}

function WorkspacePresence({
  model,
  ambientClient,
  preferences,
  realtimePresence,
}: {
  model: ReturnType<typeof useWorkspaceModel>;
  ambientClient: CoreClient["ambient"] | undefined;
  preferences: DesktopPreferences | null;
  realtimePresence: RealtimePresenceState;
}) {
  const voice = useVoicePresence();
  const voiceRef = useRef(voice);
  voiceRef.current = voice;
  const [ambientDialogue, setAmbientDialogue] = useState<AmbientDialogueProjection | null>(null);
  const visibleAmbientDialogueRef = useRef<AmbientDialogueProjection | null>(null);
  const [petInputOpen, setPetInputOpen] = useState(false);
  const realtimeActive = !["idle", "error"].includes(realtimePresence);
  const updateAmbientDialogue = useCallback((projection: AmbientDialogueProjection | null) => {
    setAmbientDialogue(projection);
  }, []);
  const updateVisibleAmbientDialogue = useCallback(
    (projection: AmbientDialogueProjection | null) => {
      const previous = visibleAmbientDialogueRef.current;
      if (previous?.presentation_id === projection?.presentation_id) return;
      visibleAmbientDialogueRef.current = projection;
      if (previous !== null) voiceRef.current.stopAmbient();
      if (projection?.tts_allowed === true) {
        void voiceRef.current.speakAmbient(projection.text, projection.presentation_id);
      }
    },
    [],
  );
  return (
    <>
      <AmbientDialogueHost
        activeTurn={model.chatBusy || model.projectBusy}
        approvalWaiting={model.approvals.some((approval) => approval.decision === "pending")}
        client={ambientClient}
        inputOpen={petInputOpen}
        microphoneActive={voice.recording || realtimeActive}
        onProjection={updateAmbientDialogue}
        preferences={preferences}
        realtimeActive={realtimeActive}
        severeError={Boolean(
          model.actionError || model.errorMessage || model.chatError || model.projectError
        )}
        ttsActive={voice.speaking && !voice.speakingAmbient}
      />
      <PresenceBridge
        ambientDialogue={ambientDialogue}
        events={model.presenceEvents}
        onCancel={model.cancelPetTurn}
        onInputState={(state) => setPetInputOpen(state.open)}
        onNewChat={model.createPetChatConversation}
        onSend={model.sendPetMessage}
        onAmbientVisibilityChange={updateVisibleAmbientDialogue}
        onStopVoice={voice.stopSpeaking}
        reply={projectPetReply({
          petTaskId: model.petTaskId,
          turn: model.chatTurn,
          streamedText: model.chatStreamedText,
          messages: model.messages,
        })}
        speaking={voice.speaking}
        realtimePresence={realtimePresence}
      />
    </>
  );
}

export function App({
  client,
  settingsClient,
  mainViewHost,
  backgroundNotificationHost,
}: AppProps) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 5_000,
            retry: false,
            refetchOnWindowFocus: false,
          },
        },
      }),
  );
  const [mainView, setMainView] = useState<MainView>("workspace");
  const [settingsMounted, setSettingsMounted] = useState(false);
  const [settingsRequest, setSettingsRequest] = useState<{
    sequence: number;
    category?: SettingsCategoryId;
  }>({ sequence: 0 });
  const [preferences, setPreferences] = useState<DesktopPreferences | null>(null);
  const [workspaceNavigation, setWorkspaceNavigation] = useState({
    sequence: 0,
    conversationId: null as string | null,
    turnId: null as string | null,
    mode: null as "chat" | "project" | null,
  });
  const focusReturnRef = useRef<HTMLElement | null>(null);
  const mainViewRef = useRef<MainView>("workspace");
  const lastNativeSequenceRef = useRef(-1);

  const showSettings = useCallback((
    category?: SettingsCategoryId,
    sequence?: number,
  ) => {
    if (mainViewRef.current !== "settings") {
      const activeElement = document.activeElement;
      focusReturnRef.current =
        activeElement instanceof HTMLElement ? activeElement : null;
    }
    mainViewRef.current = "settings";
    setSettingsMounted(true);
    setSettingsRequest((current) => ({
      sequence: sequence ?? current.sequence + 1,
      category,
    }));
    setMainView("settings");
  }, []);

  const showWorkspace = useCallback(() => {
    mainViewRef.current = "workspace";
    setMainView("workspace");
    requestAnimationFrame(() => focusReturnRef.current?.focus());
  }, []);

  const applyMainViewRequest = useCallback((request: MainViewRequest) => {
    if (
      request.schema_version !== 1 ||
      request.sequence <= lastNativeSequenceRef.current
    ) {
      return;
    }
    lastNativeSequenceRef.current = request.sequence;
    if (request.view === "settings") {
      showSettings(request.settings_category ?? undefined, request.sequence);
    } else {
      showWorkspace();
      if (request.conversation_id !== null || request.workspace_mode != null) {
        setWorkspaceNavigation({
          sequence: request.sequence,
          conversationId: request.conversation_id,
          turnId: request.turn_id ?? null,
          mode: request.workspace_mode ?? null,
        });
      }
    }
  }, [showSettings, showWorkspace]);

  const openSettings = useCallback(async (category?: SettingsCategoryId) => {
    if (settingsClient === undefined) {
      await client.desktop.openSettings(category);
      return;
    }
    showSettings(category);
    if (mainViewHost !== undefined) {
      applyMainViewRequest(await mainViewHost.navigate("settings", category));
    }
  }, [
    applyMainViewRequest,
    client.desktop,
    mainViewHost,
    settingsClient,
    showSettings,
  ]);

  const closeSettings = useCallback(() => {
    showWorkspace();
    if (mainViewHost !== undefined) {
      void mainViewHost
        .navigate("workspace")
        .then(applyMainViewRequest)
        .catch(() => undefined);
    }
  }, [applyMainViewRequest, mainViewHost, showWorkspace]);

  useEffect(() => {
    if (mainViewHost === undefined) return;
    let active = true;
    let unsubscribe: (() => void) | undefined;
    void mainViewHost
      .subscribe((request) => {
        if (active) applyMainViewRequest(request);
      })
      .then((stop) => {
        if (active) {
          unsubscribe = stop;
        } else {
          stop();
        }
      })
      .then(() => mainViewHost.get())
      .then((request) => {
        if (active) applyMainViewRequest(request);
      })
      .catch(() => undefined);
    return () => {
      active = false;
      unsubscribe?.();
    };
  }, [applyMainViewRequest, mainViewHost]);

  useEffect(() => {
    if (mainView !== "settings") return;
    const frame = requestAnimationFrame(() => {
      const heading = document.querySelector<HTMLElement>(
        '[data-testid="settings-view"] h1',
      );
      if (heading === null) return;
      heading.tabIndex = -1;
      heading.focus();
    });
    return () => cancelAnimationFrame(frame);
  }, [mainView, settingsRequest.sequence]);

  useEffect(() => {
    if (mainView !== "settings") return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      const active = document.activeElement;
      if (
        active instanceof HTMLInputElement ||
        active instanceof HTMLTextAreaElement ||
        active instanceof HTMLSelectElement ||
        active?.getAttribute("contenteditable") === "true" ||
        document.querySelector('[role="dialog"], [role="alertdialog"], [role="menu"]') !== null
      ) {
        return;
      }
      closeSettings();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [closeSettings, mainView]);

  useEffect(() => {
    const preload = () => {
      if (settingsClient !== undefined) void import("../settings/SettingsApp");
    };
    const requestIdle = window.requestIdleCallback?.bind(window);
    if (typeof requestIdle === "function") {
      const id = requestIdle(preload, { timeout: 2_000 });
      return () => window.cancelIdleCallback(id);
    }
    const id = globalThis.setTimeout(preload, 750);
    return () => globalThis.clearTimeout(id);
  }, [settingsClient]);

  return (
    <QueryClientProvider client={queryClient}>
      <DesktopPreferencesBridge
        trash={client.trash}
        onChange={setPreferences}
      />
      <div
        className="main-view-surface"
        data-testid="workspace-view"
        hidden={mainView !== "workspace"}
        inert={mainView !== "workspace"}
      >
        <Workspace
          client={client}
          preferences={preferences}
          onOpenSettings={openSettings}
          navigationRequest={workspaceNavigation}
          backgroundNotificationHost={backgroundNotificationHost}
          workspaceVisible={mainView === "workspace"}
        />
      </div>
      {settingsMounted && settingsClient !== undefined ? (
        <div
          className="main-view-surface"
          data-testid="settings-view"
          hidden={mainView !== "settings"}
          inert={mainView !== "settings"}
        >
          <Suspense fallback={<div className="settings-route-loading" role="status">Loading settings</div>}>
            <LazySettingsApp
              client={settingsClient}
              initialPreferences={preferences}
              navigationRequest={settingsRequest}
              onBack={closeSettings}
              queryClient={queryClient}
            />
          </Suspense>
        </div>
      ) : null}
    </QueryClientProvider>
  );
}
