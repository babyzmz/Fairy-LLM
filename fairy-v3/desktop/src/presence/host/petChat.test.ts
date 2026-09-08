import { describe, expect, it, vi } from "vitest";
import { PresenceProjection, presenceProjectionStateSchema } from "../domain/projection";
import { mergePetChatProjection, observePetChat, type PetChatContext, type PetChatTransport } from "./petChat";

const first = "01989f92-4b80-7000-8000-000000000001";
const second = "01989f92-4b80-7000-8000-000000000002";
function context(revision = 1): PetChatContext {
  return { revision, projection_revision: revision, conversation_id: first,
    connection_available: true, turn: null, reply: null, submission: null };
}

describe("host-owned pet chat presentation", () => {
  it("drops cross-chat payloads and cleans up a subscription established after unmount", async () => {
    let receive!: (value: unknown) => void;
    let finish!: (value: () => void) => void;
    const stop = vi.fn(); const getContext = vi.fn(async () => context()); const updates = vi.fn();
    const close = observePetChat({ getContext, onContext: (listener) => {
      receive = listener; return new Promise((resolve) => { finish = resolve; });
    } }, updates);
    receive({ ...context(), turn: { id: first, conversation_id: second, status: "running", cancellation_revision: 0, cancellation_pending: false } });
    expect(updates).not.toHaveBeenCalled();
    close(); finish(stop); await Promise.resolve();
    expect(stop).toHaveBeenCalledOnce(); expect(getContext).not.toHaveBeenCalled();
  });
  it("rejects late initial reads and old binding events, and closes late subscriptions", async () => {
    let push: ((value: unknown) => void) | undefined;
    let resolveRead!: (value: unknown) => void;
    const stop = vi.fn();
    const transport = {
      onContext: vi.fn(async (listener) => { push = listener; return stop; }),
      getContext: vi.fn(() => new Promise((resolve) => { resolveRead = resolve; })),
    } as Pick<PetChatTransport, "onContext" | "getContext">;
    const received = vi.fn();
    const close = observePetChat(transport, received);
    await Promise.resolve();
    push?.({ ...context(2), conversation_id: second });
    resolveRead(context(1));
    await Promise.resolve();
    push?.(context(1));
    push?.({ ...context(3), conversation_id: first });
    expect(received.mock.calls.map(([value]) => value.conversation_id)).toEqual([second, first]);
    close();
    push?.(context(4));
    expect(received).toHaveBeenCalledTimes(2);
    expect(stop).toHaveBeenCalledOnce();
  });

  it("validates scope and projects a complete Unicode reply within the existing wire bound", () => {
    const state = context();
    state.turn = { id: second, conversation_id: first, status: "completed", cancellation_revision: 0, cancellation_pending: false };
    state.reply = { id: first, text: "🌸".repeat(1200) };
    const projected = mergePetChatProjection(PresenceProjection.initial(), state, 1000);
    expect(projected.reply?.text).toBe("🌸".repeat(600));
    expect(presenceProjectionStateSchema.safeParse(projected).success).toBe(true);
    const switched = mergePetChatProjection(projected, { ...context(2), conversation_id: second }, 1001);
    expect(switched.reply).toBeNull();
    expect(switched.work_state).toBe("idle");
  });

  it("keeps stopping distinct from stopped and preserves Realtime's display priority", () => {
    const state = context();
    state.turn = { id: second, conversation_id: first, status: "cancelled", cancellation_revision: 1, cancellation_pending: true };
    const stopping = mergePetChatProjection(PresenceProjection.initial(), state, 1000);
    expect(stopping.status_text).toBe("Stopping current task");
    expect(stopping.work_state).toBe("tool");
    const realtime = { ...PresenceProjection.initial(), realtime_active: true, status_text: "Fairy is listening", work_state: "ready" as const };
    expect(mergePetChatProjection(realtime, state, 1000)).toBe(realtime);
    state.turn.cancellation_pending = false;
    expect(mergePetChatProjection(PresenceProjection.initial(), state, 1000).work_state).toBe("idle");
  });
});
