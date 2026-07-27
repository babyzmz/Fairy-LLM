import type {
  CompanionSessionDigest,
  RealtimeMemoryProposal,
} from "../core/client";
import type { SettingsClient } from "./client";

export interface RealtimeMemoryReviewData {
  digests: CompanionSessionDigest[];
  proposals: RealtimeMemoryProposal[];
  diagnostic: string | null;
}

export async function loadRealtimeMemoryReview(
  client: SettingsClient,
): Promise<RealtimeMemoryReviewData> {
  const [digestsResult, proposalsResult] = await Promise.allSettled([
    client.realtimeMemory.digests.list({ limit: 30 }),
    client.realtimeMemory.proposals.list({ pending_only: false, limit: 100 }),
  ]);
  const diagnostics: string[] = [];
  if (digestsResult.status === "rejected") {
    diagnostics.push("Session summaries could not be loaded.");
  }
  if (proposalsResult.status === "rejected") {
    diagnostics.push("Realtime memory review could not be loaded.");
  }
  const digests = digestsResult.status === "fulfilled"
      ? [...digestsResult.value.items].sort((left, right) =>
          right.created_at.localeCompare(left.created_at))
      : [];
  const visibleDigestIds = new Set(digests.map((digest) => digest.id));
  return {
    digests,
    proposals: proposalsResult.status === "fulfilled"
      ? [...proposalsResult.value.items].sort((left, right) =>
          right.updated_at.localeCompare(left.updated_at)).filter((proposal) =>
            visibleDigestIds.has(proposal.digest_id))
      : [],
    diagnostic: diagnostics.length > 0 ? diagnostics.join(" ") : null,
  };
}
