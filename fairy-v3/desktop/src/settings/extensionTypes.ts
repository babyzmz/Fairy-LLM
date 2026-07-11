export interface McpServerDraft {
  serverId: string;
  displayName: string;
  transport: "stdio" | "streamable_http";
  command: string | null;
  arguments: string[];
  endpoint: string | null;
  credentialRef: string | null;
  environmentRefs: Record<string, string>;
}
