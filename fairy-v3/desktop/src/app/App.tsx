import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

import { WorkspaceShell } from "./WorkspaceShell";
import { type WorkspaceClient, useWorkspaceModel } from "./workspaceModel";

interface AppProps {
  client: WorkspaceClient;
}

function Workspace({ client }: AppProps) {
  const model = useWorkspaceModel(client);
  return <WorkspaceShell model={model} />;
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
