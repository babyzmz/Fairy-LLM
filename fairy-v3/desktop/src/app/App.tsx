import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { WorkspaceShell } from "./WorkspaceShell";

export interface CoreHealthClient {
  health(): Promise<{ status: string }>;
}

interface AppProps {
  client: CoreHealthClient;
}

function Workspace({ client }: AppProps) {
  const health = useQuery({
    queryKey: ["core", "health"],
    queryFn: () => client.health(),
    retry: false,
    refetchOnWindowFocus: false,
  });
  const coreStatus = health.isPending
    ? "Core starting"
    : health.isError
      ? "Core offline"
      : "Core ready";

  return (
    <WorkspaceShell
      context={{
        project: "Fairy V3",
        conversation: "Homepage revision",
        version: "Current draft",
        executionTarget: "Local",
        permission: "Standard",
        syncStatus: coreStatus,
      }}
    />
  );
}

export function App({ client }: AppProps) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { staleTime: 5_000 },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <Workspace client={client} />
    </QueryClientProvider>
  );
}
