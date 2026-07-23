import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { PresenceBridge } from "../presence/PresenceBridge";
import { projectPetReply } from "../presence/domain/reply";
import { VoiceController, useVoicePresence } from "../voice/VoiceController";
import { DesktopPreferencesBridge } from "../settings/DesktopPreferencesBridge";
import type { DesktopPreferences } from "../settings/client";
import { AmbientDialogueHost } from "../persona/AmbientDialogueHost";
import type { AmbientDialogueProjection } from "../core/contracts";
import type { CoreClient } from "../core/client";
import {
  subscribeRealtimePresence,
  type RealtimePresenceState,
} from "../realtime/realtimePresence";

import { WorkspaceShell } from "./WorkspaceShell";
import { type WorkspaceClient, useWorkspaceModel } from "./workspaceModel";

interface AppProps {
  client: WorkspaceClient & {
    ambient?: CoreClient["ambient"];
  };
}

function Workspace({ client }: AppProps) {
  const model = useWorkspaceModel(client);
  const [realtimePresence, setRealtimePresence] = useState<RealtimePresenceState>("idle");
  const [preferences, setPreferences] = useState<DesktopPreferences | null>(null);
  useEffect(
    () => subscribeRealtimePresence(setRealtimePresence),
    [],
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
      <DesktopPreferencesBridge trash={client.trash} onChange={setPreferences} />
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
          model={model}
          ambientClient={client.ambient}
          preferences={preferences}
          realtimePresence={realtimePresence}
        />
        <WorkspaceShell model={model} />
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
  const realtimeActive = !["idle", "completed", "error"].includes(realtimePresence);
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

export function App({ client }: AppProps) {
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

  return (
    <QueryClientProvider client={queryClient}>
      <Workspace client={client} />
    </QueryClientProvider>
  );
}
