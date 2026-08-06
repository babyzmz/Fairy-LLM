import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { TurnTrace } from "../core/client";
import { OPEN_WORKSPACE_SOURCE_EVENT } from "../app/workspaceNavigation";
import { EvidenceSources } from "./EvidenceSources";

afterEach(cleanup);

describe("EvidenceSources", () => {
  it("stays collapsed, opens safe web links, and navigates Workspace files", () => {
    const openLink = vi.fn(async () => undefined);
    const onWorkspaceSource = vi.fn();
    window.addEventListener(OPEN_WORKSPACE_SOURCE_EVENT, onWorkspaceSource);
    const sources: TurnTrace["evidence_sources"] = [
      {
        id: "019fd61f-6be7-7000-8000-000000000001",
        source_kind: "web_document",
        public_label: "Current release notes",
        tool_name: "web.fetch",
        workspace_id: null,
        version_id: null,
        relative_path: null,
        line_start: null,
        line_end: null,
        safe_url: "https://example.com/releases",
        observed_at: "2026-08-06T00:00:00Z",
        expires_at: "2026-08-06T00:30:00Z",
        truncated: false,
      },
      {
        id: "019fd61f-6be7-7000-8000-000000000002",
        source_kind: "project_file",
        public_label: "Current package manifest",
        tool_name: "project.read",
        workspace_id: "019fd61f-6be7-7000-8000-000000000010",
        version_id: "019fd61f-6be7-7000-8000-000000000011",
        relative_path: "package.json",
        line_start: 1,
        line_end: 20,
        safe_url: null,
        observed_at: "2026-08-06T00:00:00Z",
        expires_at: null,
        truncated: false,
      },
    ];
    render(
      <EvidenceSources taskId="task-1" sources={sources} onOpenLink={openLink} />,
    );

    const summary = screen.getByText("Sources", { exact: false });
    expect(summary.closest("details")).not.toHaveAttribute("open");
    fireEvent.click(summary);
    fireEvent.click(screen.getByRole("button", { name: "Current release notes" }));
    expect(openLink).toHaveBeenCalledWith("task-1", "https://example.com/releases");

    fireEvent.click(screen.getByTitle("package.json"));
    expect(onWorkspaceSource).toHaveBeenCalledTimes(1);
    expect((onWorkspaceSource.mock.calls[0]?.[0] as CustomEvent).detail).toEqual({
      path: "package.json",
      workspaceId: "019fd61f-6be7-7000-8000-000000000010",
      versionId: "019fd61f-6be7-7000-8000-000000000011",
      lineStart: 1,
      lineEnd: 20,
    });
    window.removeEventListener(OPEN_WORKSPACE_SOURCE_EVENT, onWorkspaceSource);
  });
});
