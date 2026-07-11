export const SLASH_COMMANDS = [
  "new",
  "project",
  "permission",
  "stop",
  "clear",
  "help",
] as const;

export type SlashCommandName = (typeof SLASH_COMMANDS)[number];

export type ParsedSlashCommand =
  | { name: SlashCommandName; argument: string }
  | { name: "unknown"; argument: string };

export function parseSlashCommand(value: string): ParsedSlashCommand | null {
  const trimmed = value.trimStart();
  if (!trimmed.startsWith("/")) return null;
  const commandText = trimmed.slice(1).trimEnd();
  const separator = commandText.search(/\s/);
  const name = separator < 0 ? commandText : commandText.slice(0, separator);
  const argument = separator < 0 ? "" : commandText.slice(separator).trim();
  if ((SLASH_COMMANDS as readonly string[]).includes(name)) {
    return { name: name as SlashCommandName, argument };
  }
  return { name: "unknown", argument: commandText };
}
