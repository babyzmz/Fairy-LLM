import { expect, it, vi } from "vitest";
import { PetChatBindingController } from "./petChatBinding";

it("performs no startup writes and coalesces rapid explicit selections", async () => {
  let finish!: (value: { revision: number; conversation_id: string }) => void;
  const get = vi.fn(async () => ({ revision: 4, conversation_id: "pet-chat" }));
  const bind = vi.fn((_revision: number, _id: string) => new Promise<{ revision: number; conversation_id: string }>((resolve) => { finish = resolve; }));
  const errors = vi.fn(); const controller = new PetChatBindingController({ get, bind }, errors);
  expect(get).not.toHaveBeenCalled(); expect(bind).not.toHaveBeenCalled();
  controller.select("first");
  await vi.waitFor(() => expect(bind).toHaveBeenCalledWith(4, "first"));
  controller.select("second"); controller.select("third");
  finish({ revision: 5, conversation_id: "first" });
  await vi.waitFor(() => expect(bind).toHaveBeenCalledWith(5, "third"));
  finish({ revision: 6, conversation_id: "third" });
  await Promise.resolve();
  expect(bind).toHaveBeenCalledTimes(2); expect(get).toHaveBeenCalledOnce(); expect(errors).not.toHaveBeenCalled();
});
it("does not retry a competing host revision and model refresh retains the host conversation", async () => {
  const get = vi.fn(async () => ({ revision: 7, conversation_id: "pet-owned" }));
  const bind = vi.fn(async () => { throw new Error("PET_CHAT_BINDING_CHANGED"); });
  const errors = vi.fn(); const controller = new PetChatBindingController({ get, bind }, errors);
  controller.select(null);
  await vi.waitFor(() => expect(errors).toHaveBeenCalledOnce());
  expect(bind).toHaveBeenCalledWith(7, "pet-owned");
  expect(get).toHaveBeenCalledOnce(); expect(bind).toHaveBeenCalledOnce();
});
it("does not dispatch a delayed read after its main WebView unmounts", async () => {
  let resolve!: (value: { revision: number; conversation_id: string }) => void;
  const get = vi.fn(() => new Promise<{ revision: number; conversation_id: string }>((done) => { resolve = done; }));
  const bind = vi.fn(); const controller = new PetChatBindingController({ get, bind }, vi.fn());
  controller.select("first"); controller.close(); resolve({ revision: 1, conversation_id: "second" });
  await Promise.resolve(); await Promise.resolve(); expect(bind).not.toHaveBeenCalled();
});
