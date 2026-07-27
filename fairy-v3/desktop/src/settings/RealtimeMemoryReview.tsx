import {
  Brain,
  CheckCircle2,
  Clock3,
  FileText,
  ShieldAlert,
  XCircle,
} from "lucide-react";
import { useMemo, useState } from "react";

import type {
  RealtimeMemoryProposal,
} from "../core/client";
import { ActionDialog } from "../ui/ActionDialog";
import type { SettingsClient } from "./client";
import type { RealtimeMemoryReviewData } from "./realtimeMemoryReviewData";

type ReviewDecision = {
  proposal: RealtimeMemoryProposal;
  action: "accept" | "reject";
};

export function RealtimeMemoryReview({
  data,
  busy,
  client,
  act,
  reload,
}: {
  data: RealtimeMemoryReviewData;
  busy: boolean;
  client: SettingsClient;
  act(operation: () => Promise<void>): Promise<void>;
  reload(): Promise<void>;
}) {
  const [decision, setDecision] = useState<ReviewDecision | null>(null);
  const proposalsByDigest = useMemo(() => {
    const grouped = new Map<string, RealtimeMemoryProposal[]>();
    data.proposals.forEach((proposal) => {
      const current = grouped.get(proposal.digest_id) ?? [];
      current.push(proposal);
      grouped.set(proposal.digest_id, current);
    });
    return grouped;
  }, [data.proposals]);
  const pendingCount = data.proposals.filter((item) => item.status === "pending").length;
  const savedCount = data.proposals.filter((item) => item.status === "promoted").length;

  return (
    <section className="realtime-memory-review" aria-labelledby="realtime-memory-review-title">
      <header>
        <span>
          <Brain size={17} aria-hidden="true" />
          <strong id="realtime-memory-review-title">Realtime memory</strong>
        </span>
        <small>{savedCount} saved · {pendingCount} to review</small>
      </header>
      {data.diagnostic ? (
        <p className="settings-callout knowledge-diagnostic-error" role="status">
          {data.diagnostic}
        </p>
      ) : null}
      {data.digests.length === 0 ? (
        <p className="settings-empty">No completed Realtime session summaries yet.</p>
      ) : (
        <div className="realtime-digest-list">
          {data.digests.map((digest) => {
            const proposals = proposalsByDigest.get(digest.id) ?? [];
            return (
              <article key={digest.id} className="realtime-digest-card">
                <header>
                  <FileText size={15} aria-hidden="true" />
                  <span>
                    <strong>{digest.subject_title?.trim() || "Realtime session"}</strong>
                    <small>
                      {formatDate(digest.ended_at)} · {formatDuration(digest.duration_seconds)}
                    </small>
                  </span>
                  <span className="realtime-digest-activity">{digest.activity}</span>
                </header>
                <p>{digest.progress_summary}</p>
                {digest.next_goal ? <small>Next: {digest.next_goal}</small> : null}
                {digest.unresolved_issue ? (
                  <small>Open issue: {digest.unresolved_issue}</small>
                ) : null}
                {proposals.length > 0 ? (
                  <div className="realtime-proposal-list">
                    {proposals.map((proposal) => (
                      <RealtimeProposalRow
                        key={proposal.id}
                        proposal={proposal}
                        busy={busy}
                        onDecide={(action) => setDecision({ proposal, action })}
                      />
                    ))}
                  </div>
                ) : (
                  <small className="realtime-memory-none">No durable facts were proposed.</small>
                )}
              </article>
            );
          })}
        </div>
      )}
      <ActionDialog
        open={decision !== null}
        busy={busy}
        destructive={decision?.action === "reject"}
        title={decision?.action === "accept"
          ? "Accept Realtime memory"
          : "Reject Realtime memory"}
        description={decision === null ? "" : decision.action === "accept"
          ? "Save this sourced fact to durable memory. Existing conflicting claims are never replaced silently."
          : "Reject this suggestion without creating a durable memory claim."}
        confirmLabel={decision?.action === "accept" ? "Accept memory" : "Reject suggestion"}
        onCancel={() => setDecision(null)}
        onConfirm={async () => {
          if (decision === null) return;
          const current = decision;
          await act(async () => {
            const action = current.action === "accept"
              ? client.realtimeMemory.proposals.accept
              : client.realtimeMemory.proposals.reject;
            await action({
              proposal_id: current.proposal.id,
              expected_revision: current.proposal.revision,
              user_confirmed: true,
              idempotency_key: [
                "settings:realtime-memory",
                current.action,
                current.proposal.id,
                current.proposal.revision,
              ].join(":"),
            });
            await reload();
          });
          setDecision(null);
        }}
      />
    </section>
  );
}

function RealtimeProposalRow({
  proposal,
  busy,
  onDecide,
}: {
  proposal: RealtimeMemoryProposal;
  busy: boolean;
  onDecide(action: "accept" | "reject"): void;
}) {
  const icon = proposal.status === "promoted"
    ? <CheckCircle2 size={15} aria-hidden="true" />
    : proposal.status === "rejected"
      ? <XCircle size={15} aria-hidden="true" />
      : proposal.sensitivity === "secret"
        ? <ShieldAlert size={15} aria-hidden="true" />
        : <Clock3 size={15} aria-hidden="true" />;
  return (
    <div className="realtime-proposal-row" data-status={proposal.status}>
      {icon}
      <span>
        <strong>{proposal.normalized_text}</strong>
        <small>
          {proposalLabel(proposal)} · {proposal.target_namespace.replaceAll("_", " ")}
        </small>
      </span>
      {proposal.status === "pending" ? (
        <div>
          <button
            type="button"
            className="secondary-command"
            disabled={busy}
            onClick={() => onDecide("reject")}
          >
            Reject
          </button>
          <button
            type="button"
            className="primary-command"
            disabled={busy}
            onClick={() => onDecide("accept")}
          >
            Accept
          </button>
        </div>
      ) : (
        <span className="realtime-proposal-status">{proposal.status === "promoted"
          ? "Saved"
          : "Rejected"}</span>
      )}
    </div>
  );
}

function proposalLabel(proposal: RealtimeMemoryProposal): string {
  if (proposal.status === "promoted" && proposal.policy_decision === "auto_promote") {
    return "Saved automatically from an explicit low-risk statement";
  }
  if (proposal.status === "promoted") return "Accepted by you";
  if (proposal.status === "rejected") return "Rejected";
  return proposal.policy_reason === "existing_claim_conflict"
    ? "Conflicts with an existing memory"
    : "Confirmation required";
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatDuration(seconds: number): string {
  const minutes = Math.max(0, Math.round(seconds / 60));
  if (minutes < 60) return `${minutes} min`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}
