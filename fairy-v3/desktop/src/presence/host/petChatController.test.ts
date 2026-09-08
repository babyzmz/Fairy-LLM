import { expect, it, vi } from "vitest";
import { PetChatController } from "./petChatController";
import type { PetChatContext, PetChatTransport } from "./petChat";

function deferred<T>() { let resolve!: (value: T) => void; let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject }; }
const initial: PetChatContext = { revision: 0, projection_revision: 0, conversation_id: null,
  submission: null, turn: null, reply: null, connection_available: false };
function harness() {
  let receive!: (value: unknown) => void;
  const pending = deferred<{ binding_revision: number; turn_id: string }>();
  const transport: PetChatTransport = {
    getContext: vi.fn(async () => initial), onContext: vi.fn(async (listener) => { receive = listener; return vi.fn(); }),
    submit: vi.fn(() => pending.promise), cancelSubmission: vi.fn(async () => ({ accepted: true, cancellation_pending: null })),
    cancel: vi.fn(async () => ({ accepted: true, cancellation_pending: false })), newChat: vi.fn(async () => initial),
  };
  const updates = vi.fn(); const contexts = vi.fn();
  const controller = new PetChatController(transport, contexts, updates);
  controller.start();
  return { controller, transport, updates, contexts, pending, emit: (value: PetChatContext) => receive(value) };
}
it("stops after host registration without waiting for the submit RPC, and ignores its late failure", async () => {
  const h = harness(); await vi.waitFor(() => expect(h.contexts).toHaveBeenCalled());
  h.controller.send("once", "Hello"); h.controller.cancel("once");
  expect(h.transport.cancelSubmission).not.toHaveBeenCalled();
  h.emit({ ...initial, projection_revision: 1, submission: { id: "once", revision: 0 } });
  await vi.waitFor(() => expect(h.transport.cancelSubmission).toHaveBeenCalledWith(0, "once"));
  h.pending.reject(new Error("PROVIDER_CANCELLED"));
  await Promise.resolve(); await Promise.resolve();
  expect(h.updates.mock.calls.at(-1)?.[0].status).toBe("cancelled");
  expect(h.transport.submit).toHaveBeenCalledOnce(); h.controller.close();
});
it("marks a timed-out submission uncertain and never creates an automatic retry", async () => {
  const h = harness(); await vi.waitFor(() => expect(h.contexts).toHaveBeenCalled());
  h.controller.send("uncertain", "Hello");
  h.pending.reject(new Error("CORE_RPC_TIMEOUT"));
  await vi.waitFor(() => expect(h.updates.mock.calls.at(-1)?.[0].failure).toBe("uncertain"));
  expect(h.transport.submit).toHaveBeenCalledOnce(); h.controller.close();
});
it("recovers cancellation identity from the host after input WebView reload", async () => {
  const h = harness(); await vi.waitFor(() => expect(h.contexts).toHaveBeenCalled());
  h.emit({ ...initial, revision: 4, projection_revision: 8, submission: { id: "before-reload", revision: 2 } });
  h.controller.cancel("ui-action");
  await vi.waitFor(() => expect(h.transport.cancelSubmission).toHaveBeenCalledWith(2, "before-reload"));
  expect(h.transport.cancel).not.toHaveBeenCalled(); h.controller.close();
});
it("does not deliver asynchronous results into a closed input surface", async () => {
  const h = harness(); await vi.waitFor(() => expect(h.contexts).toHaveBeenCalled());
  h.controller.send("closed", "Hello"); h.controller.close(); h.updates.mockClear();
  h.pending.resolve({ binding_revision: 1, turn_id: "turn" });
  await Promise.resolve(); await Promise.resolve();
  expect(h.updates).not.toHaveBeenCalled();
});

it("routes a bare slash command to Core without creating a model message", async () => {
  const h = harness(); await vi.waitFor(() => expect(h.contexts).toHaveBeenCalled());
  h.transport.command = vi.fn(async () => ({ notice: "Core help" }));
  h.controller.send("help", "/help");
  await vi.waitFor(() => expect(h.transport.command).toHaveBeenCalledWith(0, "help", "/help"));
  expect(h.transport.submit).not.toHaveBeenCalled(); h.controller.close();
});
