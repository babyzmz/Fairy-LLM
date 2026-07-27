import type { CoreClient } from "../core/client";

export interface CompanionMemoryNotice {
  sessionId: string;
  digestId: string;
  savedCount: number;
  pendingCount: number;
}

export async function loadCompanionMemoryNotice(
  client: CoreClient["realtime"],
  sessionId: string,
): Promise<CompanionMemoryNotice | null> {
  const page = await client.digests.list({ session_id: sessionId, limit: 1 });
  const digest = page.items.find((item) => item.session_id === sessionId);
  if (digest === undefined) return null;
  return noticeForDigest(client, sessionId, digest.id);
}

export async function createCompanionMemoryNotice(
  client: CoreClient["realtime"],
  input: {
    sessionId: string;
    activity: "auto" | "game" | "focus";
    subjectTitle: string | null;
  },
): Promise<CompanionMemoryNotice> {
  const digest = await client.digests.create({
    session_id: input.sessionId,
    request_id: `desktop:realtime-digest:${input.sessionId}`,
    activity: input.activity,
    subject_title: input.subjectTitle,
  });
  if (digest.session_id !== input.sessionId) {
    throw new Error("Realtime digest session mismatch");
  }
  return noticeForDigest(client, input.sessionId, digest.id);
}

async function noticeForDigest(
  client: CoreClient["realtime"],
  sessionId: string,
  digestId: string,
): Promise<CompanionMemoryNotice> {
  const page = await client.memoryProposals.list({
    session_id: sessionId,
    digest_id: digestId,
    pending_only: false,
    limit: 100,
  });
  const proposals = page.items.filter((item) =>
    item.session_id === sessionId && item.digest_id === digestId);
  return {
    sessionId,
    digestId,
    savedCount: proposals.filter((item) => item.status === "promoted").length,
    pendingCount: proposals.filter((item) => item.status === "pending").length,
  };
}
