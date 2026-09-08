import { expect, it, vi } from "vitest";
import { dispatchWorkspaceCommand } from "./workspaceCommands";
import type { AssistantCommandResult } from "../core/client";

const result: AssistantCommandResult = { command: "help", notice: "Core help", conversation: null, turn: null, ui_action: null };
function harness() {
  return { dispatch: vi.fn(async () => result), selectConversation: vi.fn(async () => undefined),
    setPermission: vi.fn(async () => undefined), showProject: vi.fn(),
    context: { conversationId: "chat-a", turn: { id: "turn-a", conversation_id: "chat-a", cancellation_revision: 3 } } };
}
it("passes a fenced stop to Core but never adds a Turn target to new-chat commands", async () => {
  const h = harness();
  await dispatchWorkspaceCommand("/stop", "once", h);
  expect(h.dispatch).toHaveBeenLastCalledWith({ text: "/stop", idempotency_key: "once", conversation_id: "chat-a", turn_id: "turn-a", expected_cancellation_revision: 3 });
  await dispatchWorkspaceCommand("/new", "new-once", h);
  expect(h.dispatch).toHaveBeenLastCalledWith({ text: "/new", idempotency_key: "new-once", conversation_id: "chat-a" });
});
it("does not execute frontend side effects from command text or a failed dispatcher", async () => {
  const h = harness();
  expect(await dispatchWorkspaceCommand("/permission autonomous", "one", h)).toBe("Core help");
  expect(h.setPermission).not.toHaveBeenCalled();
  h.dispatch.mockRejectedValueOnce(new Error("UNAVAILABLE"));
  await expect(dispatchWorkspaceCommand("/new", "two", h)).rejects.toThrow("UNAVAILABLE");
  expect(h.selectConversation).not.toHaveBeenCalled(); expect(h.showProject).not.toHaveBeenCalled();
});
it("uses only typed UI results and retains the existing permission host path", async () => {
  const h = harness();
  h.dispatch.mockResolvedValueOnce({ ...result, command: "permission", ui_action: { kind: "request_permission", profile: "observe" } });
  await dispatchWorkspaceCommand("/permission observe", "one", h);
  expect(h.setPermission).toHaveBeenCalledWith("observe");
  h.dispatch.mockResolvedValueOnce({ ...result, command: "project", ui_action: { kind: "show_project", profile: null } });
  await dispatchWorkspaceCommand("/project", "two", h);
  expect(h.showProject).toHaveBeenCalledOnce();
});
it("rejects a Turn from another conversation before sending stop", async () => {
  const h = harness(); h.context.turn.conversation_id = "chat-b";
  await expect(dispatchWorkspaceCommand("/stop", "one", h)).rejects.toThrow("current conversation");
  expect(h.dispatch).not.toHaveBeenCalled();
});

it("preserves completed Core facts but does not navigate after the user changed scope", async () => {
  const h = harness();
  h.dispatch.mockResolvedValueOnce({ ...result, command: "permission", ui_action: { kind: "request_permission", profile: "autonomous" } });
  expect(await dispatchWorkspaceCommand("/permission autonomous", "one", { ...h, isCurrent: () => false }))
    .toBe("Command completed in its original conversation");
  expect(h.setPermission).not.toHaveBeenCalled(); expect(h.showProject).not.toHaveBeenCalled();
});
