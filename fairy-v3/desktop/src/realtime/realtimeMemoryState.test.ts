import { describe, expect, it, vi } from "vitest";

import type {
  CompanionSessionDigest,
  CoreClient,
  RealtimeMemoryProposal,
} from "../core/client";
import {
  createCompanionMemoryNotice,
  loadCompanionMemoryNotice,
} from "./realtimeMemoryState";

const SESSION_ID = "01900000-0000-7000-8000-000000000001";
const DIGEST_ID = "01900000-0000-7000-8000-000000000002";

describe("Companion Realtime memory projection", () => {
  it("returns only bounded counts for the requested Session", async () => {
    const listDigests = vi.fn(async () => ({ items: [digest()] }));
    const listProposals = vi.fn(async () => ({
      items: [
        proposal("promoted", SESSION_ID),
        proposal("pending", SESSION_ID, "01900000-0000-7000-8000-000000000004"),
        proposal("promoted", "01900000-0000-7000-8000-000000000099"),
      ],
    }));
    const client = clientWith(listDigests, listProposals);

    const result = await loadCompanionMemoryNotice(client, SESSION_ID);

    expect(result).toEqual({
      sessionId: SESSION_ID,
      digestId: DIGEST_ID,
      savedCount: 1,
      pendingCount: 1,
    });
    expect(JSON.stringify(result)).not.toContain("private source text");
    expect(listProposals).toHaveBeenCalledWith({
      session_id: SESSION_ID,
      digest_id: DIGEST_ID,
      pending_only: false,
      limit: 100,
    });
  });

  it("creates one idempotent digest request and rejects a mismatched Session", async () => {
    const create = vi.fn(async () => digest());
    const client = {
      ...clientWith(vi.fn(), vi.fn(async () => ({ items: [] }))),
      digests: {
        create,
        list: vi.fn(),
        get: vi.fn(),
      },
    } as unknown as CoreClient["realtime"];

    await createCompanionMemoryNotice(client, {
      sessionId: SESSION_ID,
      activity: "focus",
      subjectTitle: "Editor",
    });
    expect(create).toHaveBeenCalledWith({
      session_id: SESSION_ID,
      request_id: `desktop:realtime-digest:${SESSION_ID}`,
      activity: "focus",
      subject_title: "Editor",
    });

    create.mockResolvedValue({ ...digest(), session_id: "01900000-0000-7000-8000-000000000099" });
    await expect(createCompanionMemoryNotice(client, {
      sessionId: SESSION_ID,
      activity: "focus",
      subjectTitle: null,
    })).rejects.toThrow("session mismatch");
  });
});

function clientWith(
  listDigests: ReturnType<typeof vi.fn>,
  listProposals: ReturnType<typeof vi.fn>,
): CoreClient["realtime"] {
  return {
    digests: {
      create: vi.fn(),
      get: vi.fn(),
      list: listDigests,
    },
    memoryProposals: {
      list: listProposals,
      accept: vi.fn(),
      reject: vi.fn(),
    },
  } as unknown as CoreClient["realtime"];
}

function digest(): CompanionSessionDigest {
  return {
    id: DIGEST_ID,
    session_id: SESSION_ID,
    conversation_id: "01900000-0000-7000-8000-000000000010",
    request_id: `desktop:realtime-digest:${SESSION_ID}`,
    activity: "focus",
    subject_title: "Editor",
    started_at: "2026-07-28T00:00:00Z",
    ended_at: "2026-07-28T00:10:00Z",
    duration_seconds: 600,
    activities: ["reviewed code"],
    progress_summary: "Reviewed code.",
    unresolved_issue: null,
    next_goal: null,
    notable_outcome: null,
    source_first_sequence: 1,
    source_last_sequence: 2,
    source_digest: "a".repeat(64),
    policy_version: "realtime-digest-v1",
    proposal_ids: [],
    created_at: "2026-07-28T00:10:01Z",
    revision: 1,
  };
}
function proposal(
  status: RealtimeMemoryProposal["status"],
  sessionId: string,
  id = "01900000-0000-7000-8000-000000000003",
): RealtimeMemoryProposal {
  return {
    id,
    digest_id: DIGEST_ID,
    session_id: sessionId,
    conversation_id: "01900000-0000-7000-8000-000000000010",
    kind: "inferred_fact",
    subject: "user",
    predicate: "preference",
    value: "private source text",
    normalized_text: "private source text",
    target_namespace: "user_profile",
    confidence: 0.6,
    sensitivity: "private",
    source_first_sequence: 1,
    source_last_sequence: 1,
    evidence_digest: "b".repeat(64),
    policy_decision: "requires_confirmation",
    policy_reason: "inferred_or_sensitive_requires_confirmation",
    status,
    claim_id: status === "promoted" ? "01900000-0000-7000-8000-000000000020" : null,
    created_at: "2026-07-28T00:10:01Z",
    updated_at: "2026-07-28T00:10:01Z",
    revision: 1,
  };
}
