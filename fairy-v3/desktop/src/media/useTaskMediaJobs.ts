import { useQuery } from "@tanstack/react-query";

import type { CoreClient } from "../core/client";

interface MediaJobClient {
  media: {
    jobs: Pick<CoreClient["media"]["jobs"], "list">;
  };
}

export function useTaskMediaJobs(
  client: MediaJobClient,
  scope: { conversationId: string; taskId: string } | null,
) {
  return useQuery({
    queryKey: ["workspace", "media-jobs", scope?.conversationId, scope?.taskId],
    queryFn: () => client.media.jobs.list(requireScope(scope).taskId),
    enabled: scope !== null,
    retry: false,
  });
}

function requireScope(scope: { conversationId: string; taskId: string } | null) {
  if (scope === null) throw new Error("Media job scope is unavailable");
  return scope;
}
