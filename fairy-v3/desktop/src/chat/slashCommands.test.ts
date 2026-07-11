import { describe, expect, it } from "vitest";

import { parseSlashCommand } from "./slashCommands";

describe("parseSlashCommand", () => {
  it("parses only exact supported commands at the first non-space character", () => {
    expect(parseSlashCommand("  /new  ")).toEqual({ name: "new", argument: "" });
    expect(parseSlashCommand("/permission autonomous")).toEqual({
      name: "permission",
      argument: "autonomous",
    });
    expect(parseSlashCommand("/project build status")).toEqual({
      name: "project",
      argument: "build status",
    });
    expect(parseSlashCommand("/news")).toEqual({ name: "unknown", argument: "news" });
    expect(parseSlashCommand("/NEW")).toEqual({ name: "unknown", argument: "NEW" });
    expect(parseSlashCommand("please use /new here")).toBeNull();
  });

  it("leaves natural-language weather and news requests as ordinary text", () => {
    expect(parseSlashCommand("What is the weather in Sydney? ")).toBeNull();
    expect(parseSlashCommand("Show me today's AI news")).toBeNull();
    expect(parseSlashCommand("  tell me the /latest news")).toBeNull();
  });
});
