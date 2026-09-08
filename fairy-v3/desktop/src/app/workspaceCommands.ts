import type { AssistantCommandInput, AssistantCommandResult } from "../core/client";
import type { PermissionProfile } from "./workspaceTypes";

interface CommandContext {
  conversationId: string | null;
  turn: { id: string; conversation_id: string; cancellation_revision: number } | null;
}
interface CommandHost {
  context: CommandContext;
  dispatch(input: AssistantCommandInput): Promise<AssistantCommandResult>;
  selectConversation(id: string): Promise<void>;
  setPermission(profile: PermissionProfile): Promise<void>;
  showProject(): void;
  isCurrent?(): boolean;
}

export async function dispatchWorkspaceCommand(text: string, idempotencyKey: string, host: CommandHost): Promise<string | null> {
  const input: AssistantCommandInput = { text, idempotency_key: idempotencyKey, conversation_id: host.context.conversationId };
  // This attaches transport context, not an alternative command interpreter.
  if (text.trim() === "/stop") {
    const turn = host.context.turn;
    if (turn === null || turn.conversation_id !== host.context.conversationId) {
      throw new Error("No active Turn is available in the current conversation");
    }
    input.turn_id = turn.id;
    input.expected_cancellation_revision = turn.cancellation_revision;
  }
  const result = await host.dispatch(input);
  if (host.isCurrent?.() === false) return "Command completed in its original conversation";
  if (result.conversation !== null) await host.selectConversation(result.conversation.id);
  if (result.ui_action?.kind === "show_project") host.showProject();
  if (result.ui_action?.kind === "request_permission" && result.ui_action.profile !== null) {
    await host.setPermission(result.ui_action.profile);
  }
  return result.notice;
}
