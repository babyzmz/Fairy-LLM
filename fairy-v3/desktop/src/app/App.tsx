import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

import { PresenceBridge } from "../presence/PresenceBridge";
import { VoiceController } from "../voice/VoiceController";
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
      <PresenceBridge events={model.presenceEvents} />
      <VoiceController
        client={client}
        conversationId={conversationId}
        profile={profile}
        health={health}
        turn={model.mode === "chat" ? model.chatTurn : null}
        events={model.mode === "chat" ? model.chatEvents : []}
      >
        <WorkspaceShell model={model} />
      </VoiceController>
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
