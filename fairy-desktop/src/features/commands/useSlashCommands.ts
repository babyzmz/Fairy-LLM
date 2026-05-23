import { useEffect, useMemo, useState } from "react";

import { listCommands, type CommandSpec } from "../../lib/api/commands";

export interface ParsedCommand {
  spec: CommandSpec;
  args: string;
  raw: string;
}

export function useSlashCommands(): { commands: CommandSpec[]; loaded: boolean; error: string } {
  const [commands, setCommands] = useState<CommandSpec[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let disposed = false;
    void (async () => {
      try {
        const response = await listCommands();
        if (!disposed) {
          setCommands(response.commands);
          setLoaded(true);
        }
      } catch (err) {
        if (!disposed) {
          setError(err instanceof Error ? err.message : String(err));
          setLoaded(true);
        }
      }
    })();
    return () => {
      disposed = true;
    };
  }, []);

  return { commands, loaded, error };
}

export function matchPrefix(commands: CommandSpec[], input: string): CommandSpec[] {
  const trimmed = input.trimStart();
  if (!trimmed.startsWith("/")) {
    return [];
  }
  const firstToken = trimmed.split(/\s+/, 1)[0] ?? "";
  if (firstToken.length === 1) {
    return commands;
  }
  const lower = firstToken.toLowerCase();
  return commands.filter((spec) => {
    if (spec.name.toLowerCase().startsWith(lower)) return true;
    return spec.aliases.some((alias) => alias.toLowerCase().startsWith(lower));
  });
}

export function parseCommandInput(commands: CommandSpec[], input: string): ParsedCommand | null {
  const trimmed = input.trim();
  if (!trimmed.startsWith("/")) {
    return null;
  }
  const firstSpace = trimmed.search(/\s/);
  const name = firstSpace === -1 ? trimmed : trimmed.slice(0, firstSpace);
  const args = firstSpace === -1 ? "" : trimmed.slice(firstSpace + 1).trim();
  const lower = name.toLowerCase();
  const spec = commands.find((candidate) => {
    if (candidate.name.toLowerCase() === lower) return true;
    return candidate.aliases.some((alias) => alias.toLowerCase() === lower);
  });
  if (!spec) {
    return null;
  }
  return { spec, args, raw: trimmed };
}

export function applyRewrite(spec: CommandSpec, args: string): string {
  if (!spec.rewrite_template) {
    return args;
  }
  return spec.rewrite_template.replace("{arg}", args);
}
