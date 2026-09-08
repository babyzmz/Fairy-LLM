import { observePetChat, type PetChatContext, type PetChatTransport } from "./petChat";
import type { PresenceSubmissionUpdate } from "../transport/presenceChannel";

interface PendingSubmission {
  id: string; revision: number; registered: boolean; settled: boolean;
  cancelRequested: boolean; cancelAccepted: boolean; cancelling: boolean;
  turnId: string | null;
}

/** UI delivery state only. Core owns messages and Rust owns the business connection. */
export class PetChatController {
  private context: PetChatContext | null = null;
  private pending: PendingSubmission | null = null;
  private stop?: () => void;
  private closed = false;
  constructor(
    private readonly transport: PetChatTransport,
    private readonly onContext: (context: PetChatContext) => void,
    private readonly onUpdate: (update: PresenceSubmissionUpdate) => void,
    private readonly onCommand: (id: string, notice: string | null) => void = () => undefined,
  ) {}

  start() {
    this.stop = observePetChat(this.transport, (context) => {
      this.context = context;
      this.onContext(context);
      const pending = this.pending;
      if (pending && context.submission?.id === pending.id) {
        pending.registered = true;
        if (pending.cancelRequested) void this.cancelOwned(pending);
      }
      if (pending?.cancelAccepted && pending.turnId === context.turn?.id && context.turn?.status === "cancelled" && !context.turn.cancellation_pending) {
        this.update(pending.id, "cancelled");
      }
    });
  }

  close() { this.closed = true; this.stop?.(); }

  send(id: string, text: string) {
    if (this.closed) return;
    if (this.context === null) { this.update(id, "failed", "offline"); return; }
    if (this.pending && !this.pending.settled) { this.update(id, "failed", "busy"); return; }
    if (text.trimStart().startsWith("/")) {
      const command = this.transport.command;
      if (!command) { this.update(id, "failed", "unavailable"); return; }
      void command(this.context.revision, id, text).then((result) => {
        if (!this.closed) this.onCommand(id, result.notice);
      }).catch(() => this.update(id, "failed", "uncertain"));
      return;
    }
    const pending: PendingSubmission = { id, revision: this.context.revision, registered: false,
      settled: false, cancelRequested: false, cancelAccepted: false, cancelling: false, turnId: null };
    this.pending = pending;
    void this.transport.submit(pending.revision, id, text).then((result) => {
      pending.turnId = result.turn_id;
      pending.registered = true;
      if (pending.cancelRequested) void this.cancelOwned(pending);
      else this.update(id, "accepted");
    }).catch((error: unknown) => {
      if (pending.cancelAccepted) return;
      if (pending.cancelRequested && pending.cancelling) return;
      this.update(id, "failed", submissionFailure(error));
    }).finally(() => { pending.settled = true; });
  }

  cancel(id: string) {
    if (this.closed) return;
    const pending = this.pending;
    if (pending?.id === id) {
      pending.cancelRequested = true;
      this.update(id, "cancelling");
      if (pending.registered || pending.settled) void this.cancelOwned(pending);
      return;
    }
    const context = this.context;
    if (context === null) { this.update(id, "failed", "offline"); return; }
    const owned = context.submission;
    this.update(id, "cancelling");
    const request = owned ? this.transport.cancelSubmission(owned.revision, owned.id) : this.transport.cancel(context.revision);
    void request.then((result) => {
      this.update(id, result.cancellation_pending ? "cancelling" : "cancelled");
    }).catch(() => this.update(id, "failed", "uncertain"));
  }

  newChat(id: string) {
    if (this.closed) return;
    if (this.context === null) { this.update(id, "failed", "offline"); return; }
    void this.transport.newChat(this.context.revision, id).catch((error: unknown) => {
      this.update(id, "failed", submissionFailure(error));
    });
  }

  private async cancelOwned(pending: PendingSubmission) {
    if (pending.cancelling || pending.cancelAccepted) return;
    pending.cancelling = true;
    try {
      const result = await this.transport.cancelSubmission(pending.revision, pending.id);
      if (!result.accepted) throw new Error("CANCEL_NOT_ACCEPTED");
      pending.cancelAccepted = true;
      pending.turnId = result.turn_id ?? pending.turnId;
      this.update(pending.id, result.cancellation_pending ? "cancelling" : "cancelled");
    } catch {
      this.update(pending.id, "failed", "uncertain");
    } finally { pending.cancelling = false; }
  }

  private update(id: string, status: PresenceSubmissionUpdate["status"], failure: PresenceSubmissionUpdate["failure"] = null) {
    if (!this.closed) this.onUpdate({ submission_id: id, status, failure });
  }
}

function submissionFailure(error: unknown): PresenceSubmissionUpdate["failure"] {
  const code = typeof error === "string" ? error : error instanceof Error ? error.message : "";
  if (code.includes("BUSY") || code.includes("ACTIVE_TURN")) return "busy";
  if (["PET_CHAT_INPUT_INVALID", "PET_CHAT_BINDING_CHANGED", "PET_CHAT_MODEL_BINDING_INVALID",
    "PET_CHAT_SUBMISSION_ID_REUSED", "SCOPE_MISMATCH"].includes(code)) return "unavailable";
  // A transport error does not establish whether Core committed the message.
  return "uncertain";
}
