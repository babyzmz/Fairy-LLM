import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DocumentContext, MemorySearchHit } from "../core/client";
import { KnowledgeSettings } from "./KnowledgeSettings";

afterEach(cleanup);

describe("KnowledgeSettings", () => {
  it("requires a durable Task before opening", () => {
    render(<KnowledgeSettings {...props()} available={false} />);
    expect(screen.getByRole("button", { name: "Knowledge" })).toBeDisabled();
  });

  it("lists, searches, and explicitly deletes governed documents", async () => {
    const user = userEvent.setup();
    const value = props();
    render(<KnowledgeSettings {...value} />);
    await user.click(screen.getByRole("button", { name: "Knowledge" }));
    expect(await screen.findByText("notes.md")).toBeVisible();
    await user.type(screen.getByLabelText("Search knowledge"), "scope");
    await user.click(screen.getByRole("button", { name: "Search" }));
    await waitFor(() => expect(value.onSearchDocuments).toHaveBeenCalledWith("scope"));
    await user.click(screen.getByRole("button", { name: "Delete notes.md" }));
    expect(value.onDeleteDocument).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(value.onDeleteDocument).toHaveBeenCalledWith(DOCUMENT.document.id));
  });

  it("searches Hermes memory and requires confirmation before forgetting", async () => {
    const user = userEvent.setup();
    const value = props();
    render(<KnowledgeSettings {...value} />);
    await user.click(screen.getByRole("button", { name: "Knowledge" }));
    await user.click(screen.getByRole("tab", { name: "Memory" }));
    await user.type(screen.getByLabelText("Search knowledge"), "preference");
    await user.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByText("Use compact navigation")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Forget memory" }));
    expect(value.onForgetMemory).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Forget" }));
    await waitFor(() => expect(value.onForgetMemory).toHaveBeenCalledWith("claim", MEMORY.document.source_id));
  });
});

const DOCUMENT = { document: { id: "0198f4de-0114-7000-8000-000000000101", filename: "notes.md", media_type: "text/markdown", byte_length: 128 }, revision: {} } as DocumentContext;
const MEMORY = { document: { id: "0198f4de-0114-7000-8000-000000000102", source_kind: "claim", source_id: "0198f4de-0114-7000-8000-000000000103", namespace: "project_canonical", normalized_text: "Use compact navigation" }, lexical_score: 1, exact_match: true } as unknown as MemorySearchHit;

function props() {
  return {
    available: true,
    disabled: false,
    onListDocuments: vi.fn(async () => [DOCUMENT]),
    onSearchDocuments: vi.fn(async () => [{ document: DOCUMENT.document }] as never),
    onDeleteDocument: vi.fn(async () => undefined),
    onSearchMemory: vi.fn(async () => [MEMORY]),
    onForgetMemory: vi.fn(async () => undefined),
  };
}
