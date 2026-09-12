import { invoke, isTauri } from "@tauri-apps/api/core";

interface BindingContext { revision: number; conversation_id: string | null }
interface BindingTransport {
  get(): Promise<BindingContext>;
  bind(revision: number, conversationId: string): Promise<BindingContext>;
}

/** Explicit main-window actions only; constructing this never changes pet state. */
export class PetChatBindingController {
  private pending: string | null | undefined;
  private busy = false;
  private closed = false;
  constructor(private readonly transport: BindingTransport, private readonly onError: (code: string) => void) {}
  select(conversationId: string | null) {
    if (this.closed) return;
    this.pending = conversationId;
    if (!this.busy) void this.drain();
  }
  close() { this.closed = true; this.pending = undefined; }
  private async drain() {
    this.busy = true;
    try {
      let context = await this.transport.get();
      while (!this.closed && this.pending !== undefined) {
        const id = this.pending ?? context.conversation_id;
        this.pending = undefined;
        if (id !== null) context = await this.transport.bind(context.revision, id);
      }
    } catch (error) {
      // Do not fetch a newer host revision and overwrite another window's action.
      this.pending = undefined;
      const code = typeof error === "string" ? error : error instanceof Error ? error.message : "";
      if (!this.closed) this.onError(/^[A-Z][A-Z0-9_]{0,127}$/.test(code) ? code : "PET_CHAT_BINDING_FAILED");
    } finally {
      this.busy = false;
      if (!this.closed && this.pending !== undefined) void this.drain();
    }
  }
}

export function createPetChatBindingController(onError: (code: string) => void): PetChatBindingController | null {
  if (!isTauri()) return null;
  return new PetChatBindingController({
    get: () => invoke("pet_chat_context_get"),
    bind: (expected_revision, conversation_id) => invoke("pet_chat_bind", {
      input: { expected_revision, conversation_id, profile_id: null, model_selection: null },
    }),
  }, onError);
}
