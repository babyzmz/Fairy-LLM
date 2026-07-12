import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

import { PresenceBridge } from "../presence/PresenceBridge";
import type { PresenceReply } from "../presence/projection";
import { VoiceController, useVoicePresence } from "../voice/VoiceController";
import { DesktopPreferencesBridge } from "../settings/DesktopPreferencesBridge";

import { WorkspaceShell } from "./WorkspaceShell";
import { type WorkspaceClient, useWorkspaceModel } from "./workspaceModel";

interface AppProps {
  client: WorkspaceClient;
}

function Workspace({ client }: AppProps) {
  const model = useWorkspaceModel(client);
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
      <DesktopPreferencesBridge />
      <VoiceController
        client={client}
        conversationId={conversationId}
        profile={profile}
        health={health}
        turn={model.mode === "chat" ? model.chatTurn : null}
        events={model.mode === "chat" ? model.chatEvents : []}
        petTaskId={model.petTaskId}
      >
        <WorkspacePresence model={model} />
        <WorkspaceShell model={model} />
      </VoiceController>
    </>
  );
}

function WorkspacePresence({ model }: { model: ReturnType<typeof useWorkspaceModel> }) {
  const voice = useVoicePresence();
  return (
    <PresenceBridge
      events={model.presenceEvents}
      onNewChat={model.createPetChatConversation}
      onSend={model.sendPetMessage}
      onStopVoice={voice.stopSpeaking}
      reply={petReply(model)}
      speaking={voice.speaking}
    />
  );
}

function petReply(model: ReturnType<typeof useWorkspaceModel>): PresenceReply | null {
  const taskId = model.petTaskId;
  if (taskId === null) return null;
  if (model.chatTurn?.task_id === taskId && model.chatStreamedText !== "") {
    return {
      id: `pet-stream:${model.chatTurn.id}`,
      text: boundedReply(model.chatStreamedText),
      kind: "scratch",
      streaming: model.chatTurn.status !== "completed",
    };
  }
  const message = [...model.messages]
    .filter(
      (item) =>
        item.task_id === taskId &&
        item.role === "assistant" &&
        item.visibility === "user",
    )
    .sort((left, right) => left.sequence - right.sequence)
    .at(-1);
  return message === undefined
    ? null
    : {
        id: `pet-message:${message.id}`,
        text: boundedReply(message.content),
        kind: "scratch",
        streaming: false,
      };
}

function boundedReply(value: string): string {
  return Array.from(value.trim()).slice(-1_200).join("") || "Fairy is ready";
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
