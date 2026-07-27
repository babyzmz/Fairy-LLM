import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  CompanionSessionDigest,
  RealtimeMemoryProposal,
} from "../core/client";
import { RealtimeMemoryReview } from "./RealtimeMemoryReview";
import type { SettingsClient } from "./client";

describe("RealtimeMemoryReview", () => {
  afterEach(() => cleanup());

  it("shows saved, pending, and rejected facts and confirms a revision-fenced decision", async () => {
    const accept = vi.fn(async () => proposal("promoted"));
    const reload = vi.fn(async () => undefined);
    const client = {
      realtimeMemory: {
        proposals: {
          accept,
          reject: vi.fn(),
        },
      },
    } as unknown as SettingsClient;
    render(
      <RealtimeMemoryReview
        data={{
          digests: [digest()],
          proposals: [
            proposal("pending"),
            proposal("promoted", "01900000-0000-7000-8000-000000000004"),
            proposal("rejected", "01900000-0000-7000-8000-000000000005"),
          ],
          diagnostic: null,
        }}
        busy={false}
        client={client}
        act={async (operation) => operation()}
        reload={reload}
      />,
    );

    expect(screen.getByText(/Saved automatically from an explicit low-risk statement/)).not.toBeNull();
    expect(screen.getAllByText(/Rejected/).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "Accept" }));
    expect(screen.getByRole("dialog", { name: "Accept Realtime memory" })).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Accept memory" }));

    await waitFor(() => expect(accept).toHaveBeenCalledWith({
      proposal_id: "01900000-0000-7000-8000-000000000003",
      expected_revision: 7,
      user_confirmed: true,
      idempotency_key:
        "settings:realtime-memory:accept:01900000-0000-7000-8000-000000000003:7",
    }));
    expect(reload).toHaveBeenCalledOnce();
  });

  it("rejects a pending suggestion only after explicit confirmation", async () => {
    const reject = vi.fn(async () => proposal("rejected"));
    const reload = vi.fn(async () => undefined);
    const client = {
      realtimeMemory: {
        proposals: {
          accept: vi.fn(),
          reject,
        },
      },
    } as unknown as SettingsClient;
    render(
      <RealtimeMemoryReview
        data={{
          digests: [digest()],
          proposals: [proposal("pending")],
          diagnostic: null,
        }}
        busy={false}
        client={client}
        act={async (operation) => operation()}
        reload={reload}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(screen.getByRole("alertdialog", { name: "Reject Realtime memory" })).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Reject suggestion" }));

    await waitFor(() => expect(reject).toHaveBeenCalledWith({
      proposal_id: "01900000-0000-7000-8000-000000000003",
      expected_revision: 7,
      user_confirmed: true,
      idempotency_key:
        "settings:realtime-memory:reject:01900000-0000-7000-8000-000000000003:7",
    }));
    expect(reload).toHaveBeenCalledOnce();
  });
});

function digest(): CompanionSessionDigest {
  return {
    id: "01900000-0000-7000-8000-000000000002",
    session_id: "01900000-0000-7000-8000-000000000001",
    conversation_id: "01900000-0000-7000-8000-000000000010",
    request_id: "desktop:digest",
    activity: "focus",
    subject_title: "Editor",
    started_at: "2026-07-28T00:00:00Z",
    ended_at: "2026-07-28T00:10:00Z",
    duration_seconds: 600,
    activities: ["reviewed code"],
    progress_summary: "Reviewed the active change.",
    unresolved_issue: null,
    next_goal: "Run tests.",
    notable_outcome: null,
    source_first_sequence: 1,
    source_last_sequence: 3,
    source_digest: "a".repeat(64),
    policy_version: "realtime-digest-v1",
    proposal_ids: [],
    created_at: "2026-07-28T00:10:01Z",
    revision: 1,
  };
}

function proposal(
  status: RealtimeMemoryProposal["status"],
  id = "01900000-0000-7000-8000-000000000003",
): RealtimeMemoryProposal {
  return {
    id,
    digest_id: "01900000-0000-7000-8000-000000000002",
    session_id: "01900000-0000-7000-8000-000000000001",
    conversation_id: "01900000-0000-7000-8000-000000000010",
    kind: status === "promoted" ? "next_goal" : "inferred_fact",
    subject: "user",
    predicate: "next_goal",
    value: "Run tests.",
    normalized_text: status === "rejected" ? "Possible private inference" : "Run tests.",
    target_namespace: "device_local",
    confidence: 0.8,
    sensitivity: "private",
    source_first_sequence: 2,
    source_last_sequence: 2,
    evidence_digest: "b".repeat(64),
    policy_decision: status === "promoted" ? "auto_promote" : "requires_confirmation",
    policy_reason: status === "promoted"
      ? "explicit_low_risk_stable_caption"
      : "inferred_or_sensitive_requires_confirmation",
    status,
    claim_id: status === "promoted" ? "01900000-0000-7000-8000-000000000020" : null,
    created_at: "2026-07-28T00:10:01Z",
    updated_at: "2026-07-28T00:10:01Z",
    revision: 7,
  };
}
