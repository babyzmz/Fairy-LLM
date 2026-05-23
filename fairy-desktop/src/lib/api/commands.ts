import { apiRequest } from "./client";

export type CommandKind = "client_action" | "rewrite_message" | "server_action";

export interface CommandSpec {
  name: string;
  aliases: string[];
  description: string;
  usage: string;
  kind: CommandKind;
  rewrite_template?: string | null;
  client_action_type?: string | null;
  server_handler_name?: string | null;
  argument_label?: string | null;
}

export interface CommandListResponse {
  commands: CommandSpec[];
}

export interface CommandExecuteResponse {
  name: string;
  result: Record<string, unknown>;
}

export function listCommands(): Promise<CommandListResponse> {
  return apiRequest<CommandListResponse>("/commands/list");
}

export function executeCommand(name: string, args: Record<string, unknown> = {}): Promise<CommandExecuteResponse> {
  return apiRequest("/commands/execute", {
    method: "POST",
    body: JSON.stringify({ name, args }),
  });
}
