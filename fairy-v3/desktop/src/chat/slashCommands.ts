import type { SlashCommandMetadata } from "../core/client";

export type SlashCommandName = SlashCommandMetadata["name"];

export type ParsedSlashCommand =
  | { name: SlashCommandName; argument: string }
  | { name: "unknown" | "unavailable"; command: string; argument: string };

export function parseSlashCommand(
  value: string,
  commands: readonly SlashCommandMetadata[],
): ParsedSlashCommand | null {
  const trimmed = value.trimStart();
  if (!trimmed.startsWith("/")) return null;
  const commandText = trimmed.slice(1).trimEnd();
  const separator = commandText.search(/\s/);
  const name = separator < 0 ? commandText : commandText.slice(0, separator);
  const argument = separator < 0 ? "" : commandText.slice(separator).trim();
  const definition = commands.find((command) => command.name === name);
  if (definition === undefined) return { name: "unknown", command: name, argument };
  if (!definition.available) return { name: "unavailable", command: name, argument };
  return { name: definition.name, argument };
}

export function slashCommandHelp(
  commands: readonly SlashCommandMetadata[],
): string {
  return commands
    .filter((command) => command.available)
    .map(
      (command) =>
        `/${command.name}${command.argument_hint ? ` ${command.argument_hint}` : ""}`,
    )
    .join(" | ");
}
