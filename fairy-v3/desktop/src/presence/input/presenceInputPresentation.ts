import type { FairySurface } from "../domain/motionState";
import type { PetInputLayout } from "../host/petHost";
import type {
  PresenceSubmissionFailure,
  PresenceSubmissionUpdate,
} from "../transport/presenceChannel";
import type { PresenceSubmissionCard } from "./PresencePanel";

export interface PresenceSubmissionState {
  id: string;
  text: string;
  phase: PresenceSubmissionCard["phase"];
  failure: PresenceSubmissionFailure | null;
}

export function applySubmissionUpdate(
  current: PresenceSubmissionState | null,
  update: PresenceSubmissionUpdate,
): PresenceSubmissionState | null {
  if (current === null || current.id !== update.submission_id) return current;
  return { ...current, phase: update.status, failure: update.failure };
}

export function toSubmissionCard(
  submission: PresenceSubmissionState | null,
): PresenceSubmissionCard | null {
  if (submission?.phase !== "failed") return null;
  if (submission.failure === "uncertain") {
    if (submission.text === "") return card(submission, "Chat creation is not confirmed", "Check the chat list before trying again", false);
    return card(submission, "Delivery is not confirmed", "Check the chat before sending again, or stop this request", true);
  }
  return {
    ...card(submission, failureTitle(submission.failure), "Your message was not started", false),
    canRetry: submission.text !== "",
  };
}

function card(
  submission: PresenceSubmissionState,
  title: string,
  detail: string,
  canCancel: boolean,
): PresenceSubmissionCard {
  return {
    id: submission.id,
    phase: submission.phase,
    title,
    detail,
    canCancel,
    canRetry: false,
  };
}

function failureTitle(failure: PresenceSubmissionFailure | null): string {
  if (failure === "offline") return "Fairy is offline";
  if (failure === "busy") return "Fairy is already working";
  return "Message could not be sent";
}

export function layoutForSurface(surface: FairySurface): PetInputLayout {
  switch (surface) {
    case "input": return "compact";
    case "options":
    case "submission":
    case "reply":
    case "ambient": return "expanded";
    case "notice":
    case "core": return "core";
  }
}

export function writeInputPresentationDiagnostics(
  commit: { session_id: number; revision: number } | null,
  status: "starting" | "pending" | "committed" | "failed" | "recovered",
): void {
  if (typeof document === "undefined") return;
  const targets = [
    document.documentElement,
    document.querySelector('[data-testid="presence-input-surface"]'),
  ];
  for (const target of targets) {
    if (!(target instanceof HTMLElement)) continue;
    target.dataset.inputPresentationStatus = status;
    target.dataset.inputPresentationSession = String(commit?.session_id ?? 0);
    target.dataset.inputPresentationRevision = String(commit?.revision ?? 0);
  }
}
