import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

import { PresenceBridge } from "../presence/PresenceBridge";
import { projectPetReply } from "../presence/domain/reply";
import { VoiceController, useVoicePresence } from "../voice/VoiceController";
import { DesktopPreferencesBridge } from "../settings/DesktopPreferencesBridge";
import type { CoreClient } from "../core/client";
import {
  RealtimeCompanion,
  type RealtimePresenceState,
} from "../realtime/RealtimeCompanion";

import { WorkspaceShell } from "./WorkspaceShell";
import { type WorkspaceClient, useWorkspaceModel } from "./workspaceModel";

interface AppProps {
  client: WorkspaceClient & { realtime?: CoreClient["realtime"] };
}

function Workspace({ client }: AppProps) {
  const model = useWorkspaceModel(client);
  const [realtimePresence, setRealtimePresence] = useState<RealtimePresenceState>("idle");
  const [realtimeOpenRequest, setRealtimeOpenRequest] = useState(0);
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
      <DesktopPreferencesBridge trash={client.trash} />
      {client.realtime ? (
        <RealtimeCompanion
          client={client.realtime}
          onPresenceChange={setRealtimePresence}
          openRequest={realtimeOpenRequest}
        />
      ) : null}
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
          realtimePresence={realtimePresence}
          onOpenRealtime={() => setRealtimeOpenRequest((value) => value + 1)}
        />
        <WorkspaceShell model={model} />
      </VoiceController>
    </>
  );
}

function WorkspacePresence({
  model,
  realtimePresence,
  onOpenRealtime,
}: {
  model: ReturnType<typeof useWorkspaceModel>;
  realtimePresence: RealtimePresenceState;
  onOpenRealtime(): void;
}) {
  const voice = useVoicePresence();
  return (
    <PresenceBridge
      events={model.presenceEvents}
      onCancel={model.cancelPetTurn}
      onNewChat={model.createPetChatConversation}
      onSend={model.sendPetMessage}
      onStopVoice={voice.stopSpeaking}
      reply={projectPetReply({
        petTaskId: model.petTaskId,
        turn: model.chatTurn,
        streamedText: model.chatStreamedText,
        messages: model.messages,
      })}
      speaking={voice.speaking}
      realtimePresence={realtimePresence}
      onOpenRealtime={onOpenRealtime}
    />
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
