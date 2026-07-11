import { describe, expect, it } from "vitest";

import { parseSlashCommand, slashCommandHelp } from "./slashCommands";

const commands = [
  {
    name: "new",
    description: "Start a durable conversation",
    argument_hint: null,
    required_operation: "workspace.create_scratch",
    available: true,
  },
  {
    name: "permission",
    description: "Change the execution profile",
    argument_hint: "<observe|standard|autonomous>",
    required_operation: null,
    available: true,
  },
  {
    name: "stop",
    description: "Stop the active response",
    argument_hint: null,
    required_operation: "model.generate",
    available: false,
  },
] as const;

describe("parseSlashCommand", () => {
  it("parses only exact supported commands at the first non-space character", () => {
    expect(parseSlashCommand("  /new  ", commands)).toEqual({
      name: "new",
      argument: "",
    });
    expect(parseSlashCommand("/permission autonomous", commands)).toEqual({
      name: "permission",
      argument: "autonomous",
    });
    expect(parseSlashCommand("/stop", commands)).toEqual({
      name: "unavailable",
      command: "stop",
      argument: "",
    });
    expect(parseSlashCommand("/news", commands)).toEqual({
      name: "unknown",
      command: "news",
      argument: "",
    });
    expect(parseSlashCommand("/NEW", commands)).toEqual({
      name: "unknown",
      command: "NEW",
      argument: "",
    });
    expect(parseSlashCommand("please use /new here", commands)).toBeNull();
  });

  it("leaves natural-language weather and news requests as ordinary text", () => {
    expect(parseSlashCommand("What is the weather in Sydney? ", commands)).toBeNull();
    expect(parseSlashCommand("Show me today's AI news", commands)).toBeNull();
    expect(parseSlashCommand("  tell me the /latest news", commands)).toBeNull();
  });

  it("builds help from available Core metadata only", () => {
    expect(slashCommandHelp(commands)).toBe(
      "/new | /permission <observe|standard|autonomous>",
    );
  });
});
