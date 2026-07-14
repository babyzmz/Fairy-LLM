import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import type { FilePresentationResult } from "../../core/client";
import DataViewer from "./DataViewer";
import DocumentViewer from "./DocumentViewer";

afterEach(cleanup);

describe("document viewers", () => {
  it("renders inert document blocks without interpreting markup", () => {
    render(
      <DocumentViewer
        presentation={presentation({
          kind: "document",
          blocks: [{ kind: "paragraph", text: "<img src=x onerror=alert(1)>" }],
        })}
      />,
    );

    expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeVisible();
    expect(document.querySelector("img")).toBeNull();
  });

  it("navigates workbook sheets", async () => {
    const user = userEvent.setup();
    render(
      <DocumentViewer
        presentation={presentation({
          kind: "workbook",
          sheets: [
            { name: "Summary", rows: [["Revenue", 42]] },
            { name: "Costs", rows: [["Hosting", 7]] },
          ],
        })}
      />,
    );
    expect(screen.getByText("Revenue")).toBeVisible();
    await user.click(screen.getByRole("tab", { name: "Costs" }));
    expect(screen.getByText("Hosting")).toBeVisible();
  });

  it("renders archive, ebook, and mail models without active content", async () => {
    const user = userEvent.setup();
    const { rerender } = render(
      <DocumentViewer presentation={presentation({
        kind: "archive",
        format: "zip",
        entries: [{ path: "docs/readme.txt", size: 5, kind: "file" }],
      })} />,
    );
    expect(screen.getByText("docs/readme.txt")).toBeVisible();

    rerender(<DocumentViewer presentation={presentation({
      kind: "ebook",
      chapters: [
        { title: "One", text: "First" },
        { title: "Two", text: "Second" },
      ],
    })} />);
    await user.click(screen.getByRole("button", { name: /Two/ }));
    expect(screen.getByText("Second")).toBeVisible();

    rerender(<DocumentViewer presentation={presentation({
      kind: "mail",
      headers: { subject: "Review", from: "sender@example.test" },
      body: "<img src=x onerror=alert(1)>",
      attachments: [{ filename: "note.txt", size: 10 }],
    })} />);
    expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeVisible();
    expect(screen.getByText("Remote content blocked")).toBeVisible();
    expect(document.querySelector("img")).toBeNull();
  });

  it("parses quoted CSV and reports malformed JSON safely", () => {
    const { rerender } = render(
      <DataViewer path="report.csv" text={'name,note\nFairy,"hello, world"'} />,
    );
    expect(screen.getByText("hello, world")).toBeVisible();
    rerender(<DataViewer path="report.json" text="{" />);
    expect(screen.getByRole("alert")).toBeVisible();
  });
});

function presentation(
  payload: Record<string, unknown>,
): FilePresentationResult {
  return {
    job: {
      id: "0198f4de-0114-7000-8000-000000000001",
      workspace_id: "0198f4de-0114-7000-8000-000000000002",
      version_id: "0198f4de-0114-7000-8000-000000000003",
      file_set_id: "0198f4de-0114-7000-8000-000000000004",
      source_path: "document.docx",
      source_hash: "a".repeat(64),
      cache_key: "b".repeat(64),
      requested_mode: "auto",
      renderer_pack_id: null,
      renderer_pack_version: null,
      status: "ready",
      progress: 100,
      error_code: null,
      public_summary: "Ready",
      created_at: "2026-07-14T00:00:00Z",
      updated_at: "2026-07-14T00:00:00Z",
    },
    presentation: {
      id: "0198f4de-0114-7000-8000-000000000005",
      workspace_id: "0198f4de-0114-7000-8000-000000000002",
      version_id: "0198f4de-0114-7000-8000-000000000003",
      file_set_id: "0198f4de-0114-7000-8000-000000000004",
      source_path: "document.docx",
      source_hash: "a".repeat(64),
      renderer: "builtin.ooxml",
      fidelity: "content_only",
      status: "ready",
      capabilities: ["search", "select"],
      assets: [
        {
          id: "0198f4de-0114-7000-8000-000000000006",
          role: "document-model",
          media_type: "application/vnd.fairy.document+json",
          content_hash: "c".repeat(64),
          byte_length: 10,
          storage_key: "embedded:test",
          metadata: { payload },
        },
      ],
      created_at: "2026-07-14T00:00:00Z",
    },
  };
}
