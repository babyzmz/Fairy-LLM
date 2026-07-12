import type { McpServer } from "../core/client";

type PermissionProfileValue = "observe" | "standard" | "autonomous";

export function equalOverrides(
  left: Record<string, boolean>,
  right: Record<string, boolean>,
): boolean {
  const leftEntries = Object.entries(left).sort(([a], [b]) => a.localeCompare(b));
  const rightEntries = Object.entries(right).sort(([a], [b]) => a.localeCompare(b));
  return JSON.stringify(leftEntries) === JSON.stringify(rightEntries);
}

export function permissionUpdateKey(
  revision: number,
  profile: PermissionProfileValue,
  overrides: Record<string, boolean>,
): string {
  return hashedKey(`permissions:${revision}`, {
    profile,
    overrides: Object.entries(overrides).sort(([a], [b]) => a.localeCompare(b)),
  });
}

export function requireMcpServer(
  servers: McpServer[] | undefined,
  serverId: string,
): McpServer {
  const server = servers?.find((item) => item.server_id === serverId);
  if (server === undefined) throw new Error("MCP server is unavailable");
  return server;
}

export function extensionUpdateKey(
  operation: string,
  serverId: string,
  revision: number,
  payload: unknown,
): string {
  return hashedKey(`mcp:${serverId}:${operation}:${revision}`, payload);
}

function hashedKey(prefix: string, payload: unknown): string {
  const canonical = JSON.stringify(canonicalValue(payload));
  let hash = 0xcbf29ce484222325n;
  for (const byte of new TextEncoder().encode(canonical)) {
    hash ^= BigInt(byte);
    hash = BigInt.asUintN(64, hash * 0x100000001b3n);
  }
  return `${prefix}:${hash.toString(16).padStart(16, "0")}`;
}

function canonicalValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalValue);
  if (typeof value !== "object" || value === null) return value;
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => [key, canonicalValue(item)]),
  );
}
