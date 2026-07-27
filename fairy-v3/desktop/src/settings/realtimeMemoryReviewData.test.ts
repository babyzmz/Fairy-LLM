import { describe, expect, it, vi } from "vitest";

import type {
  CompanionSessionDigest,
  RealtimeMemoryProposal,
} from "../core/client";
import type { SettingsClient } from "./client";
import { loadRealtimeMemoryReview } from "./realtimeMemoryReviewData";

describe("loadRealtimeMemoryReview", () => {
  it("keeps proposal counts scoped to the bounded digest page", async () => {
    const visible = digest("01900000-0000-7000-8000-000000000001");
    const hidden = digest("01900000-0000-7000-8000-000000000002");
    const client = {
      realtimeMemory: {
        digests: {
          list: vi.fn(async () => ({ items: [visible] })),
        },
        proposals: {
          list: vi.fn(async () => ({
            items: [
              proposal("01900000-0000-7000-8000-000000000003", visible.id),
              proposal("01900000-0000-7000-8000-000000000004", hidden.id),
            ],
          })),
        },
      },
    } as unknown as SettingsClient;

    const result = await loadRealtimeMemoryReview(client);

    expect(result.digests.map((item) => item.id)).toEqual([visible.id]);
    expect(result.proposals.map((item) => item.digest_id)).toEqual([visible.id]);
  });
});

function digest(id: string): CompanionSessionDigest {
  return {
    id,
    session_id: "01900000-0000-7000-8000-000000000010",
    conversation_id: "01900000-0000-7000-8000-000000000011",
    request_id: `test:${id}`,
    activity: "focus",
    subject_title: null,
    started_at: "2026-07-28T00:00:00Z",
    ended_at: "2026-07-28T00:01:00Z",
    duration_seconds: 60,
    activities: [],
    progress_summary: "Completed the session.",
    unresolved_issue: null,
    next_goal: null,
    notable_outcome: null,
    source_first_sequence: 1,
    source_last_sequence: 1,
    source_digest: "a".repeat(64),
    policy_version: "realtime-digest-v1",
    proposal_ids: [],
    created_at: "2026-07-28T00:01:01Z",
    revision: 1,
  };
}

function proposal(id: string, digestId: string): RealtimeMemoryProposal {
  return {
    id,
    digest_id: digestId,
    session_id: "01900000-0000-7000-8000-000000000010",
    conversation_id: "01900000-0000-7000-8000-000000000011",
    kind: "inferred_fact",
    subject: "user",
    predicate: "preference",
    value: "test",
    normalized_text: "Test preference",
    target_namespace: "user_profile",
    confidence: 0.5,
    sensitivity: "private",
    source_first_sequence: 1,
    source_last_sequence: 1,
    evidence_digest: "b".repeat(64),
    policy_decision: "requires_confirmation",
    policy_reason: "inferred_or_sensitive_requires_confirmation",
    status: "pending",
    claim_id: null,
    created_at: "2026-07-28T00:01:01Z",
    updated_at: "2026-07-28T00:01:01Z",
    revision: 1,
  };
}
